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
import json
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
    clear_index_files,
    read_index_status,
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

    def test_encode_text_dim_and_normalized(self):
        """文本 embedding：维度/归一化/批量（与图像同一 CLIP 空间）。"""
        self._maybe_skip_model()
        v = self.encoder.encode_text("a colorful abstract photo")
        self.assertEqual(v.shape, (self.model_info["embedding_dimension"],))
        self.assertAlmostEqual(float(np.linalg.norm(v)), 1.0, places=4)
        vs = self.encoder.encode_text(["a photo", "a cat"])
        self.assertEqual(vs.shape, (2, self.model_info["embedding_dimension"]))

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

    def test_search_by_text_sorted(self):
        """自然语言搜索：文本 embedding 检索（排序/字段/降序）。"""
        self._maybe_skip_model()
        cache = self.dir / "test_text"
        paths = [self.photos / n for n in
                 ("base.jpg", "bright.jpg", "shift.jpg", "other.jpg")]
        idx, _ = self._build_index(cache, paths)
        res = idx.search_by_text(
            "a colorful abstract picture with geometric shapes",
            self.encoder, top_k=5)
        self.assertGreaterEqual(len(res), 1)
        sims = [r["similarity"] for r in res]
        self.assertEqual(sims, sorted(sims, reverse=True), "按相似度降序")
        for r in res:
            self.assertIn("photo_id", r)
            self.assertIn("path", r)
            self.assertIn("similarity", r)

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


class _FakeEncoder:
    """确定性假编码器：不加载模型即可覆盖索引持久化/续建/清理逻辑。"""

    def __init__(self, dim=8):
        self._dim = dim

    def _vec(self, key):
        rng = np.random.default_rng(abs(hash(key)) % (2 ** 32))
        v = rng.normal(size=self._dim).astype(np.float32)
        return v / np.linalg.norm(v)

    def model_info(self):
        return {"model_name": "ViT-L-14",
                "pretrained": "datacomp_xl_s13b_b90k",
                "embedding_dimension": self._dim,
                "model_version": "fake"}

    def encode(self, path, normalize=True):
        return self._vec(str(path))

    def encode_batch(self, paths, normalize=True, progress_cb=None):
        out = []
        for i, p in enumerate(paths):
            out.append(self._vec(str(p)))
            if progress_cb:
                progress_cb(i + 1, len(paths))
        return out


class VisualIndexPersistenceTests(unittest.TestCase):
    """faiss 中文路径 / 中断续建 / 垃圾清理 / 状态读取（假编码器，不加载模型）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.encoder = _FakeEncoder()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _photos(self, name, count):
        d = self.dir / name
        d.mkdir(parents=True, exist_ok=True)
        files = []
        for i in range(count):
            p = d / f"img_{i}.jpg"
            if not p.exists():
                p.write_bytes(f"fake-image-{i}".encode())
            files.append(str(p))
        return d, files

    def test_cjk_cache_dir_roundtrip(self):
        """回归：项目路径含中文（.../同步/...）时索引也能存能读能搜。"""
        _, files = self._photos("cjk_photos", 6)
        cache = self.dir / "同步缓存_测试"
        idx = VisualSearchIndex(cache_dir=str(cache))
        stats = idx.add_images(files[:4], self.encoder)
        self.assertEqual(stats["new"], 4)
        self.assertTrue((cache / "index.faiss").is_file())
        self.assertTrue((cache / "metadata.json").is_file())
        idx2 = VisualSearchIndex(cache_dir=str(cache))
        self.assertEqual(idx2.count(), 4)
        hits = idx2.search_by_image(files[0], self.encoder, top_k=3)
        self.assertEqual(len(hits), 3)
        # 状态读取（设置页状态行）不加载模型
        st = read_index_status(cache_dir=str(cache),
                               photos_dir=str(self.dir / "cjk_photos"))
        self.assertEqual(st["state"], "ready")
        self.assertEqual(st["indexed"], 4)
        self.assertEqual(st["photos_total"], 6)

    def test_resume_after_interrupt(self):
        """分批落盘 + 续建：第二次调用跳过已落盘照片，entries 无重复。"""
        _, files = self._photos("resume_photos", 30)
        cache = self.dir / "resume_cache"
        idx = VisualSearchIndex(cache_dir=str(cache))
        first = idx.add_images(files[:10], self.encoder)
        self.assertEqual(first["new"], 10)
        stats = idx.add_images(files, self.encoder)
        self.assertEqual(stats["skipped_existing"], 10)
        self.assertEqual(stats["new"], 20)
        self.assertEqual(idx.count(), 30)
        with open(idx.metadata_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        self.assertEqual(len(meta["entries"]), 30)
        paths = [e["path"] for e in meta["entries"]]
        self.assertEqual(len(paths), len(set(paths)))
        ids = [e["id"] for e in meta["entries"]]
        self.assertEqual(ids, list(range(30)))

    def test_progress_is_cumulative(self):
        """分批编码后进度回调仍是全局累计（1..N），不会每块归零。"""
        _, files = self._photos("progress_photos", 30)
        idx = VisualSearchIndex(cache_dir=str(self.dir / "progress_cache"))
        seen = []
        idx.add_images(files, self.encoder,
                       progress_cb=lambda d, t: seen.append((d, t)))
        self.assertEqual(seen[0], (1, 30))
        self.assertEqual(seen[-1], (30, 30))
        self.assertTrue(all(seen[i][0] <= seen[i + 1][0]
                            for i in range(len(seen) - 1)))
        self.assertTrue(all(t == 30 for _, t in seen))

    def test_stale_temp_files_cleaned(self):
        """历史上中断留下的 .index_tmp_*/.meta_tmp_* 在加载时清理。"""
        _, files = self._photos("cleanup_photos", 3)
        cache = self.dir / "cleanup_cache"
        idx = VisualSearchIndex(cache_dir=str(cache))
        idx.add_images(files, self.encoder)
        (cache / ".index_tmp_stale").write_bytes(b"")
        (cache / ".meta_tmp_stale.json").write_text("{}", encoding="utf-8")
        VisualSearchIndex(cache_dir=str(cache))
        leftovers = [p.name for p in cache.iterdir() if "_tmp_" in p.name]
        self.assertEqual(leftovers, [])
        self.assertEqual(
            read_index_status(cache_dir=str(cache))["state"], "ready")

    def test_legacy_index_upgraded(self):
        """旧格式（faiss.write_index）索引可读，并自动改写为新格式。"""
        import faiss
        _, files = self._photos("legacy_photos", 2)
        cache = self.dir / "legacy_cache"
        cache.mkdir(parents=True, exist_ok=True)
        plain = faiss.IndexFlatIP(8)
        plain.add(np.random.rand(2, 8).astype(np.float32))
        ascii_tmp = self.dir / "legacy_plain.faiss"
        faiss.write_index(plain, str(ascii_tmp))
        (cache / "index.faiss").write_bytes(ascii_tmp.read_bytes())
        with open(cache / "metadata.json", "w", encoding="utf-8") as fh:
            json.dump({
                "version": 1, "model": self.encoder.model_info(),
                "entries": [
                    {"id": 0, "path": files[0], "md5": "a", "size": 1,
                     "mtime_ns": 1},
                    {"id": 1, "path": files[1], "md5": "b", "size": 1,
                     "mtime_ns": 1},
                ]}, fh)
        idx = VisualSearchIndex(cache_dir=str(cache))
        self.assertEqual(idx.count(), 2)
        blob = (cache / "index.faiss").read_bytes()
        restored = faiss.deserialize_index(np.frombuffer(blob, dtype=np.uint8))
        self.assertEqual(restored.ntotal, 2)

    def test_clear_index_files(self):
        """重建入口：清空缓存文件后状态回到 missing。"""
        _, files = self._photos("clear_photos", 3)
        cache = self.dir / "clear_cache"
        idx = VisualSearchIndex(cache_dir=str(cache))
        idx.add_images(files, self.encoder)
        removed = clear_index_files(cache_dir=str(cache))
        self.assertEqual(removed, 2)
        self.assertEqual(
            read_index_status(cache_dir=str(cache))["state"], "missing")

class QueryExpansionTests(unittest.TestCase):
    """中文查询扩展：CLIP 文本塔对中文偏弱，额外给出英文关键词提示词。

    实测（240 张真实图库，同一张图最高相似度）：中文「兽装」0.220 → 英文 "a photo of fursuit" 0.318；
    扩展后逐照片取最大相似度 → 只升不降。
    """

    def test_chinese_query_adds_english_prompt(self):
        from core.visual_search.query import expand_queries

        out = expand_queries("兽装")
        self.assertIn("兽装", out, "保留原始查询")
        self.assertTrue(any("fursuit" in v for v in out), out)
        self.assertLessEqual(len(out), 4, "变体数受控（控制编码开销）")

    def test_english_query_unchanged(self):
        from core.visual_search.query import expand_queries

        self.assertEqual(expand_queries("fursuit"), ["fursuit"])
        self.assertEqual(expand_queries("a photo of a wolf"),
                         ["a photo of a wolf"])

    def test_longer_term_wins(self):
        from core.visual_search.query import expand_queries

        joined = " ".join(expand_queries("水果")).lower()
        self.assertIn("fruit", joined)
        self.assertNotIn("water", joined, "「水果」不应被单字「水」误判")

    def test_mixed_query_keeps_both(self):
        from core.visual_search.query import expand_queries

        out = expand_queries("狼 fursuit")
        self.assertIn("狼 fursuit", out)
        self.assertTrue(any("wolf" in v for v in out), out)

    def test_empty_query(self):
        from core.visual_search.query import expand_queries

        self.assertEqual(expand_queries(""), [])
        self.assertEqual(expand_queries("   "), [])


class _TextFakeEncoder:
    """文本/图片都映射到固定向量的假编码器（验证多变体融合，不加载模型）。"""

    def __init__(self, vectors, dim=4):
        self._v = {k: np.asarray(v, dtype=np.float32)
                   for k, v in vectors.items()}
        self._dim = dim

    def model_info(self):
        return {"model_name": "ViT-L-14",
                "pretrained": "datacomp_xl_s13b_b90k",
                "embedding_dimension": self._dim,
                "model_version": "fake"}

    def encode(self, path, normalize=True):
        return self._v[str(path)]

    def encode_batch(self, paths, normalize=True, progress_cb=None):
        out = []
        for i, p in enumerate(paths):
            out.append(self._v[str(p)])
            if progress_cb:
                progress_cb(i + 1, len(paths))
        return out

    def encode_text(self, texts, normalize=True):
        single = isinstance(texts, str)
        items = [texts] if single else list(texts)
        arr = np.stack([self._v[str(t)] for t in items])
        return arr[0] if single else arr


class MultiVariantSearchTests(unittest.TestCase):
    """多变体文本检索：逐照片取最大相似度（中文靠英文变体命中）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.photos = self.dir / "photos"
        self.photos.mkdir()
        a, b = self.photos / "wolf.jpg", self.photos / "city.jpg"
        a.write_bytes(b"fake-a")
        b.write_bytes(b"fake-b")
        # 索引内部统一用正斜杠路径，假编码器的键也要对齐
        self.a = str(a).replace("\\", "/")
        self.b = str(b).replace("\\", "/")
        self.enc = _TextFakeEncoder({
            self.a: [1, 0, 0, 0],
            self.b: [0, 1, 0, 0],
            "qn": [0, 0, 1, 0],                # 与两张都不相似
            "a photo of wolf": [1, 0, 0, 0],   # 命中 a
            "a photo of city": [0, 1, 0, 0],   # 命中 b
        })
        self.idx = VisualSearchIndex(cache_dir=str(self.dir / "cache"))
        self.idx.add_images([self.a, self.b], self.enc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_single_variant_sorted(self):
        hits = self.idx.search_by_text("a photo of wolf", self.enc, top_k=2)
        self.assertEqual(hits[0]["path"], self.a)
        self.assertAlmostEqual(hits[0]["similarity"], 1.0, places=3)

    def test_multi_variant_takes_max_similarity(self):
        hits = self.idx.search_by_texts(
            ["qn", "a photo of city"], self.enc, top_k=2)
        self.assertEqual(hits[0]["path"], self.b,
                         "应按各变体中的最大相似度排序")
        self.assertGreater(hits[0]["similarity"], hits[1]["similarity"])

    def test_search_by_text_accepts_expanded_list(self):
        hits = self.idx.search_by_text(["qn", "a photo of wolf"], self.enc, top_k=2)
        self.assertEqual(hits[0]["path"], self.a, "列表入参视为已扩展，不再二次扩展")

    def test_chinese_query_end_to_end_uses_mapping(self):
        """中文查询「狼」→ 扩展出英文提示词 → 命中狼的照片。"""
        from core.visual_search.query import expand_queries

        variants = expand_queries("狼")
        self.assertTrue(any("wolf" in v for v in variants), variants)
        hits = self.idx.search_by_texts(
            [v for v in variants if v in self.enc._v], self.enc, top_k=2)
        self.assertEqual(hits[0]["path"], self.a)


class TextEmbeddingCacheTests(unittest.TestCase):
    """文本向量缓存：同一查询重复编码不再调用模型。"""

    def test_repeated_query_hits_cache(self):
        import torch

        from core.visual_search import ClipImageEncoder

        calls = {"n": 0}

        class _FakeTokens:
            def __init__(self, n):
                self.n = n

            def to(self, device):
                return self

        class _FakeTextModel:
            def encode_text(self, tokens):
                calls["n"] += 1
                n = max(1, int(getattr(tokens, "n", 1)))
                return torch.arange(1, n * 4 + 1,
                                    dtype=torch.float32).reshape(n, 4)

            def cpu(self):
                return self

        enc = ClipImageEncoder(device="cpu")
        enc._model = _FakeTextModel()
        enc._dim = 4
        try:
            with mock.patch("open_clip.get_tokenizer",
                            return_value=lambda items: _FakeTokens(len(list(items)))):
                v1 = enc.encode_text("hello")
                v2 = enc.encode_text("hello")
                v3 = enc.encode_text(["hello", "world"])
            self.assertEqual(calls["n"], 2,
                             "重复查询应命中缓存，只编码新文本")
            np.testing.assert_allclose(v1, v2)
            self.assertEqual(v3.shape, (2, 4))
            self.assertTrue(np.allclose(np.linalg.norm(v3, axis=1), 1.0, atol=1e-5))
        finally:
            enc.close()
        self.assertEqual(enc._text_cache, {}, "close() 后缓存清空（模型释放）")

if __name__ == "__main__":
    unittest.main()
