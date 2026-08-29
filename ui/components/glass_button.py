"""
glass_button.py —— Liquid Glass 按钮

- 半透明玻璃渐变背景 + 边缘高光
- Hover：高光增强 + 轻微上浮阴影（Aurora 光晕可选）
- Press：向下微压（几何 +1px / 亮度变化）+ 释放回弹（OutBack）
- 三种状态：normal（白玻璃） / accent（强调渐变） / danger（危险红）
- 动画克制：短时长、只作用于视觉层

用法与 QPushButton 一致（继承自 QPushButton，测试 findChildren 兼容）：
    btn = GlassButton("✨ 开始", variant="accent")
"""

from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPointF
from PySide6.QtGui import QPainter, QColor, QLinearGradient, QPen, QFont
from PySide6.QtWidgets import QPushButton

from config.settings_manager import settings as S

# 三种状态配色
_VARIANTS = {
    "normal": {
        "grad": ((255, 255, 255, 235), (235, 240, 250, 180)),
        "text": (60, 76, 100, 255),
        "hover_grad": ((255, 255, 255, 255), (230, 240, 252, 210)),
        "border": (255, 255, 255, 200),
    },
    "accent": {
        "grad": ((120, 160, 255, 235), (150, 130, 255, 220)),
        "text": (255, 255, 255, 255),
        "hover_grad": ((130, 170, 255, 255), (160, 140, 255, 235)),
        "border": (255, 255, 255, 160),
    },
    "danger": {
        "grad": ((255, 150, 150, 235), (240, 110, 120, 220)),
        "text": (255, 255, 255, 255),
        "hover_grad": ((255, 165, 165, 255), (245, 120, 130, 240)),
        "border": (255, 255, 255, 160),
    },
}


class GlassButton(QPushButton):
    """Liquid Glass 按钮（继承 QPushButton，接口完全兼容）。"""

    def __init__(self, text="", parent=None, variant="normal", aurora=True):
        super().__init__(text, parent)
        self._variant = variant if variant in _VARIANTS else "normal"
        self._use_aurora = bool(aurora)
        self._hover = False
        self._pressed_anim = 0.0      # 0=常态 1=按下
        self._press_anim = None

        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "QPushButton{border:none;background:transparent;}"
        )

    # --------------------------------------------------------
    # 事件（驱动动画）
    # --------------------------------------------------------
    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._animate_press(1.0)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._animate_press(0.0)
        super().mouseReleaseEvent(event)

    def _animate_press(self, target):
        self._press_anim = QPropertyAnimation(self, b"_pressed", self)
        self._press_anim.setDuration(130)
        self._press_anim.setStartValue(self._pressed_anim)
        self._press_anim.setEndValue(target)
        self._press_anim.setEasingCurve(QEasingCurve.OutBack)
        self._press_anim.valueChanged.connect(lambda v: (setattr(self, "_pressed_anim", float(v)), self.update()))
        self._press_anim.start()

    # --------------------------------------------------------
    # 绘制
    # --------------------------------------------------------
    def paintEvent(self, event):
        v = _VARIANTS[self._variant]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            p.end()
            return

        radius = min(h / 2, 16.0)
        rect = self.rect()
        # 按下微压：整体下移 1px + 亮度略降
        press = self._pressed_anim
        dy = int(1.0 * press)
        rect.moveTop(rect.top() + dy)

        # ── Aurora 光晕（可选，hover 时跟随鼠标，参数联动）──
        if self._use_aurora and S.get("aurora.enabled", True) and self._hover:
            self._draw_aurora_glow(p, rect, press)

        # ── 玻璃渐变底 ──
        if self._hover and press < 0.6:
            grad = QLinearGradient(0, 0, w, h)
            c1, c2 = v["hover_grad"]
        else:
            grad = QLinearGradient(0, 0, w, h)
            c1, c2 = v["grad"]
        # 按下时整体亮度微降
        if press > 0.01:
            c1 = tuple(int(x * (1 - 0.12 * press)) if i < 3 else x for i, x in enumerate(c1))
            c2 = tuple(int(x * (1 - 0.12 * press)) if i < 3 else x for i, x in enumerate(c2))
        grad.setColorAt(0.0, QColor(*c1))
        grad.setColorAt(1.0, QColor(*c2))
        p.setBrush(grad)
        p.setPen(QPen(QColor(*v["border"]), 1.0))
        p.drawRoundedRect(rect, radius, radius)

        # ── 顶部高光 ──
        hi = QLinearGradient(0, rect.top(), 0, rect.top() + h * 0.5)
        hi.setColorAt(0.0, QColor(255, 255, 255, 90 + (50 if self._hover else 0)))
        hi.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(hi)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(rect, radius, radius)

        # ── 文本 ──
        p.setPen(QColor(*v["text"]))
        f = QFont(self.font())
        f.setPixelSize(max(11, self.height() // 3))
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.drawText(rect, Qt.AlignCenter, self.text())
        p.end()

    def _draw_aurora_glow(self, p, rect, press):
        """hover 时按钮内部的柔和 Aurora 光晕（跟随鼠标，克制冷色）。"""
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QCursor, QRadialGradient
        local = self.mapFromGlobal(QCursor.pos())
        if not rect.contains(local):
            local = QPoint(rect.center())
        intensity = float(S.get("aurora.intensity", 0.55)) * (1.0 - 0.4 * press)
        for (dx, dy, rr), col in (
            ((-0.2, -0.1, 0.55), (90, 170, 255)),
            ((0.2, 0.1, 0.55), (150, 120, 255)),
            ((0.0, 0.25, 0.5), (255, 130, 190)),
        ):
            c = QPointF(local.x() + dx * rect.width(), local.y() + dy * rect.height())
            g = QRadialGradient(c, rr * rect.width())
            g.setColorAt(0.0, QColor(col[0], col[1], col[2], int(70 * intensity)))
            g.setColorAt(1.0, QColor(col[0], col[1], col[2], 0))
            p.setBrush(g)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(rect, min(rect.height() / 2, 16.0), min(rect.height() / 2, 16.0))
