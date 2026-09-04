"""智能搜索第 2 层 · 第一阶段测试：视觉 Embedding + FAISS 基础设施。

覆盖（对应阶段验收 16 项）：
1 OpenCLIP 加载 / 2 设备选择(GPU可用走GPU, 否则CPU) / 3 CPU 运行 /
4 单图 embedding / 5 维度 / 6 归一化 / 7 FAISS 索引建立 / 8 加照片 /
9 以图搜图 / 10 相似度排序 / 11 已索引不重算 / 12 增量 / 13 MD5 去重 /
14~16 不碰 Fursee/Face/角色系统（源码级断言 + 复用既有回归）。

模型使用本地已缓存权重的 open_clip（HF_HUB_OFFLINE=1 离线加载）；
若环境不可用则跳过模型类用例（基础设施逻辑测试仍执行）。
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from core.visual_search import (
    ClipImageEncoder,
    VisualSearchIndex,
    VisualSearchModelMismatch,
    resolve_device,
)
from tests.test_visual_duplicates import make_scene


class VisualSearchInfraTests(unittest.TestCase):
    """模型 + FAISS 索引 + 搜索 + 增量（temp 隔离，不碰生产数据）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.photos = cls.dir / "photos"
        cls.photos.mkdir()
        make_scene(str(cls.photos / "base.jpg"))
        make_scene(str(cls.photos / "bright.jpg"), brightness=1.15)
        make_scene(str(cls.photos / "shift.jpg"), roll=4)
        make_scene(str(cls.photos / "other.jpg"), style="other")
        shutil.copyfile(str(cls.photos / "base.jpg"),
                        str(cls.photos / "copy_base.jpg"))
        cls.cache_dir = cls.dir / "vs_cache"

        cls.skip_model = False
        cls.load_error = ""
        try:
            cls.encoder = ClipImageEncoder(device="cpu")
            cls.model_info = cls.encoder.model_info()   # 强制加载（第1/3项）
            # 构建共享索引（第7/8项）
            cls.encoder.encode_batch([
                str(cls.photos / "base.jpg"),
                str(cls.photos / "bright.jpg"),
                str(cls.photos / "shift.jpg"),
                str(cls.photos / "other.jpg"),
            ])
        except Exception as e:  # noqa: BLE001
            cls.skip_model = True
            cls.load_error = str(e)
            cls.encoder = None

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "encoder", None):
            try:
                cls.encoder.close()
            except Exception:
                pass
        cls.tmp.cleanup()

    def _maybe_skip_model(self):
        if self.skip_model:
            self.skipTest(f"OpenCLIP 当前环境不可用: {self.load_error}")

    def _build_index(self, cache_dir, paths):
        idx = VisualSearchIndex(cache_dir=str(cache_dir))
        stats = idx.add_images([str(p) for p in paths], self.encoder)
        return idx, stats

    # ── 1/2/3/4/5/6：模型加载、设备、embedding ──
    def test_model_loads_and_info(self):
        self._maybe_skip_model()
        info = self.model_info
        self.assertEqual(info["model_name"], "ViT-L-14")
        self.assertEqual(info["pretrained"], "datacomp_xl_s13b_b90k")
        self.assertIn("embedding_dimension", info)
        self.assertGreaterEqual(info["embedding_dimension"], 128)
        self.assertEqual(info["device"], "cpu")

    def test_device_resolution(self):
        # GPU 可用 → cuda；不可用 → cpu；env 强制生效
        with mock.patch("core.visual_search.embedding._cuda_available",
                        return_value=True):
            self.assertEqual(resolve_device(None), "cuda")
        with mock.patch("core.visual_search.embedding._cuda_available",
                        return_value=False):
            self.assertEqual(resolve_device(None), "cpu")
        with mock.patch.dict(os.environ, {"VISUAL_SEARCH_DEVICE": "cuda"}):
            self.assertEqual(resolve_device(None), "cuda")
        with mock.patch.dict(os.environ, {"VISUAL_SEARCH_DEVICE": "cpu"}):
            self.assertEqual(resolve_device(None), "cpu")

    def test_embedding_dim_and_normalized(self):
        self._maybe_skip_model()
        vec = self.encoder.encode(str(self.photos / "base.jpg"))
        self.assertEqual(vec.shape, (self.model_info["embedding_dimension"],))
        self.assertEqual(vec.dtype, np.float32)
        self.assertAlmostEqual(float(np.linalg.norm(vec)), 1.0, places=4)

    # ── 7/8：FAISS 索引建立与加照片 ──
    def test_faiss_index_build_and_persist(self):
        self._maybe_skip_model()
        cache = self.dir / "test_build"
        idx, stats = self._build_index(
            cache, [self.photos / "base.jpg", self.photos / "bright.jpg"])
        self.assertEqual(stats["new"], 2)
        self.assertEqual(idx.count(), 2)
        self.assertTrue(Path(idx.index_path).is_file())
        self.assertTrue(Path(idx.metadata_path).is_file())
        # 重新打开（磁盘加载）
        idx2 = VisualSearchIndex(cache_dir=str(cache))
        self.assertEqual(idx2.count(), 2)
        self.assertEqual(idx2.model_info()["model_name"], "ViT-L-14")

    # ── 9/10：以图搜图 + 排序 ──
    def test_search_by_image_sorted(self):
        self._maybe_skip_model()
        cache = self.dir / "test_search"
        paths = [self.photos / n for n in
                 ("base.jpg", "bright.jpg", "shift.jpg", "other.jpg")]
        idx, _ = self._build_index(cache, paths)
        res = idx.search_by_image(str(self.photos / "base.jpg"),
                                  self.encoder, top_k=5)
        self.assertGreaterEqual(len(res), 3)
        sims = [r["similarity"] for r in res]
        self.assertEqual(sims, sorted(sims, reverse=True), "按相似度降序")
        names = [os.path.basename(r["path"]) for r in res]
        self.assertNotIn("base.jpg", names, "排除查询照片自身")
        self.assertIn("bright.jpg", names)
        self.assertIn("shift.jpg", names)
        # 变体（曝光/平移）必须排在完全不同场景之前，且显著更相似
        self.assertIn(names[0], ("bright.jpg", "shift.jpg"),
                      "最相似结果必须是真实变体")
        variant_sim = {n: s for n, s in zip(names, sims)
                       if n in ("bright.jpg", "shift.jpg")}
        other_sim = sims[names.index("other.jpg")] if "other.jpg" in names else 0.0
        self.assertTrue(all(s > other_sim for s in variant_sim.values()),
                        "真实变体相似度应高于无关场景")
        self.assertIn("photo_id", res[0])
        self.assertIn("path", res[0])
        self.assertIn("similarity", res[0])

    # ── 11/12/13：增量 / 不重算 / MD5 去重 ──
    def test_incremental_no_recompute_and_md5_dedup(self):
        self._maybe_skip_model()
        cache = self.dir / "test_incr"
        idx, stats = self._build_index(
            cache, [self.photos / "base.jpg",
                    self.photos / "copy_base.jpg",   # MD5 相同 → 不重复编码
                    self.photos / "bright.jpg"])
        self.assertEqual(stats["new"], 2, "copy_base 与 base MD5 相同，只编码 1 次")
        self.assertEqual(stats["skipped_md5"], 1)
        # 统计编码调用：再次加入同一批 → 全部复用，0 次编码
        calls = {"n": 0}
        orig = self.encoder.encode_batch

        def counting(paths, **kw):
            calls["n"] += len(paths)
            return orig(paths, **kw)

        self.encoder.encode_batch = counting
        try:
            stats2 = idx.add_images(
                [str(self.photos / "base.jpg"),
                 str(self.photos / "bright.jpg")], self.encoder)
            self.assertEqual(calls["n"], 0, "已索引照片不得重复计算 embedding")
            self.assertEqual(stats2["new"], 0)
            self.assertEqual(stats2["skipped_existing"], 2)
            # 新照片增量
            stats3 = idx.add_images(
                [str(self.photos / "shift.jpg")], self.encoder)
            self.assertEqual(stats3["new"], 1)
            self.assertEqual(calls["n"], 1)
            self.assertEqual(idx.count(), 3)
            # 已索引的 MD5 相同副本（copy_base 再出现）→ md5 去重跳过
            stats4 = idx.add_images(
                [str(self.photos / "copy_base.jpg")], self.encoder)
            self.assertEqual(stats4["skipped_md5"], 1)
            self.assertEqual(stats4["new"], 0)
            self.assertEqual(calls["n"], 1, "MD5 重复不触发编码")
        finally:
            self.encoder.encode_batch = orig

    # ── 8b：模型一致性（换模型不混用）──
    def test_model_mismatch_raises(self):
        self._maybe_skip_model()
        cache = self.dir / "test_mismatch"
        idx, _ = self._build_index(cache, [self.photos / "base.jpg"])
        with self.assertRaises(VisualSearchModelMismatch):
            idx.check_model({
                "model_name": "ViT-B-32", "pretrained": "x",
                "embedding_dimension": 512, "model_version": "0"})

    # ── 14/15/16：不影响角色系统（import 级隔离检查）──
    def test_no_identity_dependency(self):
        import ast
        pkg = Path(__file__).resolve().parents[1] / "core" / "visual_search"
        for f in pkg.rglob("*.py"):
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=f.name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                self.assertFalse(
                    any(n and (n.startswith("core.identity") or "fursee" in n.lower()
                               or n.startswith("core.model_hub"))
                        for n in names),
                    f"{f.name} 不得 import 角色系统/Fursee/model_hub")


if __name__ == "__main__":
    unittest.main()
