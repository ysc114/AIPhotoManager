"""
animated_toggle.py —— 平滑动画开关（macOS / HyperOS 风格）

- 自绘轨道 + 圆点滑块
- 开启/关闭：圆点平滑滑动（QPropertyAnimation，OutCubic 150ms）
- 开启：轨道渐变填充强调色 + 圆点轻微阴影
- 接口兼容 QCheckBox 用法（isChecked / setChecked / toggled 信号）

替代 QCheckBox 用于设置中心的开关类设置。
"""

from PySide6.QtCore import Qt, QRectF, QPropertyAnimation, QEasingCurve, QPointF
from PySide6.QtGui import QPainter, QColor, QLinearGradient, QRadialGradient, QPen
from PySide6.QtWidgets import QAbstractButton


class AnimatedToggle(QAbstractButton):
    """平滑动画开关（点击切换 + 滑动动画）。"""

    def __init__(self, parent=None, checked=False):
        super().__init__(parent)
        self._pos = 1.0 if checked else 0.0   # 圆点位置 0..1
        self._anim = None
        self.setCheckable(True)
        # 统一用 toggled 信号驱动动画（覆盖 setChecked / click / 键盘 全部路径）
        self.toggled.connect(self._on_toggled)
        self.setChecked(bool(checked))
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(46, 26)

    def _on_toggled(self, checked):
        self._animate(float(checked))

    # --------------------------------------------------------
    # 尺寸
    # --------------------------------------------------------
    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(46, 26)

    # --------------------------------------------------------
    # 切换动画
    # --------------------------------------------------------
    def setChecked(self, checked):
        super().setChecked(checked)
        self.update()

    def _animate(self, target):
        self._anim = QPropertyAnimation(self, b"toggle_pos", self)
        self._anim.setDuration(160)
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(target)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_pos)
        self._anim.start()

    def _on_pos(self, v):
        self._pos = float(v)
        self.update()

    def toggle_pos(self):
        return self._pos

    def set_toggle_pos(self, v):
        self._pos = float(v)

    # --------------------------------------------------------
    # 绘制
    # --------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        track = QRectF(1, h / 2 - 8, w - 2, 16)

        # 轨道（关闭：浅灰玻璃；开启：渐变强调色）
        if self.isChecked():
            grad = QLinearGradient(track.left(), 0, track.right(), 0)
            grad.setColorAt(0.0, QColor(110, 160, 255, 235))
            grad.setColorAt(1.0, QColor(150, 120, 255, 225))
            p.setBrush(grad)
            p.setPen(Qt.NoPen)
        else:
            p.setBrush(QColor(150, 160, 175, 90))
            p.setPen(QPen(QColor(255, 255, 255, 120), 1.0))
        p.drawRoundedRect(track, 8, 8)

        # 圆点
        d = h - 6
        x = track.left() + 3 + self._pos * (track.width() - d - 6)
        y = h / 2 - d / 2
        # 圆点阴影
        p.setBrush(QColor(40, 60, 90, 50))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(x + 1, y + 2), d / 2, d / 2)
        # 圆点本体（白色渐变 + 顶部高光）
        grad = QRadialGradient(x + d * 0.35, y + d * 0.3, d)
        grad.setColorAt(0.0, QColor(255, 255, 255, 255))
        grad.setColorAt(0.7, QColor(240, 244, 250, 255))
        grad.setColorAt(1.0, QColor(228, 234, 244, 255))
        p.setBrush(grad)
        p.setPen(QPen(QColor(255, 255, 255, 200), 1.0))
        p.drawEllipse(QPointF(x, y), d / 2, d / 2)
        p.end()
