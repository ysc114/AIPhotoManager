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
- 中文路径安全：索引读写改走 faiss.serialize_index()/deserialize_index()
  + Python 文件 I/O。faiss 自带的路径式 I/O 在非 ASCII 目录
  （如 ...\Desktop\同步\...）下会直接失败，导致索引永远写不出盘。
"""

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path

import numpy as np

from core.visual_search.query import expand_queries

_DEFAULT_DIR = str(Path(__file__).resolve().parents[2] / "cache" / "visual_search")

_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# 分批落盘粒度：每编码 N 张保存一次；中断后可从上次进度续建
SAVE_CHUNK = 25


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


def _read_faiss_index(path, faiss_module):
    """读取 FAISS 索引文件，返回 (index, 是否旧格式)。

    新格式 = faiss.serialize_index() 字节（Python 文件 I/O，中文路径安全）。
    旧格式 = faiss.write_index() 写的原生文件；此时复制到 ASCII 临时路径
    读取一次，由调用方按新格式重写（faiss 路径式 I/O 不支持非 ASCII 路径）。
    """
    with open(path, "rb") as fh:
        blob = fh.read()
    try:
        idx = faiss_module.deserialize_index(np.frombuffer(blob, dtype=np.uint8))
        if idx is not None and int(getattr(idx, "d", 0)) > 0:
            return idx, False
    except Exception:
        pass
    fd, tmp = tempfile.mkstemp(prefix="vs_legacy_", suffix=".faiss")
    os.close(fd)
    try:
        with open(tmp, "wb") as fh:
            fh.write(blob)
        return faiss_module.read_index(tmp), True
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


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
        self._cleanup_temp_files()
        if not os.path.isfile(self.index_path) or not os.path.isfile(self.metadata_path):
            self._loaded = True
            return
        try:
            import faiss
        except ImportError as e:
            raise RuntimeError("缺少 faiss：pip install faiss-cpu") from e
        with self._lock:
            try:
                index, legacy = _read_faiss_index(self.index_path, faiss)
                with open(self.metadata_path, "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
            except (OSError, ValueError, RuntimeError) as e:
                print(f"[visual_search] 索引加载失败，视为空索引: {e}")
                self._index = None
                self._entries = []
                self._model = None
                self._md5_set = set()
                self._loaded = True
                return
            self._index = index
            self._model = meta.get("model") or None
            self._entries = meta.get("entries") or []
            self._md5_set = {e.get("md5") for e in self._entries if e.get("md5")}
            self._loaded = True
            if legacy:
                # 旧格式索引：立刻按新格式重写（失败不影响本次加载）
                try:
                    self._save()
                except Exception as e:
                    print(f"[visual_search] 旧索引格式升级失败（忽略）: {e}")

    def _save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        import faiss
        # 内存序列化 + Python 写文件：绕开 faiss 路径式 I/O 的中文路径缺陷
        blob = faiss.serialize_index(self._index).tobytes()
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=".index_tmp_", dir=str(self.dir))
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
            os.replace(tmp, self.index_path)
            tmp = None
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
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

    def _cleanup_temp_files(self):
        """清理历史上「写盘中途退出」遗留的临时文件。"""
        try:
            stale = list(self.dir.glob(".index_tmp_*")) + list(self.dir.glob(".meta_tmp_*"))
        except OSError:
            return
        for p in stale:
            try:
                p.unlink()
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

        # 2)+3) 分批编码 + 分批落盘（IndexFlatIP：余弦 = 内积）
        # 每 SAVE_CHUNK 张保存一次：中途退出时已落盘部分不复算，可续建
        new_count = 0
        failed = 0
        done = 0
        total_encode = len(to_encode)
        for start in range(0, total_encode, SAVE_CHUNK):
            chunk = to_encode[start:start + SAVE_CHUNK]

            def _progress(cur, _total, _base=done):
                if progress_cb:
                    progress_cb(min(_base + cur, total_encode), total_encode)

            encoded = encoder.encode_batch(
                [t[0] for t in chunk], progress_cb=_progress)
            done += len(chunk)
            vectors = []
            for (path, md5, size, mtime_ns), vec in zip(chunk, encoded):
                if vec is None or vec.shape[0] != dim:
                    failed += 1
                    continue
                vectors.append((path, md5, size, mtime_ns,
                                vec.astype(np.float32)))
            if not vectors:
                continue
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
                self._save()
            new_count += len(vectors)
        return {
            "new": new_count,
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

    def search_by_text(self, query_text, encoder, top_k=20):
        """自然语言搜索：文本 embedding（与图像同一 CLIP 空间）→ 索引检索。

        中文查询先扩展成英文提示词（CLIP 文本塔对中文几乎无效，见 query.py）；
        也接受 str 列表（调用方已自行扩展时直接使用）。
        """
        if isinstance(query_text, (list, tuple)):
            texts = [str(t) for t in query_text]
        else:
            texts = expand_queries(query_text)
        return self.search_by_texts(texts, encoder, top_k=top_k)

    def search_by_texts(self, texts, encoder, top_k=20):
        """多查询文本检索：逐变体编码后按「每张照片的最大相似度」融合排序。

        用于中文查询（原文 + 英文提示词取长补短）；单变体时与旧行为一致。
        """
        items = [str(t).strip() for t in (texts or []) if str(t or "").strip()]
        if not items or self._index is None or self._index.ntotal == 0:
            return []
        vecs = np.asarray(encoder.encode_text(items), dtype=np.float32)
        if vecs.ndim == 1:
            vecs = vecs.reshape(1, -1)
        best = {}
        for vec in vecs:
            for entry, sim in self.search(vec, top_k=top_k):
                pid = entry["id"]
                prev = best.get(pid)
                if prev is None or sim > prev[1]:
                    best[pid] = (entry, float(sim))
        ranked = sorted(best.values(), key=lambda x: -x[1])
        return [{"photo_id": entry["id"], "path": entry["path"],
                 "similarity": round(float(sim), 4)}
                for entry, sim in ranked[:max(1, int(top_k))]]


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


def default_cache_dir():
    """默认索引缓存目录（项目 cache/visual_search）。"""
    return _DEFAULT_DIR


def read_index_status(cache_dir=None, photos_dir=None):
    """只读索引状态（设置页状态行用）：不加载模型、不加载 faiss。

    返回 {state, exists, indexed, photos_total, model_name, pretrained,
          dimension, updated_at}；state ∈ missing/incomplete/corrupt/mismatch/ready
    """
    d = Path(cache_dir) if cache_dir else Path(_DEFAULT_DIR)
    index_file = d / "index.faiss"
    meta_file = d / "metadata.json"
    photos = Path(photos_dir) if photos_dir else (
        Path(__file__).resolve().parents[2] / "photos")
    status = {
        "state": "missing", "exists": False, "indexed": 0, "photos_total": 0,
        "model_name": "", "pretrained": "", "dimension": 0,
        "updated_at": None,
    }
    if photos.is_dir():
        try:
            status["photos_total"] = sum(
                1 for n in os.listdir(photos)
                if os.path.splitext(n)[1].lower() in _PHOTO_EXTS)
        except OSError:
            pass
    has_index = index_file.is_file()
    has_meta = meta_file.is_file()
    if not (has_index and has_meta):
        status["state"] = "incomplete" if (has_index or has_meta) else "missing"
        return status
    try:
        with open(meta_file, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        status["state"] = "corrupt"
        return status
    model = meta.get("model") or {}
    status.update({
        "exists": True,
        "indexed": len(meta.get("entries") or []),
        "model_name": model.get("model_name") or "",
        "pretrained": model.get("pretrained") or "",
        "dimension": int(model.get("embedding_dimension") or 0),
        "updated_at": index_file.stat().st_mtime,
        "state": "ready",
    })
    try:
        from core.visual_search.embedding import (
            DEFAULT_MODEL_NAME, DEFAULT_PRETRAINED)
    except Exception:
        return status
    if (status["model_name"] and status["model_name"] != DEFAULT_MODEL_NAME) or (
            status["pretrained"]
            and status["pretrained"] != DEFAULT_PRETRAINED):
        status["state"] = "mismatch"
    return status


def clear_index_files(cache_dir=None):
    """删除索引缓存文件（重建用，不动照片与角色数据）。返回删除数量。"""
    d = Path(cache_dir) if cache_dir else Path(_DEFAULT_DIR)
    removed = 0
    if not d.is_dir():
        return 0
    targets = [d / "index.faiss", d / "metadata.json"]
    try:
        targets += list(d.glob(".index_tmp_*")) + list(d.glob(".meta_tmp_*"))
    except OSError:
        pass
    for p in targets:
        try:
            if p.is_file():
                p.unlink()
                removed += 1
        except OSError:
            pass
    return removed
