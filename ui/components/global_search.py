"""
global_search.py —— 全局搜索面板（Spotlight / macOS 风格 · 路线图 ④ 第一阶段）

设计：
- 悬浮式 Liquid Glass 搜索面板（居中顶部出现，640px 宽）
- 半透明玻璃底 + 圆角 24 + 顶部高光 + 边缘描边 + 外投影
- 面板内 Aurora 柔和光效（跟随底部导航的 nav.aurora 开关，默认开）
- 输入框自动聚焦；输入防抖 120ms 发 search_requested
- 分区结果：最近搜索 / 角色 / 照片 / 收藏（标签/文件分区由调用方预留）
- 键盘：↑↓ 选择、Enter 打开、Esc 关闭；鼠标 hover 同步选择
- 出现/消失：淡入淡出 + 轻微下移动画（160ms）

组件职责边界（重要）：
- 不直接操作数据库 / 不读照片目录 / 不做搜索逻辑
- 只负责「输入 → search_requested(query)」与「渲染 set_results →
  result_selected(item)」，由 MainWindow 决定数据与跳转
"""

from PySide6.QtCore import Qt, QTimer, Signal, QVariantAnimation, QEasingCurve, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QLinearGradient, QRadialGradient, QPen
from PySide6.QtWidgets import (
    QWidget, QLineEdit, QLabel, QHBoxLayout, QVBoxLayout, QScrollArea, QFrame,
    QGraphicsDropShadowEffect, QComboBox, QPushButton,
)

from config.settings_manager import settings as S

PANEL_W = 640
PANEL_H = 576
PANEL_RADIUS = 24


class _ResultRow(QFrame):
    """单条结果：图标 + 标题 + 副标题 + 分类徽标（hover/选中高亮）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(54)
        self.setCursor(Qt.PointingHandCursor)
        self._selected = False
        self._hovered = False
        self._apply_qss()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(10)
        self.icon = QLabel("🔍")
        self.icon.setFixedWidth(30)
        self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setStyleSheet(
            "font-size:16px;background:transparent;border:none;")
        lay.addWidget(self.icon)
        text = QVBoxLayout()
        text.setSpacing(1)
        self.title = QLabel("")
        self.title.setStyleSheet(
            "font-size:13.5px;font-weight:700;color:#233248;background:transparent;border:none;")
        self.subtitle = QLabel("")
        self.subtitle.setStyleSheet(
            "font-size:11px;color:#7c8ba0;background:transparent;border:none;")
        text.addWidget(self.title)
        text.addWidget(self.subtitle)
        lay.addLayout(text, 1)
        self.badge = QLabel("")
        self.badge.setStyleSheet(
            "font-size:10px;color:#5b7bd5;background:rgba(120,150,255,0.16);"
            "border-radius:8px;padding:2px 8px;border:none;font-weight:700;")
        lay.addWidget(self.badge)

    def set_item(self, item):
        self._item = item
        self.icon.setText(item.get("icon", "🔍"))
        self.title.setText(str(item.get("title", "")))
        self.subtitle.setText(str(item.get("subtitle", "")))
        self.badge.setText(str(item.get("badge", "")))

    def set_state(self, selected=False, hovered=False):
        if (selected, hovered) == (self._selected, self._hovered):
            return
        self._selected = selected
        self._hovered = hovered
        self._apply_qss()

    def _apply_qss(self):
        if self._selected:
            bg = "rgba(120,160,255,0.22)"
        elif self._hovered:
            bg = "rgba(255,255,255,0.55)"
        else:
            bg = "transparent"
        self.setStyleSheet(
            f"QFrame{{background:{bg};border-radius:12px;border:none;}}")


class GlobalSearchPanel(QWidget):
    """Spotlight 风格全局搜索面板（纯 UI，经 signal 与 MainWindow 通信）。"""

    search_requested = Signal(str)   # 输入变化（内部已防抖）
    result_selected = Signal(object)  # 选中条目 item dict（Enter/点击）
    closed = Signal()                 # Esc/动画结束后关闭

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(PANEL_W, PANEL_H)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_Hover, True)

        # 柔和投影（Dock 同款视觉语言）
        _sh = QGraphicsDropShadowEffect(self)
        _sh.setBlurRadius(40)
        _sh.setOffset(0, 14)
        _sh.setColor(QColor(25, 45, 90, 130))
        self.setGraphicsEffect(_sh)

        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._on_tick)

        self._items = []          # 展平的全部条目（键盘选择）
        self._rows = []           # 行控件
        self._sel = -1
        self._query = ""
        self._anim = None
        self._opacity = 1.0
        self._base_y = 0

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(120)
        self._debounce.timeout.connect(self._emit_request)

        # 内容包裹层：淡入淡出仅在动画期间挂效果（常驻会模糊文字）
        self._body = QWidget(self)
        self._body.setGeometry(self.rect())
        self._body_eff = None

        self._build_ui()
        self.hide()

    def _ensure_fade(self):
        """动画期间给内容挂透明效果；完成后移除（保持文字锐利）。"""
        if self._body_eff is None:
            from PySide6.QtWidgets import QGraphicsOpacityEffect
            self._body_eff = QGraphicsOpacityEffect(self._body)
            self._body.setGraphicsEffect(self._body_eff)
        return self._body_eff

    def _drop_fade(self):
        """移除透明效果：稳态下文字/图片零模糊。"""
        if self._body_eff is not None:
            self._body.setGraphicsEffect(None)
            self._body_eff = None

    # --------------------------------------------------------
    # UI
    # --------------------------------------------------------
    def _build_ui(self):
        outer = QVBoxLayout(self._body)
        outer.setContentsMargins(20, 16, 20, 14)
        outer.setSpacing(10)

        # 输入行：🔍 + 输入框
        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        icon = QLabel("🔍")
        icon.setStyleSheet("font-size:17px;background:transparent;border:none;")
        input_row.addWidget(icon)
        self.input = QLineEdit()
        self.input.setPlaceholderText("搜索照片、角色、收藏、文件……")
        self.input.setStyleSheet(
            "QLineEdit{background:transparent;border:none;font-size:16px;"
            "color:#233248;padding:4px 0;}"
        )
        self.input.setClearButtonEnabled(False)
        self.input.installEventFilter(self)
        self.input.textChanged.connect(self._on_text_changed)
        input_row.addWidget(self.input, 1)
        esc_hint = QLabel("Esc")
        esc_hint.setStyleSheet(
            "font-size:10px;color:#9aa6b8;background:rgba(255,255,255,0.5);"
            "border-radius:8px;padding:2px 8px;border:none;")
        input_row.addWidget(esc_hint)
        outer.addLayout(input_row)

        # 筛选行（与顶部搜索条同一语义：类型作用于角色，收藏作用于照片/语义）
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self._filter_type = QComboBox()
        for label, data in (("全部", "all"), ("兽装角色", "fursuit_character"),
                            ("人物角色", "real_person")):
            self._filter_type.addItem(label, data)
        self._filter_type.setStyleSheet(
            "QComboBox{background:rgba(255,255,255,0.72);border:1px solid "
            "rgba(255,255,255,0.9);border-radius:12px;padding:4px 10px;"
            "font-size:11.5px;color:#3a5a7a;}"
            "QComboBox::drop-down{border:none;width:16px;}"
            "QComboBox QAbstractItemView{background:#f8faff;border-radius:8px;"
            "selection-background-color:rgba(120,160,255,0.25);color:#2a3a4e;}")
        self._filter_type.setToolTip("类型筛选作用于「角色」分区")
        self._filter_type.currentIndexChanged.connect(self._on_filters_changed)
        filter_row.addWidget(self._filter_type)

        self._filter_fav = QPushButton("⭐ 收藏")
        self._filter_fav.setCheckable(True)
        self._filter_fav.setCursor(Qt.PointingHandCursor)
        self._filter_fav.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.72);border:1px solid "
            "rgba(255,255,255,0.9);border-radius:12px;padding:4px 12px;"
            "font-size:11.5px;color:#3a5a7a;}"
            "QPushButton:checked{background:rgba(255,214,102,0.85);"
            "border:1px solid rgba(240,190,60,0.9);color:#5a4200;font-weight:700;}")
        self._filter_fav.setToolTip("只看收藏照片（作用于照片与语义分区）")
        self._filter_fav.toggled.connect(self._on_filters_changed)
        filter_row.addWidget(self._filter_fav)
        filter_row.addStretch(1)
        outer.addLayout(filter_row)

        # 最近搜索行（横向胶囊）
        self._recent_host = QWidget()
        self._recent_layout = QHBoxLayout(self._recent_host)
        self._recent_layout.setContentsMargins(0, 0, 0, 0)
        self._recent_layout.setSpacing(8)
        outer.addWidget(self._recent_host)

        # 结果滚动区
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:vertical{background:transparent;width:8px;margin:2px;}"
            "QScrollBar::handle:vertical{background:rgba(150,165,190,0.5);border-radius:4px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
        )
        self._host = QWidget()
        self._host.setStyleSheet("background:transparent;")
        self._results_layout = QVBoxLayout(self._host)
        self._results_layout.setContentsMargins(0, 0, 4, 0)
        self._results_layout.setSpacing(4)
        self._results_layout.addStretch(1)
        self._scroll.setWidget(self._host)
        outer.addWidget(self._scroll, 1)

        # 底部提示
        hint = QLabel("↑↓ 选择 · Enter 打开 · Esc 关闭")
        hint.setStyleSheet(
            "font-size:10.5px;color:#9aa6b8;background:transparent;border:none;")
        outer.addWidget(hint, 0, Qt.AlignRight)

    # --------------------------------------------------------
    # 动画 / 可见性
    # --------------------------------------------------------
    def show_panel(self):
        """出现：淡入 + 轻微下移（由 MainWindow 定位几何）。"""
        self._sel = -1
        self.raise_()
        self.show()
        self._ensure_fade()
        self.input.setFocus()
        self._animate(0.0, 1.0, 14.0)
        if S.get("nav.aurora", True):
            self._timer.start()

    def hide_panel(self):
        """消失：淡出后隐藏并发 closed 信号。"""
        self._timer.stop()
        if self._anim is not None and self._anim.state() == QVariantAnimation.Running:
            self._anim.stop()
        self._ensure_fade()
        self._animate(1.0, 0.0, 0.0)

    def is_visible(self):
        return self.isVisible()

    def resizeEvent(self, event):
        self._body.setGeometry(self.rect())
        super().resizeEvent(event)

    def _animate(self, a0, a1, drop):
        self._base_y = self.pos().y()
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(lambda t: self._on_anim_tick(t, a0, a1, drop))
        self._anim.finished.connect(self._on_anim_done)
        self._anim.start()

    def _on_anim_tick(self, t, a0, a1, drop):
        self._opacity = a0 + (a1 - a0) * t
        if self._body_eff:
            self._body_eff.setOpacity(self._opacity)
        if drop:
            self.move(self.pos().x(), self._base_y + int(drop * (1.0 - t)))
        self.update()

    def _on_anim_done(self):
        if self._opacity <= 0.01:
            self.hide()
            self.closed.emit()
        else:
            self._drop_fade()   # 稳态：无透明效果，文字/图片零模糊

    # --------------------------------------------------------
    # 数据接口（只渲染，不查询）
    # --------------------------------------------------------
    def filters(self):
        """当前筛选（与顶部搜索条同一语义）：类型作用于角色，收藏作用于照片/语义。"""
        return {
            "type_filter": (self._filter_type.currentData() or "all"),
            "favorite_only": bool(self._filter_fav.isChecked()),
        }

    def _on_filters_changed(self, *args):
        """筛选变化 → 用当前输入立即重跑（不等 120ms 防抖）。"""
        self._emit_request()

    def set_query(self, text):
        self.input.blockSignals(True)
        self.input.setText(text)
        self.input.blockSignals(False)
        self._query = text

    def query(self):
        return self._query

    def set_results(self, sections, recent=None):
        """sections: [{title, items:[{icon,title,subtitle,badge,payload}...]}...]"""
        self._items = []
        self._rows = []
        # 清空（保留末尾 stretch）
        while self._results_layout.count() > 1:
            item = self._results_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for sec in sections or []:
            items = sec.get("items") or []
            if not items:
                continue
            header = QLabel(sec.get("title", ""))
            header.setStyleSheet(
                "font-size:11px;color:#8a97a8;background:transparent;"
                "border:none;padding:4px 2px;font-weight:700;")
            self._results_layout.insertWidget(self._results_layout.count() - 1, header)
            for it in items:
                row = _ResultRow()
                row.set_item(it)
                row.mousePressEvent = lambda ev, i=len(self._items): self._choose(i)
                row.enterEvent = lambda ev, i=len(self._items): self._set_sel(i)
                self._rows.append(row)
                self._items.append(it)
                self._results_layout.insertWidget(self._results_layout.count() - 1, row)
        # 空结果反馈（否则像"搜索不了"）
        if not self._items:
            if self._query:
                tip = f"没有找到与「{self._query}」匹配的内容\n试试文件名、角色名或类别（如：兽装 / 人物）"
            else:
                tip = "输入关键字开始搜索（文件名 / 角色名 / 类别）"
            empty = QLabel(tip)
            empty.setAlignment(Qt.AlignCenter)
            empty.setWordWrap(True)
            empty.setStyleSheet(
                "font-size:13px;color:#8a97a8;background:transparent;"
                "border:none;padding:26px 10px;")
            self._results_layout.insertWidget(self._results_layout.count() - 1, empty)
        # 最近搜索 chips
        self._render_recents(recent or [])
        self._sel = -1
        self._apply_sel()

    def _render_recents(self, recent):
        while self._recent_layout.count():
            item = self._recent_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._recent_host.setVisible(bool(recent))
        for r in recent[:6]:
            chip = QLabel(f"  {r}  ")
            chip.setStyleSheet(
                "font-size:11px;color:#4a5a6a;background:rgba(255,255,255,0.55);"
                "border:1px solid rgba(255,255,255,0.85);border-radius:11px;"
                "padding:3px 10px;")
            chip.setCursor(Qt.PointingHandCursor)
            chip.mousePressEvent = lambda ev, t=r: self._apply_query(t)
            self._recent_layout.addWidget(chip)
        self._recent_layout.addStretch(1)

    # --------------------------------------------------------
    # 交互
    # --------------------------------------------------------
    def _on_text_changed(self, text):
        self._query = text
        # 防抖 120ms → search_requested
        self._debounce.start()

    def _emit_request(self):
        self.search_requested.emit(self._query)

    def _apply_query(self, text):
        self.set_query(text)
        self.search_requested.emit(text)

    def _set_sel(self, idx):
        if idx == self._sel:
            return
        self._sel = idx
        self._apply_sel()

    def _apply_sel(self):
        for i, row in enumerate(self._rows):
            row.set_state(selected=(i == self._sel), hovered=False)
        # 滚动到选中行
        if 0 <= self._sel < len(self._rows):
            self._scroll.ensureWidgetVisible(self._rows[self._sel], 10, 10)

    def _choose(self, idx):
        if 0 <= idx < len(self._items):
            item = self._items[idx]
            self.hide_panel()
            self.result_selected.emit(item)

    def _move_sel(self, delta):
        if not self._items:
            return
        if self._sel < 0:
            self._set_sel(0 if delta > 0 else len(self._items) - 1)
        else:
            self._set_sel(max(0, min(len(self._items) - 1, self._sel + delta)))

    def eventFilter(self, obj, event):
        if obj is self.input:
            from PySide6.QtCore import QEvent
            if event.type() == QEvent.KeyPress:
                if event.key() == Qt.Key_Down:
                    self._move_sel(1)
                    return True
                if event.key() == Qt.Key_Up:
                    self._move_sel(-1)
                    return True
                if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                    if self._sel >= 0:
                        self._choose(self._sel)
                    else:
                        self.search_requested.emit(self._query)
                    return True
                if event.key() == Qt.Key_Escape:
                    self.hide_panel()
                    return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide_panel()
            return
        if event.key() == Qt.Key_Down:
            self._move_sel(1)
            return
        if event.key() == Qt.Key_Up:
            self._move_sel(-1)
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self._sel >= 0:
            self._choose(self._sel)
            return
        super().keyPressEvent(event)

    # --------------------------------------------------------
    # 绘制（Liquid Glass + Aurora）
    # --------------------------------------------------------
    def _on_tick(self):
        self._phase += 0.035
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setOpacity(max(0.0, min(1.0, self._opacity)))
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        # 玻璃底（近实底：背后内容不穿透，避免"全糊"）
        grad = QLinearGradient(0, 0, 0, rect.height())
        grad.setColorAt(0.0, QColor(252, 253, 255, 250))
        grad.setColorAt(0.6, QColor(246, 250, 255, 246))
        grad.setColorAt(1.0, QColor(236, 243, 252, 242))
        p.setBrush(grad)
        p.setPen(QPen(QColor(255, 255, 255, 200), 1.2))
        p.drawRoundedRect(rect, PANEL_RADIUS, PANEL_RADIUS)
        # Aurora（跟随 nav.aurora）
        if S.get("nav.aurora", True):
            t = self._phase
            lights = (
                (QPointF(rect.width() * (0.30 + 0.08 * __import__("math").sin(t * 0.9)),
                         rect.height() * 0.22), (110, 170, 255), 0.16),
                (QPointF(rect.width() * (0.75 + 0.08 * __import__("math").cos(t * 0.7)),
                         rect.height() * 0.10), (150, 120, 255), 0.14),
                (QPointF(rect.width() * 0.55, rect.height() * 0.9), (255, 140, 190), 0.10),
            )
            for center, col, strength in lights:
                r = max(30.0, rect.width() * 0.36)
                g = QRadialGradient(center, r)
                g.setColorAt(0.0, QColor(col[0], col[1], col[2], int(46 * strength * 255)))
                g.setColorAt(0.6, QColor(col[0], col[1], col[2], int(18 * strength * 255)))
                g.setColorAt(1.0, QColor(col[0], col[1], col[2], 0))
                p.setBrush(g)
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(rect, PANEL_RADIUS, PANEL_RADIUS)
        # 顶部高光
        hi = QLinearGradient(0, 0, 0, rect.height() * 0.35)
        hi.setColorAt(0.0, QColor(255, 255, 255, 120))
        hi.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(hi)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(rect, PANEL_RADIUS, PANEL_RADIUS)
        p.end()
