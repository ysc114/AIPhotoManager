# core/auto_organizer.py
"""
自动分类模块
批量分析图片 → 查缓存 → 去重 → 按分类结果自动归档到子文件夹
"""

import os
import sys
import shutil
import hashlib
from pathlib import Path
from datetime import datetime

_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from core.ai_classifier import AIClassifier
from core.analysis_cache import get_cache
from config.labels import LABEL_MAP
from core.ai_advisor import AIAdvisor


def get_file_hash(filepath, chunk_size=8192):
    """计算文件 MD5 哈希"""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def auto_organize(
    image_paths,
    target_folder,
    mode="copy",
    remove_duplicates=True,
    progress_callback=None
):

    total = len(image_paths)
    classifier = AIClassifier()
    advisor = AIAdvisor()
    cache = get_cache()

    stats = {
        "success": 0,
        "failed": 0,
        "duplicates_skipped": 0,
        "cache_hits": 0,
        "categories": {},
        "errors": [],
    }

    existing_hashes = set()
    if remove_duplicates and os.path.exists(target_folder):
        if progress_callback:
            progress_callback(0, total, "正在扫描已有文件...")
        for root, dirs, files in os.walk(target_folder):
            for f in files:
                fpath = os.path.join(root, f)
                try:
                    existing_hashes.add(get_file_hash(fpath))
                except Exception:
                    pass

    for idx, image_path in enumerate(image_paths):
        try:
            if progress_callback:
                progress_callback(
                    idx + 1,
                    total,
                    f"正在分析：{os.path.basename(image_path)}"
                )

            if remove_duplicates:
                file_hash = get_file_hash(image_path)
                if file_hash in existing_hashes:
                    stats["duplicates_skipped"] += 1
                    continue
                existing_hashes.add(file_hash)

            # 查缓存：优先用缓存的修正分类
            cached_cn = cache.get_category_cn(image_path)

            if cached_cn is not None:
                # 缓存命中，直接用修正分类
                category_cn = cached_cn
                stats["cache_hits"] += 1
            else:
                # 缓存未命中，执行 AI 分析
                result = classifier.analyze(image_path)

                category_en = result.get("category", "未知")
                category_cn_raw = LABEL_MAP.get(category_en, category_en)
                quality = result.get("quality", 0) * 100
                scores = result.get("scores", {})

                advice = advisor.generate_ai_advice(category_en, category_cn_raw, quality, scores, image_path)
                category_cn = advice["category_cn"]

            dest_dir = os.path.join(target_folder, category_cn)
            os.makedirs(dest_dir, exist_ok=True)

            filename = os.path.basename(image_path)
            dest_path = os.path.join(dest_dir, filename)

            if os.path.exists(dest_path):
                name, ext = os.path.splitext(filename)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                dest_path = os.path.join(dest_dir, f"{name}_{timestamp}{ext}")

            if mode == "move":
                shutil.move(image_path, dest_path)
            else:
                shutil.copy2(image_path, dest_path)

            stats["success"] += 1
            stats["categories"][category_cn] = \
                stats["categories"].get(category_cn, 0) + 1

        except Exception as e:
            stats["failed"] += 1
            stats["errors"].append((image_path, str(e)))

            if progress_callback:
                progress_callback(
                    idx + 1,
                    total,
                    f"失败：{os.path.basename(image_path)} - {e}"
                )

    if progress_callback:
        progress_callback(total, total, "自动分类完成")

    return stats