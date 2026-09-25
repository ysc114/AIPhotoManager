# core/analysis_cache.py
"""
AI 分析结果统一缓存（内存 + 磁盘持久化）。
程序重启后缓存不丢失。
"""

import json
import os
from pathlib import Path
from datetime import datetime


CACHE_FILE = str(Path(__file__).resolve().parents[1] / "analysis_cache.json")


class AnalysisCache:
    """分析结果缓存（内存 + 磁盘）"""

    def __init__(self, cache_file=None):
        # cache_file 显式传入时只读写该路径（测试隔离；避免误写生产缓存）
        self.cache_file = str(cache_file) if cache_file else CACHE_FILE
        self._cache = {}
        self._load_from_disk()

    def _quarantine_corrupt(self, err):
        """把损坏的缓存文件改名留存（不静默丢弃人工分类）。"""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = f"{self.cache_file}.corrupt-{stamp}.bak"
        try:
            os.replace(self.cache_file, target)
            print(f"[缓存] 读取失败（{err}），原文件已留存为 {os.path.basename(target)}")
            return target
        except OSError as e:
            print(f"[缓存] 留存损坏文件失败：{e}")
            return ""

    def _load_from_disk(self):
        """从磁盘加载缓存；损坏时先另存为 .corrupt-*.bak，不静默覆盖。"""
        if not os.path.exists(self.cache_file):
            return
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("缓存结构不是对象")
            self._cache = data
            print(f"[缓存] 从磁盘加载 {len(self._cache)} 条记录")
        except (ValueError, OSError) as e:
            self._cache = {}
            self._quarantine_corrupt(e)

    def _save_to_disk(self):
        """保存缓存（临时文件 + 原子替换）。

        直接覆盖写在中途被杀 / 断电 / 同步盘打断时会留下半截 JSON，
        下次启动按空缓存加载，紧接着一次保存就把人工分类覆盖没了。
        """
        tmp = self.cache_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.cache_file)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def get(self, image_path):
        """获取缓存的分析结果"""
        path = image_path.replace("\\", "/")
        return self._cache.get(path)

    def set(self, image_path, result):
        """保存分析结果"""
        path = image_path.replace("\\", "/")

        # 只保留必要字段，不存大对象
        clean_result = {
            "category": result.get("category"),
            "quality": result.get("quality"),
            "scores": result.get("scores"),
            # Stage 4C (P2): 补存三级分类结果。
            # - 旧缓存条目没有这些键，读取侧 .get() 返回 None，向后兼容
            # - layer2 对非兽装图片合法为 None，持久化为 JSON null
            "layer1": result.get("layer1"),
            "layer2": result.get("layer2"),
            "layer3": result.get("layer3"),
            "_cached_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        # 保留已有的修正分类
        existing = self._cache.get(path)
        if existing and existing.get("_category_cn"):
            clean_result["_category_cn"] = existing["_category_cn"]

        self._cache[path] = clean_result
        self._save_to_disk()

    def has(self, image_path):
        """检查是否已有缓存"""
        path = image_path.replace("\\", "/")
        return path in self._cache

    def clear(self):
        """清空缓存"""
        self._cache.clear()
        if os.path.exists(self.cache_file):
            os.remove(self.cache_file)

    def get_category_cn(self, image_path):
        """获取人工修正后的分类中文名"""
        result = self.get(image_path)
        if result is None:
            return None
        return result.get("_category_cn")

    def remove(self, image_path):
        """删除单个路径的缓存（重复照片清理用）；不存在返回 False。"""
        path = image_path.replace("\\", "/")
        if path in self._cache:
            del self._cache[path]
            self._save_to_disk()
            return True
        return False

    def stale_count(self):
        """指向已不存在文件的条目数（含空条目；只读，不写盘）。"""
        return sum(1 for k, v in self._cache.items()
                   if not v or (k and not os.path.exists(k)))

    def prune_missing(self):
        """删除失效缓存条目（文件已删除 / 空条目），返回统计。

        只清理缓存自身的键，不删除任何照片文件；下次分析会自动重建。
        """
        stale = [k for k in list(self._cache) if k and not os.path.exists(k)]
        empty = [k for k, v in list(self._cache.items()) if not v]
        removed = set(stale) | set(empty)
        for k in removed:
            self._cache.pop(k, None)
        if removed:
            self._save_to_disk()
        return {"stale": len(stale), "empty": len(empty),
                "removed": len(removed), "kept": len(self._cache)}

    def set_category_cn(self, image_path, category_cn):
        """设置人工修正后的分类名"""
        path = image_path.replace("\\", "/")
        if path in self._cache:
            self._cache[path]["_category_cn"] = category_cn
        else:
            self._cache[path] = {
                "_category_cn": category_cn,
                "_cached_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        self._save_to_disk()


_cache_instance = None


def get_cache():
    """获取全局缓存实例"""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = AnalysisCache()
    return _cache_instance