# core/visual_search/__init__.py
"""
视觉搜索基础设施（智能搜索第 2 层 · 第一阶段）

独立于角色识别系统：不 import core.identity / model_hub，
不写 identity_db，不影响 Fursee / Face / 聚类 / MD5 / incremental_assign。

模块：
- embedding.py  OpenCLIP 图像编码（GPU/CPU、归一化、单例复用）
- index.py      FAISS IndexFlatIP + metadata（cache/visual_search/，
                增量更新 + MD5 去重 + 模型一致性校验）
- search.py     统一入口：add_images / search_by_image / build_photo_index
"""

from core.visual_search.embedding import (
    ClipImageEncoder, get_encoder, reset_encoder, resolve_device,
)
from core.visual_search.index import (
    VisualSearchIndex, VisualSearchModelMismatch, get_index, reset_index,
)
from core.visual_search.search import (
    add_image, add_images, build_photo_index, search_by_image, reset,
)

__all__ = [
    "ClipImageEncoder", "get_encoder", "reset_encoder", "resolve_device",
    "VisualSearchIndex", "VisualSearchModelMismatch", "get_index", "reset_index",
    "add_image", "add_images", "build_photo_index", "search_by_image", "reset",
]
