"""
toast.py —— 轻量浮动通知（替代 QMessageBox 的日常提示）

- 右下角浮动玻璃小卡：✓ 成功 / ℹ 提示 / ⚠ 警告
- 淡入 → 停留 → 淡出 + 轻微上滑，自动消失
- 多通知堆叠（新通知在上方）
- 单例 ToastManager：toast.show(parent, "已完成", kind="success")

用法：
    from ui.components.toast import toast
    toast.show(self, "已添加 12 张照片", kind="success")
"""

from PySide6.QtCore import Qt, QRectF, QPropertyAnimation, QEasingCurve, QTimer
from PySide6.QtGui import QPainter, QColor, QLinearGradient
from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout

_KINDS = {
    "success": ("✓", (70, 190, 130)),
    "info": ("ℹ", (110, 165, 255)),
    "warning": ("⚠", (240, 175, 80)),
}


class _ToastWidget(QWidget):
    """单条浮动通知卡。"""

    def __init__(self, text, kind="success", duration=2800):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(320)
        self._kind = kind
        icon, color = _KINDS.get(kind, _KINDS["info"])

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(10)

        badge = QLabel(icon)
        badge.setFixedSize(24, 24)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            f"font-size:14px;font-weight:800;color:rgb({color[0]},{color[1]},{color[2]});"
            f"background:rgba({color[0]},{color[1]},{color[2]},0.14);"
            "border-radius:12px;border:none;"
        )
        lay.addWidget(badge)

        msg = QLabel(text)
        msg.setStyleSheet(
            "font-size:12.5px;color:#3a4a5c;background:transparent;border:none;"
        )
        msg.setWordWrap(True)
        lay.addWidget(msg, 1)

        self._duration = duration
        self._fade = None

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        grad = QLinearGradient(0, 0, 0, rect.height())
        grad.setColorAt(0.0, QColor(250, 252, 255, 235))
        grad.setColorAt(1.0, QColor(238, 244, 252, 225))
        p.setBrush(grad)
        p.setPen(QColor(255, 255, 255, 200))
        p.drawRoundedRect(rect, 14, 14)
        # 左缘类型色条
        icon, color = _KINDS.get(self._kind, _KINDS["info"])
        p.setBrush(QColor(color[0], color[1], color[2], 190))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(rect.left() + 3, rect.top() + 8, 3, rect.height() - 16), 1.5, 1.5)
        # 顶部高光
        hi = QLinearGradient(0, rect.top(), 0, rect.top() + rect.height() * 0.5)
        hi.setColorAt(0.0, QColor(255, 255, 255, 130))
        hi.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(hi)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(rect, 14, 14)
        p.end()

    def show_with_animation(self):
        self.show()
        self.raise_()
        # 淡入
        self.setWindowOpacity(0.0)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(180)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade.start()

        # 停留后淡出 + 销毁
        QTimer.singleShot(self._duration, self._fade_out)

    def _fade_out(self):
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(280)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.InCubic)
        self._fade.finished.connect(self._cleanup)
        self._fade.start()

    def _cleanup(self):
        from ui.components.toast import toast
        toast._forget(self)
        self.deleteLater()


class ToastManager:
    """浮动通知管理器（右下角堆叠）。"""

    def __init__(self):
        self._stack = []

    def show(self, parent, text, kind="success", duration=2800):
        t = _ToastWidget(text, kind, duration)
        t.adjustSize()
        self._stack.append(t)
        self._relayout(parent)
        t.show_with_animation()

    def _forget(self, widget):
        if widget in self._stack:
            self._stack.remove(widget)
        # 通知消失后刷新剩余堆叠位置
        self._relayout(None, skip=widget)

    def _relayout(self, parent, skip=None):
        """把堆叠通知排到屏幕/窗口右下角（新在上方）。"""
        from PySide6.QtGui import QGuiApplication
        screen = None
        if parent is not None and parent.window() and parent.window().screen():
            screen = parent.window().screen()
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.right() - 340
        y_base = geo.bottom() - 20
        for w in reversed(self._stack):
            if w is skip or w.isHidden():
                continue
            y_base -= (w.height() + 10)
            w.move(int(x), int(y_base))


# 模块级单例
toast = ToastManager()
