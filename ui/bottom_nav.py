"""
BottomGlassNav —— 底部 Liquid Glass 悬浮导航栏（液态选中胶囊）

Apple Liquid Glass + 小米 HyperOS 极光风格：
- 悬浮玻璃底（半透明 + 渐变高光 + 边缘描边 + 阴影由外部挂）
- 玻璃内部 Aurora 彩色流动（复用 AuroraGlassCard，参数联动，
  aurora.enabled=False 时零极光零动画）
- 液态选中胶囊：切换页面时胶囊像液体一样滑动——OutBack 缓动 +
  移动中轻微拉伸 + 到达回弹 + 恢复正常圆角
- Hover：图标轻微放大 + 玻璃高光 + Aurora 跟随鼠标
- 线性图标自绘（SF Symbols / HyperOS 风格，非 Emoji）
- 响应式：窗口变窄自动缩小间距 / 只显示图标

纯 UI 组件：只发 page_changed 信号，不含任何业务逻辑。
"""

import math

from PySide6.QtCore import Qt, QPointF, QRectF, QEasingCurve, QVariantAnimation, Signal
from PySide6.QtGui import QPainter, QLinearGradient, QRadialGradient, QColor, QPen, QFont
from PySide6.QtWidgets import QWidget

from config.settings_manager import settings as S
from ui.aurora_card import AuroraGlassCard
from ui.components.icons import draw_icon

# 导航项： (key, 名称)（与 content_stack 页面索引一一对应）
DEFAULT_ENTRIES = [
    ("overview", "总览"),
    ("ai_pick", "AI精选"),
    ("photo", "照片"),
    ("fursuit", "兽装"),
    ("person", "人物"),
    ("character", "角色"),
    ("favorites", "收藏"),
    ("pending", "待处理"),
    ("settings", "设置"),
]

# 液态效果档位 → 拉伸峰值(px) × 强度
_EFFECT_STRETCH = {"soft": 0.0, "standard": 9.0, "vivid": 16.0}


class BottomGlassNav(QWidget):
    """底部悬浮液态导航栏。

    page_changed(int)：用户点击某导航项（与 content_stack 索引一致）。
    """

    page_changed = Signal(int)

    def __init__(self, entries=None, parent=None):
        super().__init__(parent)
        self._entries = entries or DEFAULT_ENTRIES
        self._n = len(self._entries)
        self._current = 0
        self._hover = -1
        self._hover_progress = 0.0        # hover 缩放进度 0..1
        self._capsule = (0.0, 0.0)        # 当前胶囊 (x, w)
        self._capsule_anim = None
        self._hover_anim = None

        self.setAttribute(Qt.WA_Hover, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

        # ── Aurora 玻璃层（参数联动：关闭后零极光零 timer）──
        self._aurora = AuroraGlassCard(self)
        self._aurora.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        # ── 悬浮投影：透明底 + 圆角投影跟随玻璃形状（Dock 悬浮感）──
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        from PySide6.QtWidgets import QGraphicsDropShadowEffect
        _sh = QGraphicsDropShadowEffect(self)
        _sh.setBlurRadius(26)
        _sh.setOffset(0, 9)
        _sh.setColor(QColor(28, 55, 105, 110))
        self.setGraphicsEffect(_sh)

        # 导航参数自监听：修改立即生效；销毁时注销
        self._nav_cb = S.on_change("nav", self._on_nav_cfg)
        self.destroyed.connect(self._on_destroyed)

        # 初始胶囊位置
        r = self._capsule_rect(self._current)
        self._capsule = (r.x(), r.width())

    def _on_nav_cfg(self, key, value):
        if key in ("nav.animation", "nav.animation_strength", "nav.liquid_effect"):
            if self._capsule_anim is None or self._capsule_anim.state() != QVariantAnimation.Running:
                r = self._capsule_rect(self._current)
                self._capsule = (r.x(), r.width())
        self.update()

    def _on_destroyed(self):
        S.off_change("nav", self._nav_cb)

    # --------------------------------------------------------
    # 配置
    # --------------------------------------------------------
    def _cfg(self):
        return dict(
            show_text=bool(S.get("nav.show_text", True)),
            animation=bool(S.get("nav.animation", True)),
            strength=float(S.get("nav.animation_strength", 1.0)),
            effect=str(S.get("nav.liquid_effect", "standard")),
            aurora=bool(S.get("aurora.enabled", True)),
        )

    def _show_text(self):
        cfg = self._cfg()
        if not cfg["show_text"]:
            return False
        return self.width() >= self._n * 78  # 空间不足时只显示图标

    # --------------------------------------------------------
    # 几何
    # --------------------------------------------------------
    def _item_rect(self, i):
        w = self.width()
        iw = w / self._n
        return QRectF(i * iw, 0, iw, self.height())

    def _capsule_rect(self, i):
        r = self._item_rect(i)
        return QRectF(r.x() + 9.0, 7.0, r.width() - 18.0, self.height() - 14.0)

    def _hit_item(self, pos):
        if self.width() <= 0:
            return -1
        x = pos.x()
        if x < 0 or x >= self.width():
            return -1
        return min(self._n - 1, int(x / (self.width() / self._n)))

    # --------------------------------------------------------
    # 公开接口
    # --------------------------------------------------------
    def set_current(self, idx, animate=True):
        """切换选中项（液态胶囊动画）。"""
        if not (0 <= idx < self._n) or idx == self._current:
            if 0 <= idx < self._n:
                r = self._capsule_rect(idx)
                self._capsule = (r.x(), r.width())
                self.update()
            return
        old_rect = self._capsule_rect(self._current)
        new_rect = self._capsule_rect(idx)
        self._current = idx

        cfg = self._cfg()
        if not animate or not cfg["animation"]:
            self._capsule = (new_rect.x(), new_rect.width())
            self.update()
            return

        # 液态滑动：OutBack 缓动 + 移动中拉伸 + 到达回弹
        ease = QEasingCurve(QEasingCurve.OutBack)
        stretch = _EFFECT_STRETCH.get(cfg["effect"], 9.0) * max(0.2, cfg["strength"])
        dur = int(400 * max(0.5, cfg["strength"]))

        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(dur)
        anim.valueChanged.connect(lambda t: self._on_capsule_tick(
            float(t), old_rect, new_rect, ease, stretch))
        anim.finished.connect(lambda a=anim: self._on_anim_finished(a))
        self._capsule_anim = anim
        anim.start()

    def _on_anim_finished(self, anim):
        """动画结束：清理引用（避免访问已删除的 C++ 对象）并补齐胶囊。"""
        if self._capsule_anim is anim:
            self._capsule_anim = None
        r = self._capsule_rect(self._current)
        self._capsule = (r.x(), r.width())
        self.update()
        anim.deleteLater()

    def _on_capsule_tick(self, t, old, new, ease, stretch):
        e = ease.valueForProgress(min(1.0, max(0.0, t)))
        x = old.x() + (new.x() - old.x()) * e
        peak = math.sin(math.pi * min(1.0, t)) * stretch
        w = old.width() + (new.width() - old.width()) * e + peak
        w = max(w, new.width() - 22.0)  # 不窄于目标过多
        self._capsule = (x, w)
        self.update()

    # --------------------------------------------------------
    # 事件
    # --------------------------------------------------------
    def enterEvent(self, event):
        super().enterEvent(event)

    def hoverMoveEvent(self, event):
        self._set_hover(self._hit_item(event.pos()))
        self._drive_aurora(event.pos())
        super().hoverMoveEvent(event)

    def mouseMoveEvent(self, event):
        self._set_hover(self._hit_item(event.pos()))
        self._drive_aurora(event.pos())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._set_hover(-1)
        if self._cfg()["aurora"]:
            self._aurora._hovering = False
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            idx = self._hit_item(event.pos())
            if idx >= 0:
                self.page_changed.emit(idx)
                return
        super().mousePressEvent(event)

    def _set_hover(self, idx):
        if idx == self._hover:
            return
        self._hover = idx
        # hover 缩放进度动画
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setStartValue(0.0)
        self._hover_anim.setEndValue(1.0 if idx >= 0 else 0.0)
        self._hover_anim.setDuration(120)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover_tick)
        self._hover_anim.start()

    def _on_hover_tick(self, v):
        self._hover_progress = float(v)
        self.update()

    def _drive_aurora(self, pos):
        cfg = self._cfg()
        if not cfg["aurora"]:
            return
        local = self._aurora.mapFrom(self, pos)
        self._aurora._hovering = True
        self._aurora._target = QPointF(local)
        self._aurora._timer.start()

    # --------------------------------------------------------
    # 布局
    # --------------------------------------------------------
    def resizeEvent(self, event):
        self._aurora.setGeometry(self.rect())
        # 重置胶囊到当前项（避免布局变化时动画错位）
        if self._capsule_anim is None or self._capsule_anim.state() != QVariantAnimation.Running:
            r = self._capsule_rect(self._current)
            self._capsule = (r.x(), r.width())
        super().resizeEvent(event)

    # --------------------------------------------------------
    # 绘制
    # --------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            p.end()
            return

        show_text = self._show_text()

        # ── 液态选中胶囊（多层玻璃：渐变主体 + 顶部高光 + 底部内阴影）──
        cx, cw = self._capsule
        if cw > 4:
            cap = QRectF(cx, 7.0, cw, h - 14.0)
            radius = min(cap.height() / 2, 17.0)
            # 移动拉伸时圆角略微变小（更有液体感）
            radius = max(10.0, radius - abs(cw - cap.height()) * 0.12)

            # 主体：上浅下深玻璃渐变
            grad = QLinearGradient(0, cap.top(), 0, cap.bottom())
            grad.setColorAt(0.0, QColor(255, 255, 255, 232))
            grad.setColorAt(0.55, QColor(241, 247, 254, 190))
            grad.setColorAt(1.0, QColor(214, 226, 244, 128))
            p.setBrush(grad)
            p.setPen(QPen(QColor(255, 255, 255, 205), 1.2))
            p.drawRoundedRect(cap, radius, radius)

            # 顶部高光（覆盖 60% 高度，模拟玻璃反光）
            hi = QLinearGradient(0, cap.top(), 0, cap.top() + cap.height() * 0.6)
            hi.setColorAt(0.0, QColor(255, 255, 255, 150))
            hi.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(hi)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(cap.x() + 1.5, cap.y() + 1,
                                     cap.width() - 3, cap.height() * 0.6),
                              radius - 2, radius - 2)

            # 底部内阴影（增强玻璃厚度感）
            low = QLinearGradient(0, cap.bottom() - cap.height() * 0.35, 0, cap.bottom())
            low.setColorAt(0.0, QColor(90, 120, 180, 0))
            low.setColorAt(1.0, QColor(90, 120, 180, 38))
            p.setBrush(low)
            p.drawRoundedRect(cap, radius, radius)

        # ── 图标 + 文字（Dock 风格：选中放大/高亮，hover 浮起+阴影+极光）──
        for i, (key, name) in enumerate(self._entries):
            r = self._item_rect(i)
            center = r.center()
            hovered = (i == self._hover)
            active = (i == self._current)
            hp = self._hover_progress if hovered else 0.0

            # 选中项：图标放大 1.12×；hover 项：放大 1.08× + 上浮 3px
            scale = 1.0 + (0.12 if active else 0.0) + 0.08 * hp
            lift = -3.0 * hp

            if active:
                color = QColor(30, 50, 88)
            elif hovered:
                color = QColor(62, 92, 152)
            else:
                color = QColor(126, 138, 158)

            icon_size = 25 * scale
            icon_rect = QRectF(
                center.x() - icon_size / 2,
                center.y() - icon_size / 2 - (8 if show_text else 0) + lift,
                icon_size, icon_size,
            )

            # hover：项背景柔和高亮（圆角胶囊块）+ 浮起阴影（双层柔化）
            if hp > 0.01:
                bg = QRectF(r.x() + 8, r.y() + 5, r.width() - 16, r.height() - 10)
                p.setBrush(QColor(255, 255, 255, int(52 * hp)))
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(bg, 13, 13)
                # 浮起阴影（外层大柔影 + 内层略实影）
                for (scale_s, alpha) in ((1.25, 26), (0.95, 46)):
                    shadow = QRadialGradient(
                        center.x(), icon_rect.bottom() + 6, icon_size * scale_s * 0.6)
                    shadow.setColorAt(0.0, QColor(30, 60, 110, int(alpha * hp)))
                    shadow.setColorAt(1.0, QColor(30, 60, 110, 0))
                    p.setBrush(shadow)
                    p.drawEllipse(
                        QRectF(center.x() - icon_size * scale_s * 0.55,
                               icon_rect.bottom() + 1,
                               icon_size * scale_s * 1.1,
                               icon_size * scale_s * 0.5))

            # hover：图标区极光光晕轻微增强（参数联动，可关闭）
            if hp > 0.01 and S.get("aurora.enabled", True):
                glow_r = icon_size * 0.9
                g = QRadialGradient(icon_rect.center(), glow_r)
                intensity = float(S.get("aurora.intensity", 0.55))
                g.setColorAt(0.0, QColor(140, 170, 255, int(46 * hp * intensity)))
                g.setColorAt(1.0, QColor(140, 170, 255, 0))
                p.setBrush(g)
                p.drawEllipse(icon_rect.center(), glow_r, glow_r)

            draw_icon(p, icon_rect, key, color, emphasized=hovered or active)

            if show_text:
                p.setPen(QColor(30, 50, 88) if active else color)
                f = QFont(self.font())
                f.setPixelSize(11 if not active else 11.5)
                f.setWeight(QFont.Weight.DemiBold if active else QFont.Weight.Normal)
                p.setFont(f)
                p.drawText(QRectF(r.x(), h - 18, r.width(), 14), Qt.AlignCenter, name)

        p.end()
