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

    def __init__(self):
        self._cache = {}
        self._load_from_disk()

    def _load_from_disk(self):
        """从磁盘加载缓存"""
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
                print(f"[缓存] 从磁盘加载 {len(self._cache)} 条记录")
            except (json.JSONDecodeError, IOError):
                self._cache = {}

    def _save_to_disk(self):
        """保存缓存到磁盘"""
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, ensure_ascii=False, indent=2)

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
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)

    def get_category_cn(self, image_path):
        """获取人工修正后的分类中文名"""
        result = self.get(image_path)
        if result is None:
            return None
        return result.get("_category_cn")

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