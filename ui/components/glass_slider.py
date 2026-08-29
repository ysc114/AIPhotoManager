"""
glass_slider.py —— 自绘 Glass 滑块（设置中心实时预览）

- 圆角轨道 + 已填充渐变（强调色）+ 圆点手柄（带阴影）
- 拖动中实时发 valueChanged（与 QSlider 完全兼容）
- 样式与 Liquid Glass 统一（联动 ui.glass_opacity / ui.corner_radius）

替代 QSlider 用于设置中心滑块类设置。
"""

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QLinearGradient, QRadialGradient, QPen
from PySide6.QtWidgets import QSlider



class GlassSlider(QSlider):
    """Liquid Glass 滑块（继承 QSlider，接口完全兼容）。"""

    def __init__(self, orientation=Qt.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(26)

    # --------------------------------------------------------
    # 几何
    # --------------------------------------------------------
    def _groove(self):
        w = self.width()
        h = self.height()
        if self.orientation() == Qt.Horizontal:
            return QRectF(4, h / 2 - 3, w - 8, 6)
        return QRectF(w / 2 - 3, 4, 6, h - 8)

    def _handle_center(self, groove):
        ratio = (self.value() - self.minimum()) / max(1, self.maximum() - self.minimum())
        if self.orientation() == Qt.Horizontal:
            x = groove.left() + ratio * groove.width()
            return QPointF(x, groove.center().y())
        y = groove.top() + ratio * groove.height()
        return QPointF(groove.center().x(), y)

    def _filled(self, groove):
        ratio = (self.value() - self.minimum()) / max(1, self.maximum() - self.minimum())
        if self.orientation() == Qt.Horizontal:
            return QRectF(groove.left(), groove.top(), groove.width() * ratio, groove.height())
        return QRectF(groove.left(), groove.top() + groove.height() * (1 - ratio),
                      groove.width(), groove.height() * ratio)

    # --------------------------------------------------------
    # 绘制
    # --------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        groove = self._groove()

        # 轨道底（浅灰玻璃）
        p.setBrush(QColor(140, 152, 170, 80))
        p.setPen(QPen(QColor(255, 255, 255, 90), 1.0))
        p.drawRoundedRect(groove, 3, 3)

        # 已填充（渐变强调色）
        filled = self._filled(groove)
        if filled.width() > 0.5 and filled.height() > 0.5:
            grad = QLinearGradient(filled.left(), 0, filled.right(), 0)
            grad.setColorAt(0.0, QColor(110, 165, 255, 235))
            grad.setColorAt(1.0, QColor(150, 125, 255, 225))
            p.setBrush(grad)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(filled, 3, 3)

        # 手柄（圆点 + 阴影 + 高光）
        center = self._handle_center(groove)
        r = 7.0
        p.setBrush(QColor(30, 50, 80, 45))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(center.x() + 1, center.y() + 1.5), r, r)
        grad = QRadialGradient(center.x() - 2, center.y() - 2, r * 2)
        grad.setColorAt(0.0, QColor(255, 255, 255, 255))
        grad.setColorAt(0.7, QColor(240, 244, 250, 255))
        grad.setColorAt(1.0, QColor(225, 232, 244, 255))
        p.setBrush(grad)
        p.setPen(QPen(QColor(255, 255, 255, 200), 1.0))
        p.drawEllipse(center, r, r)
        p.end()
