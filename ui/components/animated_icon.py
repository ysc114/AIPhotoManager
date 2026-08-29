"""
animated_icon.py —— 动态图标组件

四态（默认 / Hover / Press / Disabled），动画克制：
- Hover：轻微弹性放大（OutBack 120ms，~1.08×）+ 柔和 Aurora 高光 + 光泽扫过
- Press：轻微压缩（~0.92×）
- Disabled：降透明 + 淡灰色
- 不叠加夸张特效，单次动画 120~160ms

复用 ui/components/icons.py 的线性图标库。
"""

from PySide6.QtCore import Qt, QRectF, QEasingCurve, QVariantAnimation, QPointF, QTimer
from PySide6.QtGui import QPainter, QColor, QLinearGradient
from PySide6.QtWidgets import QWidget

from config.settings_manager import settings as S
from ui.components.icons import draw_icon, ICON_KEYS


class AnimatedIcon(QWidget):
    """动态线性图标（SF Symbols 风格，四态动画）。"""

    def __init__(self, icon_key="overview", size=28, parent=None, color=None):
        super().__init__(parent)
        assert icon_key in ICON_KEYS, f"未知图标: {icon_key}"
        self._key = icon_key
        self._hover = False
        self._pressed = False
        self._disabled = False
        self._scale = 1.0            # 当前缩放
        self._glow = 0.0             # Aurora 高光强度 0..1
        self._sheen = 0.0            # 光泽扫过位置 0..1（0=左 1=右）
        self._anim = None
        self._sheen_timer = None

        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_Hover, True)
        self.setMouseTracking(True)
        self._base_color = QColor(*color) if color else QColor(120, 132, 152)

    # --------------------------------------------------------
    # 状态接口
    # --------------------------------------------------------
    def set_icon(self, key):
        if key in ICON_KEYS:
            self._key = key
            self.update()

    def set_color(self, color):
        self._base_color = QColor(*color) if not isinstance(color, QColor) else color
        self.update()

    def set_disabled(self, disabled):
        self._disabled = bool(disabled)
        self.setEnabled(not disabled)
        self.update()

    # --------------------------------------------------------
    # 事件
    # --------------------------------------------------------
    def enterEvent(self, event):
        self._hover = True
        self._animate_scale(1.08)
        self._animate_glow(1.0)
        self._start_sheen()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._pressed = False
        self._animate_scale(1.0)
        self._animate_glow(0.0)
        self._stop_sheen()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self._animate_scale(0.92)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._pressed = False
        self._animate_scale(1.08 if self._hover else 1.0)
        super().mouseReleaseEvent(event)

    def _animate_scale(self, target):
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(self._scale)
        self._anim.setEndValue(target)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve.OutBack)
        self._anim.valueChanged.connect(self._on_scale)
        self._anim.start()

    def _on_scale(self, v):
        self._scale = float(v)
        self.update()

    def _animate_glow(self, target):
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(self._glow)
        self._anim.setEndValue(target)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_glow)
        self._anim.start()

    def _on_glow(self, v):
        self._glow = float(v)
        self.update()

    def _start_sheen(self):
        self._sheen = 0.0
        self._sheen_timer = QTimer(self)
        self._sheen_timer.setInterval(18)
        self._sheen_timer.timeout.connect(self._sheen_tick)
        self._sheen_timer.start()

    def _sheen_tick(self):
        self._sheen += 0.06
        self.update()
        if self._sheen >= 1.0:
            self._stop_sheen()

    def _stop_sheen(self):
        if self._sheen_timer:
            self._sheen_timer.stop()
            self._sheen_timer.deleteLater()
            self._sheen_timer = None
        self._sheen = 0.0

    # --------------------------------------------------------
    # 绘制
    # --------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = self.size()
        center = QPointF(s.width() / 2, s.height() / 2)

        # 缩放
        sc = self._scale * (0.9 if self._pressed else 1.0)
        icon_size = s.width() * 0.82 * sc
        rect = QRectF(center.x() - icon_size / 2, center.y() - icon_size / 2,
                      icon_size, icon_size)

        # 颜色：disabled 降饱和，hover 提亮
        if self._disabled:
            color = QColor(170, 176, 188)
        elif self._hover:
            color = self._base_color.lighter(120)
        else:
            color = self._base_color

        # Aurora 柔和高光（参数联动）
        if self._glow > 0.01 and S.get("aurora.enabled", True) and not self._disabled:
            glow_r = s.width() * 0.5
            from PySide6.QtGui import QRadialGradient
            g = QRadialGradient(center, glow_r)
            intensity = float(S.get("aurora.intensity", 0.55))
            g.setColorAt(0.0, QColor(140, 170, 255, int(60 * self._glow * intensity)))
            g.setColorAt(1.0, QColor(140, 170, 255, 0))
            p.setBrush(g)
            p.setPen(Qt.NoPen)
            p.drawEllipse(center, glow_r, glow_r)

        draw_icon(p, rect, self._key, color,
                  emphasized=self._hover and not self._disabled)

        # 光泽扫过（hover 时从左上到右下一次，克制）
        if self._sheen > 0 and self._sheen < 1.0 and not self._disabled:
            x0 = rect.left() + (rect.width() * self._sheen) - rect.width() * 0.3
            sheen = QLinearGradient(x0, rect.top(), x0 + rect.width() * 0.35, rect.top())
            sheen.setColorAt(0.0, QColor(255, 255, 255, 0))
            sheen.setColorAt(0.5, QColor(255, 255, 255, 120))
            sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(sheen)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(rect, rect.height() / 3, rect.height() / 3)

        p.end()
