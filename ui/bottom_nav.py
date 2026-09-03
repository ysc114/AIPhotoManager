"""
BottomGlassNav —— 底部悬浮 Liquid Glass + Aurora 导航 Dock（全面重绘版）

设计（Apple macOS Dock + Xiaomi HyperOS 极光）：
- 悬浮胶囊 Dock：不打底贴边，居中悬浮、圆角 26、与窗口底部留 26px 空隙
- 真实毛玻璃背景：pyglass 折射切片（背景抓取 → 透镜折射/霜化），
  节流刷新（显示/缩放/设置变更/每 3.5s），失败自动回退多层渐变玻璃
- Aurora 流动光效：Dock 专属（nav.aurora 开关，独立于卡片 aurora.enabled），
  三色光晕缓慢漂移 + hover 时光晕向鼠标跟随
- 选中项：Liquid Glass 胶囊（渐变主体 + 顶部高光 + 底部内阴影 + 微极光色），
  液态滑动动画（OutBack + 移动中轻微拉伸），仅选中/悬停项显示文字
  其他项目降低视觉权重（小图标 + 低对比）
- Hover：图标放大 + 背景玻璃高亮块 + 光晕跟随（约 170ms 平滑，无闪烁）
- 线性图标自绘（ui.components.icons），响应式：窄窗口只显示图标

纯 UI 组件：只发 page_changed 信号，不含任何业务逻辑。
"""

import math

from PySide6.QtCore import Qt, QPointF, QRectF, QEasingCurve, QVariantAnimation, QTimer, Signal, QPoint
from PySide6.QtGui import QPainter, QLinearGradient, QRadialGradient, QColor, QPen, QFont
from PySide6.QtWidgets import QWidget

from config.settings_manager import settings as S
from ui.components.icons import draw_icon

# 导航项： (key, 名称)（与 content_stack 页面索引一一对应）
# 注意顺序 = 内容栈顺序：…收藏(6) 待处理(7) 设置(8) 重复照片(9)
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
    ("duplicates", "重复照片"),
]

# 液态效果档位 → 拉伸峰值(px) × 强度
_EFFECT_STRETCH = {"soft": 0.0, "standard": 9.0, "vivid": 16.0}

# ── Dock 几何（本次重绘参数）──────────────────────────────
DOCK_H = 86            # 栏高
DOCK_RADIUS = 26       # 圆角
OUTER_PAD = 18         # 左右内边距
ITEM_TEXT_W = 96       # 带文字项宽
ITEM_ICON_W = 64       # 仅图标项宽（窄窗口）
CAPS_H = 62            # 选中胶囊高度
CAPS_Y = 4             # 胶囊顶部
CAPS_INSET = 7         # 胶囊左右内缩
ICON_Y = 26            # 图标中心 y
LABEL_Y = 54           # 选中/悬停文字中心 y（胶囊内）
LABEL_BOTTOM_Y = 74    # 非激活项文字中心 y（胶囊外）
HOVER_ANIM_MS = 170


class BottomGlassNav(QWidget):
    """底部悬浮液态玻璃导航 Dock。

    page_changed(int)：用户点击某导航项（与 content_stack 索引一致）。
    """

    page_changed = Signal(int)

    def __init__(self, entries=None, parent=None):
        super().__init__(parent)
        self._entries = entries or DEFAULT_ENTRIES
        self._n = len(self._entries)
        self._current = 0
        self._hover = -1
        self._hover_progress = 0.0
        self._cursor = QPointF(-1.0, -1.0)
        self._capsule = (0.0, 0.0)        # 当前胶囊 (x, w)
        self._capsule_anim = None
        self._hover_anim = None

        # ── Aurora 流动（Dock 专属，nav.aurora）──
        self._phase = 0.0
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(40)          # 25fps 足够柔
        self._anim_timer.timeout.connect(self._tick_anim)

        # ── 毛玻璃（pyglass 折射切片，nav.glass）──
        self._pg = None
        self._pg_available = None
        self._renderer = None
        self._backdrop = None
        self._glassy = None
        self._glass_dirty = True
        self._glass_refresh_timer = QTimer(self)
        self._glass_refresh_timer.setInterval(3500)
        self._glass_refresh_timer.timeout.connect(self._on_glass_cycle)

        self.setFixedHeight(DOCK_H)
        self.setAttribute(Qt.WA_Hover, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

        # ── 悬浮投影：透明底 + 圆角投影（Dock 悬浮感）──
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        from PySide6.QtWidgets import QGraphicsDropShadowEffect
        _sh = QGraphicsDropShadowEffect(self)
        _sh.setBlurRadius(30)
        _sh.setOffset(0, 10)
        _sh.setColor(QColor(28, 55, 105, 120))
        self.setGraphicsEffect(_sh)

        # 导航参数自监听：修改立即生效；销毁时注销
        self._nav_cb = S.on_change("nav", self._on_nav_cfg)
        self.destroyed.connect(self._on_destroyed)

        # 初始胶囊位置
        r = self._capsule_rect(self._current)
        self._capsule = (r.x(), r.width())
        if self._cfg()["aurora"]:
            self._anim_timer.start()

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
            aurora=bool(S.get("nav.aurora", True)),
            glass=bool(S.get("nav.glass", True)),
        )

    def _show_text(self):
        cfg = self._cfg()
        if not cfg["show_text"]:
            return False
        return self.width() >= self._n * 78  # 空间不足时只显示图标

    def natural_width(self):
        """自动宽度：全部项目 + 左右内边距（父级居中定位用）。"""
        iw = ITEM_TEXT_W if self._cfg()["show_text"] else ITEM_ICON_W
        return self._n * iw + 2 * OUTER_PAD

    def _item_w(self):
        return ITEM_TEXT_W if self._show_text() else ITEM_ICON_W

    def _on_nav_cfg(self, key, value):
        if key in ("nav.animation", "nav.animation_strength", "nav.liquid_effect"):
            if self._capsule_anim is None or self._capsule_anim.state() != QVariantAnimation.Running:
                r = self._capsule_rect(self._current)
                self._capsule = (r.x(), r.width())
        elif key == "nav.aurora":
            if value:
                self._anim_timer.start()
            else:
                self._anim_timer.stop()
        elif key == "nav.glass":
            self._glass_dirty = True
        self.update()

    # --------------------------------------------------------
    # 几何
    # --------------------------------------------------------
    def _item_rect(self, i):
        iw = self._item_w()
        start = (self.width() - self._n * iw) / 2.0
        return QRectF(start + i * iw, 0, iw, self.height())

    def _capsule_rect(self, i):
        r = self._item_rect(i)
        return QRectF(r.x() + CAPS_INSET, CAPS_Y, r.width() - 2 * CAPS_INSET, CAPS_H)

    def _hit_item(self, pos):
        if self.width() <= 0:
            return -1
        x = pos.x()
        if x < 0 or x >= self.width():
            return -1
        iw = self._item_w()
        n = self._n
        start = (self.width() - n * iw) / 2.0
        idx = int((x - start) / iw)
        if idx < 0 or idx >= n:
            return -1
        return idx

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
        w = max(w, new.width() - 22.0)
        self._capsule = (x, w)
        self.update()

    # --------------------------------------------------------
    # 事件
    # --------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        self._glass_dirty = True
        if self._cfg()["aurora"]:
            self._anim_timer.start()

    def hideEvent(self, event):
        self._anim_timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event):
        # 几何变化 → 玻璃切片失效 + 胶囊对齐
        self._renderer = None
        self._glassy = None
        self._glass_dirty = True
        if self._capsule_anim is None or self._capsule_anim.state() != QVariantAnimation.Running:
            r = self._capsule_rect(self._current)
            self._capsule = (r.x(), r.width())
        super().resizeEvent(event)

    def enterEvent(self, event):
        super().enterEvent(event)

    def hoverMoveEvent(self, event):
        self._set_hover(self._hit_item(event.pos()))
        self._cursor = QPointF(event.pos())
        super().hoverMoveEvent(event)

    def mouseMoveEvent(self, event):
        self._set_hover(self._hit_item(event.pos()))
        self._cursor = QPointF(event.pos())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._set_hover(-1)
        self._cursor = QPointF(-1.0, -1.0)
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
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setStartValue(0.0)
        self._hover_anim.setEndValue(1.0 if idx >= 0 else 0.0)
        self._hover_anim.setDuration(HOVER_ANIM_MS)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover_tick)
        self._hover_anim.start()

    def _on_hover_tick(self, v):
        self._hover_progress = float(v)
        self.update()

    # --------------------------------------------------------
    # Aurora 流动（Dock 专属暗色极光，nav.aurora）
    # --------------------------------------------------------
    def _tick_anim(self):
        if not self.isVisible():
            return
        self._phase += 0.035
        self.update()

    def _paint_aurora(self, p, w, h):
        """3 色光晕缓慢漂移 + hover 时朝鼠标汇聚（克制低透明度）。"""
        cfg = self._cfg()
        if not cfg["aurora"]:
            return
        intensity = max(0.0, float(S.get("aurora.intensity", 0.55)))
        base_a = int(20 + 20 * intensity)
        t = self._phase
        lights = (
            ((0.28 + 0.10 * math.sin(t * 0.9), 0.30 + 0.12 * math.cos(t * 0.7)), (110, 170, 255)),
            ((0.72 + 0.10 * math.cos(t * 0.8), 0.55 + 0.10 * math.sin(t * 1.1)), (150, 120, 255)),
            ((0.48 + 0.12 * math.sin(t * 1.3 + 1.0), 0.12 + 0.08 * math.cos(t * 0.6)), (255, 140, 190)),
        )
        for ((nx, ny), col) in lights:
            center = QPointF(nx * w, ny * h)
            r = max(20.0, w * 0.34)
            g = QRadialGradient(center, r)
            g.setColorAt(0.0, QColor(col[0], col[1], col[2], base_a))
            g.setColorAt(0.55, QColor(col[0], col[1], col[2], int(base_a * 0.45)))
            g.setColorAt(1.0, QColor(col[0], col[1], col[2], 0))
            p.setBrush(g)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(0, 0, w, h), DOCK_RADIUS, DOCK_RADIUS)

        # hover：光晕向鼠标方向轻微聚集
        hp = self._hover_progress
        if hp > 0.01 and self._cursor.x() >= 0:
            g = QRadialGradient(self._cursor, max(24.0, w * 0.22))
            g.setColorAt(0.0, QColor(140, 175, 255, int(34 * hp)))
            g.setColorAt(1.0, QColor(140, 175, 255, 0))
            p.setBrush(g)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(0, 0, w, h), DOCK_RADIUS, DOCK_RADIUS)

    # --------------------------------------------------------
    # 毛玻璃背景（pyglass 折射切片；失败自动回退渐变玻璃）
    # --------------------------------------------------------
    def _init_pg(self):
        if self._pg_available is None:
            try:
                import ui.vendor.pyglass as pg
                self._pg = pg
                self._pg_available = True
            except Exception:
                self._pg = None
                self._pg_available = False
        return self._pg_available

    def _glass_active(self):
        if not self._cfg()["glass"]:
            return False
        if not self._init_pg():
            return False
        return self.width() >= 60 and self.height() >= 40

    def _ensure_glass(self):
        if self._renderer is None:
            self._renderer = self._pg.GlassRenderer(
                self._pg.GlassMaterial(
                    thickness=float(S.get("glass.thickness", 0.35)),
                    frost=float(S.get("glass.frost", 0.25)),
                ),
                self.width(), self.height(), DOCK_RADIUS,
            )
        if self._backdrop is None:
            host = self.window()
            if host is None or host is self:
                return False
            self._backdrop = self._pg.WidgetBackdrop(host, exclude=self)
            self._backdrop.changed.connect(self._on_backdrop_changed)
        return True

    def _on_backdrop_changed(self):
        self._glass_dirty = True

    def _refresh_glass(self):
        """抓取窗口背景 → 折射切片（毛玻璃），失败静默回退。"""
        try:
            if not self._ensure_glass():
                self._glassy = None
                return
            bd = self._backdrop
            bd.refresh()
            arr = bd.array()
            if arr is None:
                self._glassy = None
                return
            origin = self.mapTo(bd._host, QPoint(0, 0))
            self._glassy = self._renderer.refract(
                arr, origin, bd.dpr(), fast=True)
        except Exception:
            self._glassy = None
        finally:
            self._glass_dirty = False

    def _on_glass_cycle(self):
        """周期节流刷新（窗口滚动后背景可能变化）。"""
        if self.isVisible() and self._glass_active():
            self._glass_dirty = True
            self.update()

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
        rect = QRectF(0, 0, w, h)

        # ── 底层：毛玻璃折射切片（真实）或渐变玻璃（回退）──
        glass_drawn = False
        if self._glass_active():
            if self._glass_dirty or self._glassy is None:
                self._refresh_glass()
            if self._glassy is not None:
                style = self._pg.GlassStyle(
                    tint_top=int(10 * float(S.get("glass.opacity", 0.35)) * 255),
                    tint_bottom=int(6 * float(S.get("glass.opacity", 0.35)) * 255),
                )
                self._pg.paint_glass(p, rect, DOCK_RADIUS, self._glassy, style=style)
                glass_drawn = True

        if not glass_drawn:
            # 回退：多层渐变玻璃（顶部高光 + 底部微冷色调）
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0.0, QColor(255, 255, 255, 228))
            grad.setColorAt(0.55, QColor(246, 250, 255, 206))
            grad.setColorAt(1.0, QColor(224, 234, 249, 196))
            p.setBrush(grad)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(rect, DOCK_RADIUS, DOCK_RADIUS)
            # 顶部高光带
            hi = QLinearGradient(0, 0, 0, h * 0.45)
            hi.setColorAt(0.0, QColor(255, 255, 255, 110))
            hi.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(hi)
            p.drawRoundedRect(rect, DOCK_RADIUS, DOCK_RADIUS)

        # ── Aurora 流动层（Dock 专属）──
        self._paint_aurora(p, w, h)
        self._paint_panel_border(p, rect, glass_drawn)

        # ── 液态选中胶囊 ──
        cx, cw = self._capsule
        if cw > 4:
            cap = QRectF(cx, CAPS_Y, cw, CAPS_H)
            cap = QRectF(cx, CAPS_Y, cw, min(CAPS_H, h - 12))
            radius = min(CAPS_H / 2, 22.0)
            radius = max(12.0, radius - abs(cw - CAPS_H) * 0.10)
            # 主体：上浅下深玻璃渐变 + 微极光蓝紫
            cg = QLinearGradient(0, cap.top(), 0, cap.bottom())
            cg.setColorAt(0.0, QColor(255, 255, 255, 238))
            cg.setColorAt(0.55, QColor(243, 248, 255, 205))
            cg.setColorAt(1.0, QColor(216, 228, 246, 140))
            p.setBrush(cg)
            p.setPen(QPen(QColor(255, 255, 255, 210), 1.2))
            p.drawRoundedRect(cap, radius, radius)
            # 顶部高光
            hi = QLinearGradient(0, cap.top(), 0, cap.top() + cap.height() * 0.55)
            hi.setColorAt(0.0, QColor(255, 255, 255, 170))
            hi.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(hi)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(cap.x() + 1.2, cap.y() + 1,
                                     cap.width() - 2.4, cap.height() * 0.55),
                              radius - 2, radius - 2)
            # 底部内阴影（玻璃厚度感）
            low = QLinearGradient(0, cap.bottom() - cap.height() * 0.30, 0, cap.bottom())
            low.setColorAt(0.0, QColor(90, 120, 180, 0))
            low.setColorAt(1.0, QColor(90, 120, 180, 40))
            p.setBrush(low)
            p.drawRoundedRect(cap, radius, radius)

        # ── 图标 + 文字 ──
        for i, (key, name) in enumerate(self._entries):
            r = self._item_rect(i)
            center = r.center()
            hovered = (i == self._hover)
            active = (i == self._current)
            hp = self._hover_progress if hovered else 0.0

            scale = 1.0 + (0.14 if active else 0.0) + 0.06 * hp
            if active:
                color = QColor(28, 46, 84)
            elif hovered:
                color = QColor(58, 88, 150)
            else:
                color = QColor(122, 136, 158)

            icon_size = 26 * scale
            icon_rect = QRectF(
                center.x() - icon_size / 2,
                ICON_Y - icon_size / 2,
                icon_size, icon_size,
            )

            # hover：项目背景玻璃高亮块
            if hp > 0.01:
                bg = QRectF(r.x() + 5, CAPS_Y, r.width() - 10, CAPS_H)
                p.setBrush(QColor(255, 255, 255, int(34 * hp)))
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(bg, 18, 18)

            draw_icon(p, icon_rect, key, color, emphasized=active or hovered)

            # 文字：选中/悬停显示在胶囊内（醒目）；其余降低权重
            if show_text:
                if active or hovered:
                    p.setPen(QColor(28, 46, 84) if active else QColor(70, 96, 150))
                    f = QFont(self.font())
                    f.setPixelSize(11.5)
                    f.setWeight(QFont.Weight.DemiBold if active else QFont.Weight.Medium)
                    p.setFont(f)
                    p.drawText(QRectF(r.x(), LABEL_Y - 7, r.width(), 14),
                               Qt.AlignCenter, name)
                elif S.get("nav.show_text", True):
                    p.setPen(QColor(132, 142, 160))
                    f = QFont(self.font())
                    f.setPixelSize(10.5)
                    p.setFont(f)
                    p.drawText(QRectF(r.x(), LABEL_BOTTOM_Y - 7, r.width(), 14),
                               Qt.AlignCenter, name)

        p.end()

    def _paint_panel_border(self, p, rect, glass_drawn):
        """Dock 边缘描边：顶部亮边 + 整体细边（毛玻璃自带 rim 时轻度叠加）。"""
        border_alpha = 150 if glass_drawn else 190
        p.setPen(QPen(QColor(255, 255, 255, border_alpha), 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect.adjusted(0.6, 0.6, -0.6, -0.6), DOCK_RADIUS - 1, DOCK_RADIUS - 1)
        # 顶部亮线（玻璃反光）
        tl = QLinearGradient(0, 0, 0, 3)
        tl.setColorAt(0.0, QColor(255, 255, 255, 200))
        tl.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(tl)
        p.drawRoundedRect(QRectF(rect.x() + 2, rect.y() + 1, rect.width() - 4, 3),
                          DOCK_RADIUS, DOCK_RADIUS)
