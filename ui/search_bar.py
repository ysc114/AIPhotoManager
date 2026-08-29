"""
search_bar.py —— 全局搜索框（macOS Spotlight / Finder 风格）

- Liquid Glass 圆角胶囊输入框：半透明玻璃底 + 搜索图标 + 占位文字
- 获得焦点时轻微极光边缘（参数联动，aurora 关闭后仅发光边框）
- 实时搜索：输入防抖 150ms → 只读索引 → 下拉结果面板
- 结果项复用缩略图缓存；点击发信号由主窗口跳转（角色→详情页 / 照片→预览）

信号：
    role_activated(dict)  角色组 dict（_open_group 直接可用）
    photo_activated(str)  照片本地路径
"""

from PySide6.QtCore import Qt, QTimer, QRectF, QSize, Signal, QPoint
from PySide6.QtGui import QPainter, QColor, QLinearGradient, QRadialGradient
from PySide6.QtWidgets import (
    QWidget, QLineEdit, QListWidget, QListWidgetItem, QLabel, QHBoxLayout,
    QVBoxLayout, QFrame,
)

from config.settings_manager import settings as S
from ui.components.icons import draw_icon
from core.search_index import search_index
from core.thumbnail_cache import thumbnail_cache


class _ResultCard(QFrame):
    """搜索结果项（角色：头像+名称+类别；照片：缩略图+文件名+收藏）。"""

    def __init__(self, kind, data, parent=None):
        super().__init__(parent)
        self.kind = kind          # "role" / "photo"
        self.data = data
        self.setFixedHeight(52)
        self.setStyleSheet("QFrame{background:transparent;border:none;}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 10, 5)
        lay.setSpacing(10)

        # 缩略图
        img = QLabel()
        img.setFixedSize(40, 40)
        img.setAlignment(Qt.AlignCenter)
        img.setStyleSheet("background:rgba(240,244,250,0.5);border-radius:8px;border:none;")
        pix_path = None
        if kind == "role":
            dets = data.get("detections") or []
            if dets and dets[0].get("image_path"):
                pix_path = dets[0]["image_path"]
                bbox = dets[0].get("bbox")
                cp = None
                try:
                    cp = thumbnail_cache.get_cached(pix_path, 256, bbox)
                except Exception:
                    cp = None
                if cp:
                    pix_path = cp
                    bbox = None
            if pix_path:
                from PySide6.QtGui import QPixmap
                px = QPixmap(pix_path)
                if not px.isNull():
                    img.setPixmap(px.scaled(40, 40, Qt.KeepAspectRatio,
                                            Qt.SmoothTransformation))
                else:
                    img.setText("?")
            else:
                img.setText("🎭" if data.get("type") == "fursuit_character" else "👤")
        else:
            pix_path = data.get("path")
            try:
                cp = thumbnail_cache.get_cached(pix_path, 256)
            except Exception:
                cp = None
            from PySide6.QtGui import QPixmap
            if cp:
                px = QPixmap(cp)
            else:
                px = QPixmap(pix_path) if pix_path else QPixmap()
            if not px.isNull():
                img.setPixmap(px.scaled(40, 40, Qt.KeepAspectRatio,
                                        Qt.SmoothTransformation))
            else:
                img.setText("🖼")
        lay.addWidget(img)

        # 文字
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        if kind == "role":
            title = data.get("name") or "未命名角色"
            if not data.get("name"):
                title = f"角色 {str(data.get('character_id') or '')[:10]}"
            sub = "兽装" if data.get("type") == "fursuit_character" else "人物"
            cid = str(data.get("character_id") or "")[:12]
            if cid:
                sub += f" · {cid}"
            if data.get("count"):
                sub += f" · {data['count']} 张"
        else:
            title = data.get("name", "")
            sub = "⭐ 收藏照片" if data.get("favorite") else "照片"
        t = QLabel(title)
        t.setStyleSheet("font-size:12.5px;font-weight:600;color:#26364a;"
                        "background:transparent;border:none;")
        t2 = QLabel(sub)
        t2.setStyleSheet("font-size:10.5px;color:#8a97a8;background:transparent;border:none;")
        text_col.addWidget(t)
        text_col.addWidget(t2)
        lay.addLayout(text_col, 1)


class GlassSearchBar(QWidget):
    """Liquid Glass 搜索框 + 下拉结果面板。"""

    role_activated = Signal(dict)
    photo_activated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._focused = False
        self._panel_visible = False
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(150)
        self._debounce.timeout.connect(self._do_search)
        self._last_query = ""

        self.setFixedHeight(42)
        self.setAttribute(Qt.WA_Hover, True)

        # 输入行（透明，绘制由本组件完成）
        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText("搜索照片、角色或人物…")
        self._edit.setStyleSheet(
            "QLineEdit{background:transparent;border:none;color:#2a3a4e;"
            "font-size:13px;padding-left:38px;padding-right:30px;}"
            "QLineEdit::placeholder{color:#9aa6b8;}"
        )
        self._edit.setAttribute(Qt.WA_MacShowFocusRect, False)
        self._edit.textChanged.connect(self._on_text_changed)
        self._edit.returnPressed.connect(self._on_enter)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._edit)

        # 结果面板（浮动，由主窗口定位）
        self._panel = QListWidget()
        self._panel.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self._panel.setAttribute(Qt.WA_TranslucentBackground)
        self._panel.setStyleSheet(
            "QListWidget{background:rgba(248,250,255,238);border:1px solid "
            "rgba(255,255,255,0.85);border-radius:16px;"
            "outline:none;padding:6px;}"
            "QListWidget::item{background:transparent;border-radius:12px;}"
            "QListWidget::item:hover{background:rgba(130,170,255,0.18);}"
        )
        self._panel.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._panel.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self._panel.itemClicked.connect(self._on_item_clicked)

    # --------------------------------------------------------
    # 行为
    # --------------------------------------------------------
    def _on_text_changed(self, text):
        self._debounce.start()

    def _do_search(self):
        q = self._edit.text()
        if not q.strip():
            self._hide_panel()
            return
        try:
            result = search_index.search(q)
        except Exception as e:
            print(f"[搜索] 失败: {e}")
            self._hide_panel()
            return
        self._render_results(result, q)
        self._show_panel()

    def _render_results(self, result, query):
        self._panel.clear()
        roles = result.get("roles") or []
        photos = result.get("photos") or []
        for g in roles:
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 56))
            card = _ResultCard("role", g)
            self._panel.addItem(item)
            self._panel.setItemWidget(item, card)
        for p in photos:
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 56))
            card = _ResultCard("photo", p)
            self._panel.addItem(item)
            self._panel.setItemWidget(item, card)
        if not roles and not photos:
            item = QListWidgetItem(f"没有找到「{query}」相关结果")
            item.setFlags(Qt.NoItemFlags)
            item.setSizeHint(QSize(0, 44))
            item.setTextAlignment(Qt.AlignCenter)
            self._panel.addItem(item)

    def _show_panel(self):
        if self._panel.count() == 0:
            self._hide_panel()
            return
        self._panel.adjustSize()
        w = max(self.width(), 360)
        self._panel.setFixedWidth(w)
        h = min(self._panel.sizeHintForRow(0) * self._panel.count() + 20, 360)
        self._panel.setFixedHeight(max(60, h))
        # 定位到搜索框下方（父窗口坐标）
        parent = self.window() if self.window() else self
        g = self.mapTo(parent, QPoint(0, self.height() + 6))
        self._panel.setParent(parent)
        self._panel.move(g.x(), g.y())
        self._panel.show()
        self._panel.raise_()
        self._panel_visible = True

    def _hide_panel(self):
        self._panel.hide()
        self._panel_visible = False

    def _on_item_clicked(self, item):
        w = self._panel.itemWidget(item)
        if w is None:
            return
        if w.kind == "role":
            self.role_activated.emit(w.data)
        else:
            self.photo_activated.emit(w.data.get("path", ""))
        self._edit.clear()
        self._edit.clearFocus()
        self._hide_panel()

    def _on_enter(self):
        if self._panel.count() and self._panel.item(0).flags() & Qt.ItemIsEnabled:
            self._on_item_clicked(self._panel.item(0))

    def clear(self):
        self._edit.clear()

    # --------------------------------------------------------
    # 焦点 / 极光
    # --------------------------------------------------------
    def focusInEvent(self, event):
        self._focused = True
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._focused = False
        self.update()
        # 点击面板不算失焦（面板是独立窗口，短暂隐藏由 itemClicked 处理）
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self._hide_panel()
        super().hideEvent(event)

    def resizeEvent(self, event):
        self._edit.setGeometry(self.rect())
        super().resizeEvent(event)

    # --------------------------------------------------------
    # 绘制（Liquid Glass 胶囊）
    # --------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0, 0, self.width(), self.height())
        radius = self.height() / 2

        # 聚焦极光边缘（参数联动）
        if self._focused and S.get("aurora.enabled", True):
            glow = QRadialGradient(rect.center(), rect.width() * 0.7)
            intensity = float(S.get("aurora.intensity", 0.55))
            glow.setColorAt(0.0, QColor(130, 165, 255, int(90 * intensity)))
            glow.setColorAt(1.0, QColor(150, 125, 255, int(50 * intensity)))
            p.setBrush(glow)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), radius + 3, radius + 3)

        # 玻璃底
        grad = QLinearGradient(0, 0, 0, rect.height())
        grad.setColorAt(0.0, QColor(255, 255, 255, 226))
        grad.setColorAt(1.0, QColor(240, 246, 253, 205))
        p.setBrush(grad)
        p.setPen(QColor(255, 255, 255, 190))
        p.drawRoundedRect(rect, radius, radius)
        # 顶部高光
        hi = QLinearGradient(0, rect.top(), 0, rect.top() + rect.height() * 0.55)
        hi.setColorAt(0.0, QColor(255, 255, 255, 120))
        hi.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(hi)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(rect, radius, radius)

        # 搜索图标
        draw_icon(p, QRectF(12, rect.center().y() - 9, 18, 18), "search",
                  QColor(120, 132, 152) if not self._focused else QColor(90, 120, 200),
                  emphasized=self._focused)
        p.end()
