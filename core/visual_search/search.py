# core/visual_search/search.py
"""
视觉搜索统一入口（第 2 层 · 第一阶段）：
    search_by_image(image_path, top_k)   —— 以图搜图
    add_images(paths) / add_image(path)  —— 增量加入索引
    build_photo_index(photos_dir)        —— 一键构建/增量更新照片库索引

只读：不写 identity_db、不修改任何角色/分类数据。
"""

import os
from pathlib import Path

from core.visual_search.embedding import get_encoder, reset_encoder
from core.visual_search.index import get_index, reset_index

_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def default_photos_dir():
    return str(Path(__file__).resolve().parents[2] / "photos")


def add_images(image_paths, progress_cb=None, cache_dir=None, encoder=None):
    """增量加入（已索引复用 / MD5 重复跳过）。返回统计 dict。"""
    enc = encoder or get_encoder()
    idx = get_index(cache_dir=cache_dir) if cache_dir else get_index()
    return idx.add_images(list(image_paths), enc, progress_cb=progress_cb)


def add_image(image_path, encoder=None):
    return add_images([image_path], encoder=encoder)


def search_by_image(image_path, top_k=20, cache_dir=None, encoder=None):
    """以图搜图：返回 [{photo_id, path, similarity}] 按相似度降序。"""
    enc = encoder or get_encoder()
    idx = get_index(cache_dir=cache_dir) if cache_dir else get_index()
    return idx.search_by_image(image_path, enc, top_k=top_k)


def search_by_text(query_text, top_k=20, cache_dir=None, encoder=None):
    """自然语言搜索：文本 embedding 检索图片（与图像同一 CLIP 空间）。"""
    enc = encoder or get_encoder()
    idx = get_index(cache_dir=cache_dir) if cache_dir else get_index()
    return idx.search_by_text(query_text, enc, top_k=top_k)


def search_by_texts(texts, top_k=20, cache_dir=None, encoder=None):
    """多变体文本检索（调用方已自行扩展查询时使用）。

    单变体时与 search_by_text 等价；多变体按「每张照片最大相似度」融合。
    """
    enc = encoder or get_encoder()
    idx = get_index(cache_dir=cache_dir) if cache_dir else get_index()
    return idx.search_by_texts(list(texts or []), enc, top_k=top_k)


def build_photo_index(photos_dir=None, progress_cb=None):
    """扫描照片库并增量建立索引（首次全量、之后只算新照片）。"""
    d = photos_dir or default_photos_dir()
    if not os.path.isdir(d):
        return {"total": 0, "new": 0, "skipped_existing": 0,
                "skipped_md5": 0, "failed": 0}
    files = sorted(
        os.path.join(d, n) for n in os.listdir(d)
        if os.path.splitext(n)[1].lower() in _PHOTO_EXTS
    )
    return add_images(files, progress_cb=progress_cb)


def reset():
    """释放模型与索引单例（测试/升级用）。"""
    reset_encoder()
    reset_index()
