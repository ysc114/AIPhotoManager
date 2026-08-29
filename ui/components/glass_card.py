"""
glass_card.py —— Glass 卡片（Liquid Glass 基础容器）

统一角色卡片 / 设置卡片 / 统计卡片的基础样式：
- 半透明玻璃底（透明度 / 圆角联动 ui.glass_opacity / ui.corner_radius）
- 顶部高光 + 边缘描边 + 阴影（联动 ui.shadow_strength / ui.glass_blur）
- Aurora 可选装饰层（aurora=True 时内嵌 AuroraGlassCard；
  aurora.enabled=False 时自动退化为普通玻璃，零额外开销）

用法与 QFrame 一致：
    card = GlassCard()
    card.setFixedSize(w, h)
    # 正常 addWidget 子控件
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect
from PySide6.QtGui import QColor

from config.settings_manager import settings as S
from ui.aurora_card import AuroraGlassCard


class GlassCard(QFrame):
    """Liquid Glass 卡片（玻璃底 + 可选 Aurora 装饰层）。"""

    def __init__(self, parent=None, aurora=True, radius=None):
        super().__init__(parent)
        self._use_aurora = bool(aurora)
        self._radius = radius  # None=跟随 ui.corner_radius

        self._aurora_layer = None
        if self._use_aurora:
            self._aurora_layer = AuroraGlassCard(self)
            self._aurora_layer.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self._apply_glass_style()
        self._apply_shadow()

    # --------------------------------------------------------
    # 样式
    # --------------------------------------------------------
    def radius(self):
        return self._radius if self._radius else int(S.get("ui.corner_radius", 18))

    def _apply_glass_style(self):
        ga = float(S.get("ui.glass_opacity", 0.55))
        r = self.radius()
        self.setStyleSheet("""
            QFrame {
                background: rgba(255,255,255,%f);
                border: 1px solid rgba(255,255,255,0.75);
                border-radius: %dpx;
            }
        """ % (ga, r))

    def _apply_shadow(self):
        s = max(0.0, float(S.get("ui.shadow_strength", 40)) / 40.0)
        b = max(0.2, float(S.get("ui.glass_blur", 30)) / 30.0)
        eff = QGraphicsDropShadowEffect(self)
        eff.setBlurRadius(max(1, int(22 * b)))
        eff.setOffset(0, 4)
        eff.setColor(QColor(30, 60, 110, int(40 * s)))
        self.setGraphicsEffect(eff)

    def refresh_glass(self):
        """玻璃参数变化后刷新（透明度 / 圆角 / 阴影）。"""
        self._apply_glass_style()
        self._apply_shadow()
        if self._aurora_layer:
            self._aurora_layer.update()

    # --------------------------------------------------------
    # 布局
    # --------------------------------------------------------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._aurora_layer:
            self._aurora_layer.setGeometry(self.rect())
