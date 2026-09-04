# core/visual_search/index.py
"""
FAISS 视觉索引 + metadata —— 独立于 identity_db.sqlite（不碰角色数据）。

磁盘布局（项目 cache/visual_search/，已被 .gitignore 的 cache/ 覆盖）：
    index.faiss      FAISS IndexFlatIP（余弦 = 内积，向量已归一化）
    metadata.json    {"version", "model": {model_name/pretrained/
                     embedding_dimension/model_version}, "entries":
                     [{id, path, md5, size, mtime_ns}]}

关键保证：
- 模型不一致（换模型后旧 index）→ 直接抛错，不悄悄混用不同模型 embedding
- 增量：已索引(md5+size+mtime 不变)直接复用；MD5 完全重复的照片
  不重复计算 embedding（与 MD5 去重逻辑一致）
- 线程安全：保存用临时文件 + os.replace
"""

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path

import numpy as np

_DEFAULT_DIR = str(Path(__file__).resolve().parents[2] / "cache" / "visual_search")


class VisualSearchModelMismatch(RuntimeError):
    """索引模型与当前编码器模型不一致（禁止混用 embedding）。"""


def _md5(path, chunk=1 << 20):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _norm(p):
    return str(Path(p).resolve()).replace("\\", "/")


class VisualSearchIndex:
    """FAISS 向量索引 + 元数据（photo_id = 向量在索引中的位置）。"""

    def __init__(self, cache_dir=None):
        self.dir = Path(cache_dir) if cache_dir else Path(_DEFAULT_DIR)
        self._lock = threading.Lock()
        self._index = None          # faiss IndexFlatIP
        self._model = None          # 已记录模型信息
        self._entries = []          # [{id, path, md5, size, mtime_ns}]
        self._md5_set = set()
        self._loaded = False
        self._load()

    # --------------------------------------------------------
    # 路径
    # --------------------------------------------------------
    @property
    def index_path(self):
        return str(self.dir / "index.faiss")

    @property
    def metadata_path(self):
        return str(self.dir / "metadata.json")

    # --------------------------------------------------------
    # 加载 / 保存
    # --------------------------------------------------------
    def _load(self):
        if not os.path.isfile(self.index_path) or not os.path.isfile(self.metadata_path):
            self._loaded = True
            return
        try:
            import faiss
        except ImportError as e:
            raise RuntimeError("缺少 faiss：pip install faiss-cpu") from e
        with self._lock:
            try:
                self._index = faiss.read_index(self.index_path)
                with open(self.metadata_path, "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
            except (OSError, ValueError) as e:
                print(f"[visual_search] 索引加载失败，视为空索引: {e}")
                self._index = None
                self._entries = []
                self._model = None
                self._md5_set = set()
                self._loaded = True
                return
            self._model = meta.get("model") or None
            self._entries = meta.get("entries") or []
            self._md5_set = {e.get("md5") for e in self._entries if e.get("md5")}
            self._loaded = True

    def _save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        import faiss
        fd, tmp = tempfile.mkstemp(prefix=".index_tmp_", dir=str(self.dir))
        os.close(fd)
        faiss.write_index(self._index, tmp)
        os.replace(tmp, self.index_path)
        data = {
            "version": 1,
            "model": self._model,
            "entries": self._entries,
        }
        fd, tmp2 = tempfile.mkstemp(
            prefix=".meta_tmp_", suffix=".json", dir=str(self.dir))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            os.replace(tmp2, self.metadata_path)
        finally:
            if os.path.exists(tmp2):
                try:
                    os.remove(tmp2)
                except OSError:
                    pass

    # --------------------------------------------------------
    # 模型一致性
    # --------------------------------------------------------
    def check_model(self, model_info):
        """当前编码器模型必须与索引已记录模型一致，否则拒绝混用。"""
        if self._model is None:
            return
        for key in ("model_name", "pretrained", "embedding_dimension"):
            if self._model.get(key) != model_info.get(key):
                raise VisualSearchModelMismatch(
                    f"索引模型({self._model.get(key)})与当前模型"
                    f"({model_info.get(key)})不一致，请重建索引"
                    f"(删除 {self.dir} 后重新构建)")

    # --------------------------------------------------------
    # 加入照片（增量 + MD5 去重）
    # --------------------------------------------------------
    def add_images(self, image_paths, encoder, progress_cb=None):
        """批量加入：只编码新照片；已索引 / MD5 重复的直接复用。

        返回 {"new": int, "skipped_existing": int, "skipped_md5": int,
              "failed": int, "total": int}
        """
        model_info = encoder.model_info()
        self.check_model(model_info)
        if self._model is None:
            self._model = model_info
        dim = model_info["embedding_dimension"]

        # 1) 过滤：已索引（md5+size+mtime 相同）/ MD5 重复（内容完全一致）
        to_encode = []
        skipped_existing = 0
        skipped_md5 = 0
        seen_md5_batch = set(self._md5_set)
        for p in image_paths:
            np_ = _norm(p)
            try:
                st = os.stat(np_)
                md5 = _md5(np_)
            except OSError:
                continue
            existing = next(
                (e for e in self._entries
                 if e["path"] == np_ and e.get("md5") == md5
                 and e.get("size") == st.st_size
                 and e.get("mtime_ns") == st.st_mtime_ns),
                None,
            )
            if existing is not None:
                skipped_existing += 1
                continue
            if md5 in seen_md5_batch:
                # 内容完全重复：不重复计算 embedding（无论是否已入索引）
                skipped_md5 += 1
                continue
            seen_md5_batch.add(md5)
            to_encode.append((np_, md5, st.st_size, st.st_mtime_ns))

        # 2) 批量编码新照片
        vectors = []
        failed = 0
        if to_encode:
            encoded = encoder.encode_batch(
                [t[0] for t in to_encode], progress_cb=progress_cb)
            for (path, md5, size, mtime_ns), vec in zip(to_encode, encoded):
                if vec is None or vec.shape[0] != dim:
                    failed += 1
                    continue
                vectors.append((path, md5, size, mtime_ns,
                                vec.astype(np.float32)))

        # 3) 加入 FAISS（IndexFlatIP：余弦 = 内积）
        with self._lock:
            import faiss
            if self._index is None:
                self._index = faiss.IndexFlatIP(dim)
            base = self._index.ntotal
            for (path, md5, size, mtime_ns, vec) in vectors:
                self._index.add(vec.reshape(1, -1))
                self._entries.append({
                    "id": base + len(self._entries),
                    "path": path, "md5": md5,
                    "size": size, "mtime_ns": mtime_ns,
                })
                self._md5_set.add(md5)
            # id 与向量位置一一对应（重建自增 id，保证顺序一致）
            for i, e in enumerate(self._entries):
                e["id"] = i
            if vectors:
                self._save()
        return {
            "new": len(vectors),
            "skipped_existing": skipped_existing,
            "skipped_md5": skipped_md5,
            "failed": failed,
            "total": len(image_paths),
        }

    def add_image(self, image_path, encoder):
        return self.add_images([image_path], encoder)

    # --------------------------------------------------------
    # 搜索
    # --------------------------------------------------------
    def count(self):
        return len(self._entries)

    def model_info(self):
        return self._model

    def search(self, query_vector, top_k=20):
        """按归一化向量搜索：返回 [(entry, similarity)] 降序。"""
        if self._index is None or self._index.ntotal == 0:
            return []
        q = np.asarray(query_vector, dtype=np.float32).reshape(1, -1)
        k = min(int(top_k), int(self._index.ntotal))
        D, I = self._index.search(q, k)
        out = []
        for j in range(k):
            idx = int(I[0][j])
            if 0 <= idx < len(self._entries):
                out.append((self._entries[idx], float(D[0][j])))
        return out

    def search_by_image(self, image_path, encoder, top_k=20):
        """以图搜图：编码查询图 → 索引检索（排除自身路径）。"""
        vec = encoder.encode(image_path)
        qpath = _norm(image_path)
        results = []
        for entry, sim in self.search(vec, top_k=top_k + 1):
            if entry["path"] == qpath:
                continue
            results.append({
                "photo_id": entry["id"],
                "path": entry["path"],
                "similarity": round(sim, 4),
            })
            if len(results) >= top_k:
                break
        return results


# 模块级单例
_index_instance = None
_index_lock = threading.Lock()


def get_index(cache_dir=None, force_new=False):
    global _index_instance
    if force_new or cache_dir is not None:
        return VisualSearchIndex(cache_dir=cache_dir)
    if _index_instance is None:
        with _index_lock:
            if _index_instance is None:
                _index_instance = VisualSearchIndex()
    return _index_instance


def reset_index():
    global _index_instance
    with _index_lock:
        _index_instance = None
