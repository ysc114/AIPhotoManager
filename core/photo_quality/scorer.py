"""scorer.py —— 照片质量评分入口 + 独立缓存。

- PhotoQualityAnalyzer：单角色分析（评分 + 近似分组 + AI 精选）
- 缓存：cache/photo_quality.json（与 identity 数据完全分离）
- 已分析照片（文件 mtime/size 未变）直接复用缓存，不重复计算
- 全程只读照片文件，不写 identity_db，不删除任何照片
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path

from PIL import Image

from . import __version__
from .technical import technical_metrics, technical_score
from .aesthetic import aesthetic_score
from .duplicate import group_duplicates
from .selector import select_best

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CACHE_FILE = _PROJECT_ROOT / "cache" / "photo_quality.json"

# 总分合成权重
_AESTHETIC_W = 0.55
_TECH_W = 0.45


class PhotoQualityAnalyzer:
    """照片质量分析器（线程安全：单次 analyze 全程不共享可变状态）。"""

    def __init__(self, cache_file=None, use_clip=False):
        self.cache_file = Path(cache_file) if cache_file else DEFAULT_CACHE_FILE
        self.use_clip = bool(use_clip)
        self._lock = threading.Lock()
        self._cache = self._load()

    # --------------------------------------------------------
    # 缓存
    # --------------------------------------------------------
    def _load(self):
        try:
            if self.cache_file.is_file():
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[photo_quality] 缓存加载失败，使用空缓存: {e}")
        return {}

    def _save(self):
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(self.cache_file) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.cache_file)
        except OSError as e:
            print(f"[photo_quality] 缓存保存失败: {e}")

    @staticmethod
    def _file_sig(path):
        try:
            st = os.stat(path)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _photo_ok(self, rec, path):
        """缓存记录是否仍有效（文件未变 + 版本一致）。"""
        if not rec or rec.get("version") != __version__:
            return False
        sig = self._file_sig(path)
        return sig is not None and rec.get("sig") == list(sig)

    # --------------------------------------------------------
    # 单张分析
    # --------------------------------------------------------
    def analyze_photo(self, path):
        """分析单张照片 → {score, aesthetic, technical, reason?, version, sig}。

        失败（无法解码）返回 None（调用方跳过）。
        技术指标在 ~640px 内计算即可，用 draft 缩略解码提速（JPEG 显著）。
        """
        try:
            img = Image.open(path)
            if hasattr(img, "draft"):
                img.draft("RGB", (640, 640))
            img.load()
            img = img.convert("RGB")
        except Exception as e:
            print(f"[photo_quality] 无法读取 {path}: {e}")
            return None
        metrics = technical_metrics(img)
        a = aesthetic_score(img, metrics=metrics, use_clip=self.use_clip)
        t = technical_score(metrics)
        score = round(_AESTHETIC_W * a + _TECH_W * t, 4)
        sig = self._file_sig(path)
        return {
            "path": path,
            "score": score,
            "aesthetic": a,
            "technical": metrics,
            "technical_score": t,
            "version": __version__,
            "sig": list(sig) if sig else None,
            "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    # --------------------------------------------------------
    # 角色分析（入口）
    # --------------------------------------------------------
    def analyze_role(self, role_id, photos, force=False, progress_callback=None):
        """分析单个角色：评分 + 近似分组 + AI 精选。

        photos: [本地照片路径, ...]（该角色组内照片）
        force: True 忽略缓存全量重算
        progress_callback(done, total): 可选进度回调（逐张分析时调用）
        返回 {"picks":[...], "groups":[...], "total":N, "analyzed":n, "cached":n}
        纯计算，不写任何数据库。
        """
        role_id = str(role_id)
        role_photos = {}
        need = []

        for p in photos:
            rec = None if force else (self._cache.get(role_id, {}).get("photos", {}).get(p))
            if not force and rec and self._photo_ok(rec, p):
                role_photos[p] = rec
            else:
                need.append(p)

        total_need = len(need)
        for i, p in enumerate(need):
            rec = self.analyze_photo(p)
            if rec:
                role_photos[p] = rec
            if progress_callback:
                try:
                    progress_callback(i + 1, total_need)
                except Exception:
                    pass

        # 近似分组（用缩略解码，避免二次全量解码大图）
        items = []
        for p, rec in role_photos.items():
            if rec is None:
                continue
            try:
                img = Image.open(p)
                if hasattr(img, "draft"):
                    img.draft("RGB", (128, 128))
                img.load()
                img = img.convert("RGB")
                items.append((p, img))
            except Exception:
                continue
        groups = group_duplicates(items)

        # 组 → 代表 & 精选
        result = select_best(role_photos, groups)

        # 写缓存
        with self._lock:
            entry = self._cache.setdefault(role_id, {})
            entry["photos"] = {p: rec for p, rec in role_photos.items() if rec}
            entry["picks"] = result["picks"]
            entry["groups"] = result["groups"]
            entry["total"] = result["total"]
            entry["analyzed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry["version"] = __version__
            self._save()

        result["analyzed"] = len(need)
        result["cached"] = len(photos) - len(need)
        return result

    # --------------------------------------------------------
    # 只读查询
    # --------------------------------------------------------
    def get_role_result(self, role_id):
        """返回该角色缓存的分析结果（未分析返回 None）。"""
        entry = self._cache.get(str(role_id))
        if not entry or entry.get("version") != __version__:
            return None
        return entry

    def has_role(self, role_id):
        return self.get_role_result(role_id) is not None


# 模块级共享实例（各页面共用同一缓存）
_analyzer = None


def get_analyzer(use_clip=False):
    global _analyzer
    if _analyzer is None:
        _analyzer = PhotoQualityAnalyzer(use_clip=use_clip)
    return _analyzer
