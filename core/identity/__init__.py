# core/identity/__init__.py
"""
身份识别与聚合模块 V4
统一管理真人、兽装角色、Cosplay角色的人物识别与聚合。

使用方式：
    from core.identity import IdentityManager
    manager = IdentityManager()
    groups = manager.analyze_folder(image_paths)
"""

# 公开 API 再导出（包内无引用，但外部大量 `from core.identity import ...`）
from core.identity.manager import IdentityManager, get_reader  # noqa: F401

__all__ = ["IdentityManager", "get_reader"]
