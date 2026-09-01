"""PyGlass (vendored) — physically-grounded refractive glass for PySide6.

上游：https://github.com/neomosh8/pyglass (MIT License, v0.3.0)
本副本为项目内 vendor 集成，仅保留 AIPhotoManager 需要的 in-app 折射引擎：
- GlassMaterial / GlassKernel  —— 物理折射核心（Snell / 色散 / Fresnel / frost）
- GlassRenderer / paint_glass —— 渲染与合成（可嵌入任意 paintEvent）
- WidgetBackdrop            —— 捕获宿主窗口自身渲染作为玻璃背景
- GlassStyle                —— 阴影 / 色调 / 光泽 / 边缘

已适配：PyQt6 import → PySide6；pyqtSignal → Signal。
不导入：桌面捕获（ScreenBackdrop/_magnifier/_screencapturekit）、
演示组件（GlassPane/GlassPopup/demo/desktop）——与本项目无关。
"""

from __future__ import annotations

__version__ = "0.3.0"

from .backdrop import Backdrop, WidgetBackdrop
from .effect import GlassRenderer, GlassStyle, paint_glass
from .refract import GlassKernel, GlassMaterial, qimage_to_array

__all__ = [
    "__version__",
    "Backdrop",
    "WidgetBackdrop",
    "GlassRenderer",
    "GlassStyle",
    "paint_glass",
    "GlassKernel",
    "GlassMaterial",
    "qimage_to_array",
]
