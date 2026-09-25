"""核心单元测试：视觉相似指纹 / 分组 / 人工决策（② 疑似重复照片）。

全部使用 temp 临时照片与临时索引文件，不触碰生产照片库。
"""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from core.visual_duplicates import (
    VisualDuplicateIndex,
    compute_fingerprint,
    combined_sim,
    color_sim,
    dhash_sim,
)


def make_scene(path, brightness=1.0, seed=7, w=320, h=240, style="scene", roll=0):
    """确定性场景图：渐变底 + 彩色图形 + 纹理块 + 轻微噪声。

    brightness: 全局亮度变化（模拟曝光不同）
    roll: 水平循环平移像素（模拟轻微位移/构图差异）
    """
    rng = np.random.default_rng(seed)
    base = np.zeros((h, w, 3), dtype=np.float32)
    for y in range(h):
        t = y / h
        col = (0.35 + 0.45 * t, 0.45 + 0.30 * (1 - t), 0.75)
        base[y, :] = np.array(col, dtype=np.float32)
    yy, xx = np.mgrid[0:h, 0:w]
    if style == "scene":
        c1 = (xx - w * 0.30) ** 2 + (yy - h * 0.45) ** 2 < (h * 0.22) ** 2
        base[c1] = (0.95, 0.55, 0.25)
        rect = (xx > w * 0.55) & (xx < w * 0.82) & (yy > h * 0.25) & (yy < h * 0.70)
        base[rect] = (0.20, 0.75, 0.55)
    else:
        c1 = (xx - w * 0.70) ** 2 + (yy - h * 0.30) ** 2 < (h * 0.35) ** 2
        base[c1] = (0.85, 0.20, 0.65)
        base[(yy > h * 0.55)] = (0.15, 0.25, 0.10)
    # 纹理块（可复现，真实照片结构感，避免纯渐变让 dHash 失真）；
    # 不同风格用不同种子（场景差异明显，避免纹理雷同）
    trng = np.random.default_rng(seed + (1000 if style != "scene" else 0))
    for _ in range(24):
        x0, x1 = sorted(trng.integers(0, w, 2))
        y0, y1 = sorted(trng.integers(0, h, 2))
        shade = float(trng.uniform(0.10, 0.85))
        base[y0:y1, x0:x1] = (shade, float(min(1.0, shade * 0.8 + 0.2)), max(0.0, 1.0 - shade * 0.5))
    if roll:
        base = np.roll(base, shift=int(roll), axis=1)
    noise = rng.normal(0, 0.02, base.shape)
    pix = np.clip((base + noise) * brightness, 0.0, 1.0)
    Image.fromarray((pix * 255.0).astype(np.uint8)).save(path, quality=88)
    return path


class VisualFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.base = make_scene(str(self.dir / "base.jpg"))
        self.bright = make_scene(str(self.dir / "bright.jpg"), brightness=1.15)
        self.shift = make_scene(str(self.dir / "shift.jpg"), roll=4)
        self.other = make_scene(str(self.dir / "other.jpg"), style="other")
        self.copy_path = str(self.dir / "copy_base.jpg")
        import shutil
        shutil.copyfile(self.base, self.copy_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fingerprint_fields_and_determinism(self):
        fp = compute_fingerprint(self.base)
        self.assertIsNotNone(fp)
        self.assertLessEqual(fp["dhash"].bit_length(), 64)
        self.assertEqual(len(fp["hist"]), 48)
        self.assertGreater(sum(fp["hist"]), 0.99)
        self.assertEqual(len(fp["md5"]), 32)
        self.assertIn("sharpness", fp["tech"])
        # 可复现
        fp2 = compute_fingerprint(self.base)
        self.assertEqual(fp["dhash"], fp2["dhash"])
        self.assertEqual(fp["hist"], fp2["hist"])

    def test_similarity_math(self):
        # 相同指纹 → 1 / 曝光变体高（dHash 强）/ 完全不同颜色 → 低
        fa = compute_fingerprint(self.base)
        fb = compute_fingerprint(self.bright)
        fo = compute_fingerprint(self.other)
        self.assertGreaterEqual(dhash_sim(fa["dhash"], fa["dhash"]), 1.0)
        self.assertGreaterEqual(color_sim(fa["hist"], fa["hist"]), 1.0)
        self.assertGreater(dhash_sim(fa["dhash"], fb["dhash"]), 0.9)
        self.assertGreater(
            combined_sim(fa["dhash"], fa["hist"], fb["dhash"], fb["hist"],
                         fa["gray"], fb["gray"]), 0.8)
        self.assertLess(
            combined_sim(fa["dhash"], fa["hist"], fo["dhash"], fo["hist"],
                         fa["gray"], fo["gray"]), 0.8)

    def test_groups_find_burst_and_ignore_md5_copies(self):
        idx = VisualDuplicateIndex(photos_dir=str(self.dir),
                                   index_path=str(self.dir / "vis.json"))
        idx.compute_all()
        groups = idx.groups()
        # base/bright/shift/其他 → 前 3 张一组；other 排除；copy_base（MD5 相同）不进视觉组
        g = max(groups, key=lambda g: g["score"])
        names = {ph["name"] for ph in g["photos"]}
        self.assertIn("base.jpg", names)
        self.assertIn("bright.jpg", names)
        self.assertIn("shift.jpg", names)
        self.assertNotIn("other.jpg", names)
        self.assertNotIn("copy_base.jpg", names)
        print("sim:", g["score"], g["reason"], names)
        self.assertGreaterEqual(g["score"], 0.9)
        self.assertIn(g["reason"], ("连拍/构图相似", "曝光差异"))

    def test_decisions_persist_across_reload(self):
        idx = VisualDuplicateIndex(photos_dir=str(self.dir),
                                   index_path=str(self.dir / "vis.json"))
        idx.compute_all()
        groups = idx.groups()
        g = groups[0]
        phs = g["photos"]
        # 保留第一张
        idx.resolve(phs[0]["path"], [p["path"] for p in phs[1:]])
        self.assertTrue(idx.is_resolved(phs[0]["path"]))
        self.assertTrue(idx.candidate_mark(phs[1]["path"]))
        self.assertEqual(len(idx.pending_cleanup()), len(phs) - 1)
        # 忽略另一组对（组内前两张）
        if len(groups) > 1:
            g2 = groups[1]
            idx.ignore_group(g2["photos"][0]["path"], g2["photos"][1]["path"])
        # 重新加载（模拟重启）：决策仍在
        idx2 = VisualDuplicateIndex(photos_dir=str(self.dir),
                                    index_path=str(self.dir / "vis.json"))
        self.assertTrue(idx2.is_resolved(phs[0]["path"]))
        self.assertTrue(idx2.candidate_mark(phs[1]["path"]))
        self.assertEqual(len(idx2.pending_cleanup()), len(phs) - 1)
        if len(groups) > 1:
            g2 = groups[1]
            self.assertTrue(
                idx2.is_ignored(g2["photos"][0]["path"], g2["photos"][1]["path"]))
        # 忽略后组不再推荐（若被忽略的是 groups[0] 则验证其消失）
        idx2.ignore_group(phs[0]["path"], phs[1]["path"])
        regroups = idx2.groups()
        for g in regroups:
            names = {p["name"] for p in g["photos"]}
            self.assertFalse(
                {"base.jpg", "bright.jpg"} <= names, "被忽略的对不应再推荐")

    def test_search_similar(self):
        """③ 相似照片搜索：给定一张 → 库内最相似 N 张（排除自身/可按阈值）。"""
        idx = VisualDuplicateIndex(photos_dir=str(self.dir),
                                   index_path=str(self.dir / "vis.json"))
        idx.compute_all()
        res = idx.search_similar(self.base, top_k=5, min_sim=0.5)
        names = [r["name"] for r in res]
        self.assertNotIn("base.jpg", names, "查询照片自身不参与结果")
        self.assertIn("bright.jpg", names, "曝光变体应命中")
        self.assertIn("shift.jpg", names, "平移变体应命中")
        self.assertEqual(res[0]["score"], max(r["score"] for r in res))
        top3 = names[:3]
        self.assertNotIn("other.jpg", top3, "不同场景不应排进前三")
        # 高阈值只留强命中（copy 完全一致 1.0 / shift ≈0.94）
        strict = idx.search_similar(self.base, top_k=5, min_sim=0.93)
        self.assertEqual(len(strict), 2)
        # 未入索引的查询照片：现场计算指纹也能搜
        newp = make_scene(str(self.dir / "query_outside.jpg"), brightness=1.05)
        res2 = idx.search_similar(newp, top_k=3, min_sim=0.5)
        self.assertGreaterEqual(len(res2), 1)
        self.assertIn("base.jpg", [r["name"] for r in res2])

    def test_stale_and_corrupt(self):
        idx = VisualDuplicateIndex(photos_dir=str(self.dir),
                                   index_path=str(self.dir / "vis.json"))
        idx.compute_all()
        self.assertEqual(idx.stale_files(), [])
        # 新增文件
        newp = make_scene(str(self.dir / "new.jpg"), seed=99)
        self.assertEqual(idx.stale_files(), [newp])
        # 损坏文件
        bad = self.dir / "bad.jpg"
        bad.write_bytes(b"not an image at all" * 100)
        idx.compute_all()
        self.assertEqual(idx.stale_files(), [])   # 损坏文件不再重算（fp None 但记录？）
        # 损坏文件不炸、不进入候选
        groups = idx.groups()
        for g in groups:
            self.assertNotIn("bad.jpg", [p["name"] for p in g["photos"]])
        # 索引文件可读
        with open(self.dir / "vis.json", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["version"], 1)


if __name__ == "__main__":
    unittest.main()
