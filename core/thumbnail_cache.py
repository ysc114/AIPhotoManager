"""
ThumbnailCache —— 独立图片缩略图缓存系统（第三方开源组件整合 · 第 1 项）

参考成熟图片工作站「MD5 → 缓存 → 异步解码 → 缩略图 → UI」思路：
原图只负责最终预览；角色卡片 / 照片墙等浏览场景优先读取磁盘缩略图缓存，
避免界面线程反复解码原始大图。

设计要点：
- 缓存键 = 原图内容 MD5 + 尺寸 + bbox（detection 裁剪参数）：
  原图内容变化 → MD5 变化 → 键失效 → 自动重新生成
- MD5 内存缓存按 (mtime_ns, size) 快速判定文件未变，避免频繁重读大图；
  文件被修改/替换 → mtime/size 变化 → 重新计算内容 MD5
- 支持 256px / 512px 两种长边尺寸；优先 WebP，写入失败回退 PNG
- 生成全部在后台 QThread 完成，不阻塞 GUI 主线程
- 多页面共享模块级单例 thumbnail_cache
- 任何失败（文件缺失 / 解码失败 / 写入失败）→ failed 信号，
  调用方回退原图，绝不影响 AI / 角色数据库 / 主流程

纯缓存层：不解析业务数据，不读写 identity_db，不触碰聚类 / MD5 去重逻辑。
"""

import hashlib
import json
import os
import queue
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRect, QSize, Qt, Signal, QThread
from PySide6.QtGui import QImageReader, QImageWriter, QImageIOHandler

# ── 支持的长边尺寸 ──────────────────────────────────────────
SUPPORTED_SIZES = (256, 512)

# 模块级缓存目录（相对项目根；项目根通过本文件定位）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = _PROJECT_ROOT / "cache" / "thumbnails"

# 防解析失败导致无限重试的隔离区大小
_MAX_PENDING_PER_KEY = 8


def _content_md5(path):
    """图片文件内容 MD5（分块读取，避免一次性载入内存）。"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_bbox(bbox, img_w, img_h):
    """bbox JSON → QRect（像素坐标）。兼容两种历史格式：

    - 绝对像素 [x1, y1, x2, y2]
    - 0-1 归一化 [x1, y1, x2, y2]（全部值 ≤ 1 时按宽高换算）

    无效输入返回 None（调用方回退完整原图）。
    """
    if not bbox or img_w <= 0 or img_h <= 0:
        return None
    try:
        vals = json.loads(bbox) if isinstance(bbox, str) else bbox
        x1, y1, x2, y2 = (float(v) for v in vals[:4])
    except (ValueError, TypeError, ZeroDivisionError):
        return None
    if any(v != v for v in (x1, y1, x2, y2)):  # NaN 防御
        return None
    if x1 >= x2 or y1 >= y2:
        return None
    if x2 <= 1.0 and y2 <= 1.0 and x1 >= 0.0 and y1 >= 0.0 and img_w > 1 and img_h > 1:
        x1, y1, x2, y2 = x1 * img_w, y1 * img_h, x2 * img_w, y2 * img_h
    x1 = max(0, min(int(x1), img_w - 1))
    y1 = max(0, min(int(y1), img_h - 1))
    x2 = max(x1 + 1, min(int(x2), img_w))
    y2 = max(y1 + 1, min(int(y2), img_h))
    return QRect(x1, y1, x2 - x1, y2 - y1)


def _display_rect_to_raw(rect, raw_w, raw_h, transform):
    """显示方向 bbox → 原始图像坐标（与 UI 层一致，供 setClipRect 使用）。"""
    T = QImageIOHandler.Transformation
    if transform == T.TransformationNone:
        return QRect(rect)
    if transform == T.TransformationRotate180:
        return QRect(raw_w - rect.x() - rect.width(),
                     raw_h - rect.y() - rect.height(),
                     rect.width(), rect.height())
    if transform == T.TransformationRotate90:  # EXIF6
        return QRect(rect.y(), raw_h - rect.x() - rect.width(),
                     rect.height(), rect.width())
    return None  # 镜像等罕见组合：回退整图


class _ThumbnailWorker(QThread):
    """后台生成线程：消费 (path, size, bbox) 任务，产出缩略图。"""

    def __init__(self, owner, queue_):
        super().__init__(owner)
        self._q = queue_
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        while not self._stop:
            try:
                item = self._q.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self.parent()._process(*item)
            except Exception as e:  # 单任务失败不影响线程继续
                print(f"[thumbcache] 任务异常 {item[0]}: {e}")
            finally:
                self._q.task_done()


class ThumbnailCache(QObject):
    """缩略图缓存（线程安全的磁盘缓存 + 后台生成）。

    主线程用法：
        path = cache.get_cached(src, 256, bbox_json)   # 同步命中（不读大图）
        cache.request(src, 256, bbox_json, on_ready)   # 未命中 → 后台生成后回调
    """

    ready = Signal(str, int, str, str)  # (source_path, size, bbox, cache_path)
    failed = Signal(str, int, str)      # (source_path, size, bbox)

    def __init__(self, cache_dir=None, enabled=True, parent=None):
        super().__init__(parent)
        self._dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self._enabled = bool(enabled)
        self._q = queue.Queue()
        self._lock = threading.Lock()
        self._md5_cache = {}          # path -> (mtime_ns, size, md5)
        self._pending = {}            # (path, size, bbox) -> [callback, ...]
        self._inflight = set()        # (path, size, bbox) 去重（合并重复请求）
        self._worker = _ThumbnailWorker(self, self._q)
        self._worker.daemon = True
        self._worker.start()
        self.ready.connect(self._on_ready)
        self.failed.connect(self._on_failed)

    # --------------------------------------------------------
    # 生命周期
    # --------------------------------------------------------
    def set_enabled(self, enabled):
        """开/关缓存。关闭后 request 直接回调 None（UI 回退原图）。"""
        self._enabled = bool(enabled)
        if not self._enabled:
            # 清空待办，避免关闭后仍在后台干活
            with self._lock:
                self._pending.clear()
                self._inflight.clear()
            try:
                while True:
                    self._q.get_nowait()
                    self._q.task_done()
            except queue.Empty:
                pass

    @property
    def enabled(self):
        return self._enabled

    def shutdown(self):
        self.set_enabled(False)
        self._worker.stop()
        self._worker.wait(2000)

    # --------------------------------------------------------
    # 键与路径
    # --------------------------------------------------------
    @staticmethod
    def _bbox_suffix(bbox):
        if not bbox:
            return ""
        s = bbox if isinstance(bbox, str) else json.dumps(bbox, sort_keys=True)
        return "_b" + hashlib.md5(s.encode("utf-8")).hexdigest()[:8]

    def _key(self, md5, size, bbox):
        return f"{md5}_{size}{self._bbox_suffix(bbox)}"

    def _cache_path(self, key, ext):
        return self._dir / key[:2] / f"{key}.{ext}"

    def _find_cached(self, key):
        """返回已存在的缓存文件路径（webp 优先，png 兜底），否则 None。"""
        p = self._cache_path(key, "webp")
        if p.is_file():
            return str(p)
        p = self._cache_path(key, "png")
        return str(p) if p.is_file() else None

    # --------------------------------------------------------
    # MD5 快速判定（内存缓存，mtime+size 未变则免重算）
    # --------------------------------------------------------
    def _md5_fast(self, path):
        try:
            st = os.stat(path)
        except OSError:
            return None
        with self._lock:
            rec = self._md5_cache.get(path)
            if rec and rec[0] == st.st_mtime_ns and rec[1] == st.st_size:
                return rec[2]
        return None

    def _md5_force(self, path):
        try:
            md5 = _content_md5(path)
        except OSError as e:
            print(f"[thumbcache] 读取失败 {path}: {e}")
            return None
        try:
            st = os.stat(path)
            with self._lock:
                self._md5_cache[path] = (st.st_mtime_ns, st.st_size, md5)
        except OSError:
            pass
        return md5

    # --------------------------------------------------------
    # 主线程 API
    # --------------------------------------------------------
    def get_cached(self, path, size, bbox=None):
        """同步检查：缓存存在且原图未变 → 返回缓存路径；否则 None。

        不读原图内容（仅 mtime/size 快速判定 + MD5 内存命中）。
        """
        if not self._enabled or not path or not os.path.isfile(path):
            return None
        size = self._normalize_size(size)
        md5 = self._md5_fast(path)
        if md5 is None:
            return None  # MD5 未知 → 需要后台线程计算
        return self._find_cached(self._key(md5, size, bbox))

    def request(self, path, size, bbox=None, on_ready=None):
        """请求缩略图：命中立即回调；未命中后台生成后回调。

        on_ready(cache_path) 在**主线程**被调用；失败/关闭时回调 None。
        """
        if not self._enabled or not path or not os.path.isfile(path):
            if on_ready:
                on_ready(None)
            return None
        size = self._normalize_size(size)
        md5 = self._md5_fast(path)
        if md5 is not None:
            cp = self._find_cached(self._key(md5, size, bbox))
            if cp:
                if on_ready:
                    on_ready(cp)
                return cp
        # 未命中 / MD5 未知 → 入队（worker 计算 MD5 后判定或生成）
        self._enqueue(path, size, bbox, on_ready)
        return None

    @staticmethod
    def _normalize_size(size):
        if size not in SUPPORTED_SIZES:
            return min(SUPPORTED_SIZES, key=lambda s: abs(s - size))
        return size

    def _task_key(self, path, size, bbox):
        # 归一化 bbox：PySide6 Signal(str) 跨线程投递会把 None 转成 ''，
        # 统一以 '' 表示「无裁剪」，保证入队/分发键一致
        return (path, size, bbox if bbox else "")

    def _enqueue(self, path, size, bbox, on_ready):
        task = self._task_key(path, size, bbox)
        with self._lock:
            if on_ready:
                self._pending.setdefault(task, []).append(on_ready)
            if task in self._inflight:
                return  # 已排队的同任务合并
            self._inflight.add(task)
        self._q.put((path, size, bbox))

    # --------------------------------------------------------
    # worker 线程执行
    # --------------------------------------------------------
    def _process(self, path, size, bbox):
        """在 worker 线程：算 MD5 → 命中磁盘缓存直接 ready → 否则生成。"""
        md5 = self._md5_fast(path)
        if md5 is None:
            md5 = self._md5_force(path)
        task = self._task_key(path, size, bbox)
        if md5 is None:
            with self._lock:
                self._inflight.discard(task)
            self.failed.emit(path, size, bbox)
            return
        resolved_key = self._key(md5, size, bbox)
        cp = self._find_cached(resolved_key)
        if cp is None:
            cp = self._generate(path, md5, size, bbox)
        with self._lock:
            self._inflight.discard(task)
        if cp is None:
            self.failed.emit(path, size, bbox)
            return
        self.ready.emit(path, size, bbox, cp)

    def _on_ready(self, path, size, bbox, cache_path):
        """主线程：按任务签名分发 ready 回调。"""
        with self._lock:
            callbacks = self._pending.pop(self._task_key(path, size, bbox), [])
        for cb in callbacks:
            try:
                cb(cache_path)
            except Exception as e:
                print(f"[thumbcache] ready 回调异常: {e}")

    def _on_failed(self, path, size, bbox):
        """主线程：生成失败 → 回调 None（调用方回退原图）。"""
        with self._lock:
            callbacks = self._pending.pop(self._task_key(path, size, bbox), [])
        for cb in callbacks:
            try:
                cb(None)
            except Exception:
                pass

    # --------------------------------------------------------
    # 生成（worker 线程）
    # --------------------------------------------------------
    def _generate(self, path, md5, size, bbox):
        """读取原图 → 按需裁剪 → 缩放 → 存 WebP（失败回退 PNG）。"""
        key = self._key(md5, size, bbox)
        probe = QImageReader(path)
        raw_size = probe.size()
        if not raw_size.isValid():
            return None
        transform = probe.transformation()

        reader = QImageReader(path)
        reader.setAutoTransform(True)
        if bbox:
            rect = _parse_bbox(bbox, raw_size.width(), raw_size.height())
            if rect is not None:
                raw_rect = _display_rect_to_raw(
                    rect, raw_size.width(), raw_size.height(), transform)
                if raw_rect is not None:
                    reader.setClipRect(raw_rect)
                    out_w = min(rect.width(), size)
                    out_h = min(rect.height(), size)
                    if out_w > 0 and out_h > 0:
                        reader.setScaledSize(QSize(out_w, out_h))
        else:
            # 整图：长边 = size，保持宽高比
            target = raw_size.scaled(size, size, Qt.KeepAspectRatio)
            if target.isValid() and target.width() > 0 and target.height() > 0:
                reader.setScaledSize(target)

        image = reader.read()
        if image.isNull():
            return None

        out_dir = self._dir / key[:2]
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"[thumbcache] 目录创建失败 {out_dir}: {e}")
            return None

        webp_path = str(self._cache_path(key, "webp"))
        ok = self._write_image(webp_path, image, "webp")
        if ok:
            return webp_path
        # WebP 写入失败 → PNG 兜底
        png_path = str(self._cache_path(key, "png"))
        if self._write_image(png_path, image, "png"):
            return png_path
        return None

    @staticmethod
    def _write_image(file_path, image, fmt):
        try:
            writer = QImageWriter(file_path, fmt.encode("ascii"))
            if not writer.canWrite():
                return False
            return writer.write(image)
        except Exception as e:
            print(f"[thumbcache] 写入失败 {file_path}: {e}")
            return False


# 模块级单例：各页面共享同一实例
thumbnail_cache = ThumbnailCache()
