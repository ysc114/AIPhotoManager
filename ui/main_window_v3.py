import os
import sys
import json
from pathlib import Path
from datetime import datetime

_project_root = Path(__file__).resolve().parents[1]

if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from config.settings_manager import settings as S
from ui.settings_center import SettingsCenterPage
from ui.aurora_card import AuroraGlassCard


from PySide6.QtCore import Qt, QSize, QTimer, QRect, QThread, Signal
from PySide6.QtGui import (
    QIcon,
    QPixmap,
    QPainter,
    QColor,
    QPen,
    QImageReader,
    QImageIOHandler,
    QLinearGradient,
    QBrush,
)
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QFileDialog,
    QMessageBox,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QStatusBar,
    QProgressBar,
    QFrame,
    QScrollArea,
    QComboBox,
    QDialog,
    QGraphicsDropShadowEffect,
    QSplitter,
    QStackedWidget,
    QGridLayout,
)


from core.image_loader import load_images_from_folder
from core.ai_classifier import AIClassifier
from core.ai_advisor import AIAdvisor
from core.thumbnail_cache import thumbnail_cache
from core.photo_quality.scorer import get_analyzer as get_pq_analyzer
from ui.bottom_nav import BottomGlassNav
from ui.components.glass_button import GlassButton
from ui.components.toast import toast
from ui.search_bar import GlassSearchBar
from ui.role_center_mixin import _RoleCenterMixinMixin
# 页面模块方法被拆分到以下 Mixin（main_window_v3.py 仅保留组装）
from ui.overview_mixin import _OverviewMixinMixin

# 页面模块方法被拆分到以下 Mixin（main_window_v3.py 仅保留组装）
from ui.favorites_mixin import _FavoritesMixinMixin

from ui.duplicates_page import DuplicatesPage

from config.labels import LABEL_MAP


def get_human_categories():
    categories = []
    seen = set()
    for cn_name in LABEL_MAP.values():
        clean = cn_name
        for i in range(len(clean)):
            if ord(clean[i]) > 127:
                continue
            elif clean[i] == ' ':
                clean = clean[i+1:]
                break
            else:
                break
        if clean not in seen:
            seen.add(clean)
            categories.append(clean)
    return categories


# ============================================================
# 主窗口
# ============================================================

class MainWindow(_RoleCenterMixinMixin, _OverviewMixinMixin, _FavoritesMixinMixin, QMainWindow):

    # 左侧导航项（顺序即 QStackedWidget 页索引）
    NAV_ITEMS = [
        "🏠  总览",
        "🤖  AI精选",
        "🖼️  照片",
        "🐾  兽装",
        "👤  人物",
        "🎭  角色",
        "⭐  收藏",
        "⚠️  待处理",
        "⚙️  设置",
    ]

    def __init__(self):

        super().__init__()

        self.setWindowTitle(
            "AI Photo Manager V4"
        )

        self.resize(
            1400,
            850
        )

        self.image_list = []
        # 缩略图/预览缓存：避免同一张大图在卡片、照片墙、照片页反复解码。
        self._pixmap_cache = {}
        # 方案A：detection 主体裁剪图缓存（先裁后缩），key=(path, bbox, target)
        self._det_crop_cache = {}
        # 磁盘缩略图缓存（共享单例）：角色卡片封面优先读缓存，后台生成
        self._thumb_cache = thumbnail_cache
        # 照片内容 MD5 临时内存缓存（image_path → md5），仅用于 UI 照片墙
        # 去重，不落库、不新增字段；每次会话启动后首次计算后复用。
        self._path_md5_cache = {}

        self.classifier = None

        self.current_image_path = None
        self._photo_detection_context = None

        self.current_ai_category = None

        self.advisor = AIAdvisor()

        # 总览刷新开关：构造期间为 False，避免 _switch_page 在
        # 信号尚未连接 / 事件循环未启动时触发后端读取。
        self._ui_ready = False

        # Phase 2：分组页面（兽装/人物/角色）状态容器
        # 每页一个 dict，存 page_stack / 网格容器 / 标题 / 当前组 等
        self._group_pages = {}
        self._group_page_loaded = {
            "fursuit": False, "person": False, "character": False
        }
        self._group_page_pending = {}
        # 卡片/缩略图 → 业务对象映射，供 eventFilter 派发左键点击
        self._card_group_map = {}    # QFrame → (page_key, group_dict, display_name)
        self._tile_path_map = {}     # QLabel → (page_key, group_dict, image_path, detection_index)

        self.init_ui()

        self.connect_signal()

        self._ui_ready = True

        # ui.mode 自监听：任何路径切换界面模式（设置中心/代码）即时生效
        self._mode_cb = S.on_change("ui.mode", lambda k, v: self._apply_theme())
        self.destroyed.connect(self._on_main_destroyed)

        # 启动后事件循环运转时刷新一次总览（测试无事件循环 → 不触发，
        # 避免在测试进程中打开真实 identity_db）。
        # 优化（2026-08-31）：延迟 400ms——首帧统计首次触发 torch import
        # （约 2.3s），先渲染窗口骨架，统计随后异步填充（感知启动提速）。
        QTimer.singleShot(400, self._refresh_overview)

    def _on_main_destroyed(self):
        S.off_change("ui.mode", self._mode_cb)

    # ============================================================
    # UI 构建
    # ============================================================

    def init_ui(self):

        central = QWidget()

        self.setCentralWidget(
            central
        )

        # ── Liquid Glass：主窗口柔和渐变背景（视觉模拟毛玻璃）──
        self._gradient_background(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # ---- 左侧悬浮玻璃导航栏 ----
        self._build_nav(root)

        # ---- 右侧内容区（堆栈，透明以露出渐变背景） ----
        self.content_stack = QStackedWidget()
        self.content_stack.setStyleSheet("background:transparent;")

        # 页 0：总览
        self.overview_page = self._build_overview_page()
        self.content_stack.addWidget(self.overview_page)

        # 页 1：🤖 AI精选（一级核心功能）
        self.ai_pick_page = self._build_ai_pick_page()
        self.content_stack.addWidget(self.ai_pick_page)

        # 页 2：照片（承载原有全部功能）
        self.photo_page = self._build_photo_page()
        self.content_stack.addWidget(self.photo_page)

        # 页 3：兽装 / 页 4：人物 / 页 5：角色（Phase 2 真实页面）
        self.fursuit_page = self._build_groups_page(
            "fursuit", "fursuit_character", "兽装角色", "兽装"
        )
        self.content_stack.addWidget(self.fursuit_page)

        self.person_page = self._build_groups_page(
            "person", "real_person", "人物角色", "人物"
        )
        self.content_stack.addWidget(self.person_page)

        self.character_page = self._build_groups_page(
            "character", "all", "全部角色", "角色"
        )
        self.content_stack.addWidget(self.character_page)

        # 页 6-8：收藏（真页面）/ 待处理（真页面）/ 设置（真页面）
        self.favorites_page = self._build_favorites_page()
        self.content_stack.addWidget(self.favorites_page)
        self.pending_page = self._build_pending_page()
        self.content_stack.addWidget(self.pending_page)
        self.settings_page = self._build_settings_page()
        self.content_stack.addWidget(self.settings_page)
        # ── ♻️ 重复照片管理中心（第 10 页）──
        self.duplicates_page = DuplicatesPage()
        self.content_stack.addWidget(self.duplicates_page)
        self.duplicates_page.data_changed.connect(self._on_duplicates_changed)

        # ── 全局搜索条（顶部 Liquid Glass 胶囊）+ 内容区 ──
        self.search_bar = GlassSearchBar()
        self.search_bar.role_activated.connect(self._open_group_from_search)
        self.search_bar.photo_activated.connect(self._open_photo_by_path)
        main_area = QWidget()
        main_layout = QVBoxLayout(main_area)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(10)
        main_layout.addWidget(self.search_bar)
        main_layout.addWidget(self.content_stack, 1)

        root.addWidget(main_area, 1)
        self._root_layout = root

        # ── 底部悬浮液态导航（新版模式；经典模式恢复左侧导航）──
        self.bottom_nav = BottomGlassNav(parent=central)
        self.bottom_nav.setFixedHeight(70)
        self.bottom_nav.hide()
        self.bottom_nav.page_changed.connect(self._on_bottom_nav_changed)

        self._apply_nav_mode()

        self.content_stack.setCurrentIndex(0)
        self.nav_list.setCurrentRow(0)

        self.setStatusBar(
            QStatusBar()
        )

        self.statusBar().showMessage(
            "程序启动完成"
        )

        # 按设置应用主题（新版/经典版 + Liquid Glass + 深浅色）
        self._apply_theme()

    # ------------------------------------------------------------
    # Liquid Glass 主题（视觉模拟：渐变 + 半透明 + 高光 + 阴影 + 圆角）
    # ------------------------------------------------------------

    def _gradient_background(self, widget):
        """柔和渐变背景（按主题：浅色 / 深色）。"""
        theme = S.get("ui.theme", "system")
        dark = theme == "dark"
        h = widget.height() if widget.height() > 100 else 900
        grad = QLinearGradient(0, 0, 0, h)
        if dark:
            grad.setColorAt(0.0, QColor("#232a36"))
            grad.setColorAt(0.5, QColor("#262330"))
            grad.setColorAt(1.0, QColor("#1e2830"))
        else:
            grad.setColorAt(0.0, QColor("#eef4fc"))
            grad.setColorAt(0.5, QColor("#f3f0fa"))
            grad.setColorAt(1.0, QColor("#e8f0f5"))
        pal = widget.palette()
        pal.setBrush(widget.backgroundRole(), QBrush(grad))
        widget.setPalette(pal)
        widget.setAutoFillBackground(True)

    @classmethod
    def _glass_shadow(cls, widget, blur=None, dy=None, alpha=None):
        """为玻璃面板添加柔和投影（强度受设置参数控制）。

        ui.shadow_strength 缩放透明度、ui.glass_blur 缩放弥散半径，
        参数为 0 时阴影消失。
        """
        b = max(0.2, float(S.get("ui.glass_blur", 30)) / 30.0)
        s = max(0.0, float(S.get("ui.shadow_strength", 40)) / 40.0)
        blur = int((blur if blur is not None else 26) * b)
        alpha = int((alpha if alpha is not None else 55) * s)
        dy = dy if dy is not None else 5
        effect = QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(max(1, blur))
        effect.setOffset(0, dy)
        effect.setColor(QColor(30, 60, 110, max(0, alpha)))
        widget.setGraphicsEffect(effect)

    def _glass_alpha(self):
        """玻璃面板背景不透明度（0.30~0.90）。"""
        return float(S.get("ui.glass_opacity", 0.55))

    def _corner(self):
        """玻璃卡片圆角（8~28）。"""
        return int(S.get("ui.corner_radius", 18))

    def _thumb_radius(self):
        """缩略图圆角（4~24）。"""
        return int(S.get("ui.thumb_radius", 14))

    def _apply_theme(self):
        """根据设置应用全局主题（界面模式 + Liquid Glass + 深浅色）。

        纯视觉切换：不改变任何数据库 / AI / 角色数据。
        """
        mode = S.get("ui.mode", "new")
        lg = bool(S.get("ui.liquid_glass", True))
        dark = S.get("ui.theme", "system") == "dark"

        # 内容区背景：经典版或关闭液态玻璃 → 传统浅灰（深色主题→深灰）
        if mode == "classic" or not lg:
            if dark:
                self.content_stack.setStyleSheet("background:#232a36;")
            else:
                self.content_stack.setStyleSheet("background:#f5f6fa;")
        else:
            self.content_stack.setStyleSheet("background:transparent;")

        # 导航栏样式
        self._apply_nav_theme()

        # 全局 QSS
        if mode == "classic":
            qss = self._classic_qss(dark)
        else:
            qss = self._liquid_qss(dark, lg)
        self.setStyleSheet(qss)

        # 重绘背景渐变
        central = self.centralWidget()
        if central is not None:
            self._gradient_background(central)

    def _refresh_glass_panels(self):
        """玻璃参数修改后即时重建受影响页面（总览卡 + 已加载的分组页）。

        仅刷新 UI 呈现，不触碰任何数据库 / AI 逻辑。
        """
        self._apply_theme()
        if hasattr(self, "_refresh_overview"):
            try:
                self._refresh_overview()
            except Exception:
                pass
        for key in ("fursuit", "person", "character"):
            if self._group_page_loaded.get(key):
                try:
                    self._load_groups_into_page(key)
                except Exception:
                    pass

    @staticmethod
    def _liquid_qss(dark, lg):
        """新版界面全局 QSS（Liquid Glass）。"""
        text = "#cfd8e3" if dark else "#2c3e50"
        return f"""
            QMainWindow {{ background: transparent; }}
            QWidget {{
                font-family: "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei";
                font-size: 13px;
                color: {text};
            }}
            QToolTip {{
                background: rgba(30,40,60,0.92);
                color: #e8eef6;
                border: 1px solid rgba(120,150,190,0.4);
                border-radius: 6px;
                padding: 4px 8px;
            }}
            QScrollBar:vertical {{
                background: transparent; width: 9px; margin: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(120,140,170,0.35);
                border-radius: 4px; min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: rgba(120,140,170,0.55);
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            QScrollBar:horizontal {{
                background: transparent; height: 9px; margin: 2px;
            }}
            QScrollBar::handle:horizontal {{
                background: rgba(120,140,170,0.35);
                border-radius: 4px; min-width: 30px;
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 0;
            }}
            QProgressBar {{
                background: rgba(255,255,255,0.6);
                border: none;
                border-radius: 7px;
                min-height: 14px;
                text-align: center;
                color: #2c3e50;
                font-size: 11px;
            }}
            QProgressBar::chunk {{
                border-radius: 7px;
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #6fb7f5, stop:1 #9b8cf0
                );
            }}
            QStatusBar {{
                background: transparent;
                color: {text};
                border-top: 1px solid rgba(160,180,210,0.25);
            }}
            QPushButton {{
                outline: none;
            }}
            QPushButton:pressed {{
                padding-top: 2px;
            }}
            QListWidget {{
                outline: none;
            }}
        """

    @staticmethod
    def _classic_qss(dark):
        """经典版界面全局 QSS（传统配色）。"""
        return """
            QMainWindow { background: #f5f6fa; }
            QWidget {
                font-family: "Microsoft YaHei UI", "Microsoft YaHei";
                font-size: 13px;
                color: #2c3e50;
            }
            QToolTip {
                background: #ffffff;
                color: #2c3e50;
                border: 1px solid #d5dbdb;
            }
            QScrollBar:vertical {
                background: #f0f2f5; width: 12px; margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #c8cfd8; border-radius: 5px; min-height: 30px;
                margin: 2px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
            QScrollBar:horizontal {
                background: #f0f2f5; height: 12px; margin: 0;
            }
            QScrollBar::handle:horizontal {
                background: #c8cfd8; border-radius: 5px; min-width: 30px;
                margin: 2px;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0;
            }
            QProgressBar {
                background: #ecf0f1; border: 1px solid #d5dbdb;
                border-radius: 5px; min-height: 14px;
                text-align: center; color: #2c3e50; font-size: 11px;
            }
            QProgressBar::chunk {
                border-radius: 4px; background: #3498db;
            }
            QStatusBar {
                background: #f5f6fa; color: #7f8c8d;
                border-top: 1px solid #e0e4e8;
            }
            QPushButton {
                outline: none;
            }
            QPushButton:pressed {
                padding-top: 2px;
            }
            QListWidget {
                outline: none;
            }
        """

    # ------------------------------------------------------------
    # 导航栏
    # ------------------------------------------------------------

    def _build_nav(self, parent_layout):

        nav = QWidget()
        nav.setFixedWidth(196)
        # 样式由 _apply_nav_theme() 按界面模式（新版/经典版）设置
        self.nav = nav
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(14, 22, 14, 16)
        nav_layout.setSpacing(0)

        brand = QLabel("AIPhotoManager")
        brand.setStyleSheet(
            "color:#1f2d3d;font-size:17px;font-weight:800;"
            "padding:0 8px 2px 8px;background:transparent;border:none;"
        )
        nav_layout.addWidget(brand)

        subtitle = QLabel("本地 AI 照片管理")
        subtitle.setStyleSheet(
            "color:#8a97a8;font-size:11px;padding:0 8px 18px 8px;"
            "background:transparent;border:none;"
        )
        nav_layout.addWidget(subtitle)

        self.nav_list = QListWidget()
        self.nav_list.setFixedWidth(168)

        for item_text in self.NAV_ITEMS:
            self.nav_list.addItem(
                QListWidgetItem(item_text)
            )

        nav_layout.addWidget(self.nav_list)
        nav_layout.addStretch()

        parent_layout.addWidget(nav)

        self._apply_nav_theme()

    def _apply_nav_theme(self):
        """按界面模式（新版/经典版）应用导航栏视觉。"""
        mode = S.get("ui.mode", "new")
        if mode == "classic":
            # ── 经典版：深色传统侧栏 ──
            self.nav.setStyleSheet(
                "QWidget{background:#2c3e50;border:none;border-radius:16px;}"
            )
            self.nav_list.setStyleSheet("""
                QListWidget {
                    background: #2c3e50;
                    border: none;
                    outline: none;
                }
                QListWidget::item {
                    color: #ecf0f1;
                    padding: 13px 18px;
                    font-size: 14px;
                    border-left: 3px solid transparent;
                }
                QListWidget::item:selected {
                    background: #34495e;
                    border-left: 3px solid #3498db;
                    color: #ffffff;
                }
                QListWidget::item:hover {
                    background: #3a546b;
                }
            """)
            self.nav.setGraphicsEffect(None)
        else:
            # ── 新版：悬浮玻璃侧栏（参数化：透明度 / 圆角）──
            ga = self._glass_alpha()
            cr = self._corner()
            self.nav.setStyleSheet("""
                QWidget {
                    background: rgba(255,255,255,%f);
                    border: 1px solid rgba(255,255,255,0.7);
                    border-radius: %dpx;
                }
            """ % (ga, cr))
            self.nav_list.setStyleSheet("""
                QListWidget {
                    background: transparent;
                    border: none;
                    outline: none;
                    padding: 4px 2px;
                }
                QListWidget::item {
                    color: #4a5a6a;
                    padding: 11px 14px;
                    margin: 3px 2px;
                    font-size: 13.5px;
                    border-radius: 12px;
                    background: transparent;
                }
                QListWidget::item:selected {
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:1,
                        stop:0 rgba(120,180,255,0.45),
                        stop:1 rgba(160,140,255,0.40)
                    );
                    color: #1f2d3d;
                    font-weight: 600;
                    border: 1px solid rgba(255,255,255,0.8);
                }
                QListWidget::item:hover:!selected {
                    background: rgba(255,255,255,0.45);
                }
            """)
            self._glass_shadow(self.nav, blur=30, dy=8, alpha=70)

        # 底部/左侧导航布局随界面模式切换（新版=底部液态，经典=左侧传统）
        if hasattr(self, "bottom_nav"):
            self._apply_nav_mode()

    def _apply_nav_mode(self):
        """按 ui.mode 切换导航布局：新版=底部悬浮液态导航；
        经典=传统左侧导航（内容区底部不留白）。"""
        if not hasattr(self, "bottom_nav") or self._root_layout is None:
            return
        if S.get("ui.mode", "new") == "classic":
            self.nav.show()
            self.bottom_nav.hide()
            self._root_layout.setContentsMargins(16, 16, 16, 16)
        else:
            self.nav.hide()
            self.bottom_nav.show()
            # 内容区底部预留悬浮底栏空间（高 70 + 悬浮间隙 14）
            self._root_layout.setContentsMargins(16, 16, 16, 100)
        self._layout_bottom_nav()

    def _layout_bottom_nav(self):
        """底部导航：响应式宽度 + 居中悬浮定位（窗口缩放不溢出）。"""
        if not getattr(self, "bottom_nav", None) or self.bottom_nav.isHidden():
            return
        central = self.centralWidget()
        cw = central.width() if central and central.width() > 0 else self.width()
        if cw <= 0:
            return
        margin = 28
        nav_w = min(cw - 2 * margin, len(self.bottom_nav._entries) * 112)
        nav_w = max(320, int(nav_w))
        self.bottom_nav.setFixedWidth(nav_w)
        x = (cw - nav_w) // 2
        y = self.height() - self.bottom_nav.height() - 20
        self.bottom_nav.move(max(0, x), max(0, y))

    def _on_bottom_nav_changed(self, idx):
        """底部导航点击 → 切页（与左侧导航同一路由）。"""
        if idx != self.nav_list.currentRow():
            self.nav_list.setCurrentRow(idx)
        self._switch_page(idx)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "bottom_nav"):
            self._layout_bottom_nav()

    # ------------------------------------------------------------
    # 总览页
    # ------------------------------------------------------------

    def _build_photo_page(self):

        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        search_layout = QHBoxLayout()

        self.search_edit = QLineEdit()

        self.search_edit.setPlaceholderText(
            "输入关键词搜索"
        )

        self.btn_search = QPushButton(
            "🔍搜索"
        )

        search_layout.addWidget(
            self.search_edit
        )

        search_layout.addWidget(
            self.btn_search
        )

        self.btn_fav_toggle = QPushButton("⭐ 收藏当前")
        self.btn_fav_toggle.setStyleSheet(
            "QPushButton{background:#f39c12;color:white;border:none;"
            "padding:6px 14px;border-radius:6px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#e67e22;}"
        )
        self.btn_fav_toggle.clicked.connect(self._toggle_favorite_current)
        search_layout.addWidget(self.btn_fav_toggle)

        self.btn_fav_page = QPushButton("♥ 收藏页")
        self.btn_fav_page.setStyleSheet(
            "QPushButton{background:#e74c3c;color:white;border:none;"
            "padding:6px 14px;border-radius:6px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#c0392b;}"
        )
        self.btn_fav_page.clicked.connect(lambda: self._switch_page(6))
        search_layout.addWidget(self.btn_fav_page)

        root.addLayout(
            search_layout
        )

        # QSplitter 替代固定布局
        self.splitter = QSplitter(Qt.Horizontal)

        self.image_list_widget = QListWidget()
        self.image_list_widget.setIconSize(QSize(110, 110))
        self.image_list_widget.setMinimumWidth(200)

        self.splitter.addWidget(self.image_list_widget)

        right_widget = QWidget()
        right = QVBoxLayout(right_widget)
        right.setContentsMargins(0, 0, 0, 0)

        self.splitter.addWidget(right_widget)
        self.splitter.setSizes([350, 1050])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)

        root.addWidget(self.splitter)

        self.preview_label = QLabel(
            "请选择图片"
        )

        self.preview_label.setAlignment(
            Qt.AlignCenter
        )

        self.preview_label.setMinimumSize(
            700,
            520
        )

        self.preview_label.setStyleSheet(
            """
            border:1px solid gray;
            background:#f5f5f5;
            """
        )

        right.addWidget(
            self.preview_label
        )

        self.ai_scroll_area = QScrollArea()

        self.ai_scroll_area.setWidgetResizable(True)

        self.ai_scroll_area.setMinimumHeight(300)

        self.ai_scroll_area.setMaximumHeight(600)

        self.ai_scroll_area.setStyleSheet("""
            QScrollArea {
                border: 1px solid #d0d0d0;
                border-radius: 8px;
                background: #ffffff;
            }
            QScrollBar:vertical {
                width: 8px;
            }
        """)

        self.ai_container = QWidget()

        self.ai_container.setStyleSheet("""
            QWidget {
                background: #ffffff;
                padding: 5px;
            }
        """)

        self.ai_layout = QVBoxLayout(
            self.ai_container
        )

        self.ai_layout.setSpacing(8)

        self.ai_layout.setContentsMargins(15, 15, 15, 15)

        self.default_info_label = QLabel(
            "图片信息将在这里显示"
        )

        self.default_info_label.setWordWrap(True)

        self.default_info_label.setStyleSheet("""
            color: #666;
            font-size: 14px;
            padding: 10px;
        """)

        self.ai_layout.addWidget(
            self.default_info_label
        )

        self.ai_layout.addStretch()

        self.ai_scroll_area.setWidget(
            self.ai_container
        )

        right.addWidget(
            self.ai_scroll_area
        )

        button_layout = QHBoxLayout()

        self.btn_open = QPushButton(
            "📂 打开文件夹"
        )

        self.btn_ai = QPushButton(
            "🤖 AI分析"
        )

        self.btn_auto = QPushButton(
            "📁 自动分类"
        )

        self.btn_organize = QPushButton(
            "🤖 AI智能整理"
        )

        self.btn_super = QPushButton(
            "🖼 AI超分"
        )

        self.btn_video = QPushButton(
            "🎬 视频抽帧"
        )

        button_layout.addWidget(
            self.btn_open
        )

        button_layout.addWidget(
            self.btn_ai
        )

        button_layout.addWidget(
            self.btn_auto
        )

        button_layout.addWidget(
            self.btn_organize
        )

        button_layout.addWidget(
            self.btn_super
        )

        button_layout.addWidget(
            self.btn_video
        )

        right.addLayout(
            button_layout
        )

        return page

    # ------------------------------------------------------------
    # 设置页（Phase 3-3：只读信息，不允许改 AI 参数）
    # ------------------------------------------------------------

    def _build_settings_page(self):
        """设置中心（完整控制台）：由独立模块 ui.settings_center 实现。

        所有配置读写走 config.settings_manager.SettingsManager；
        构造时不连库，切到设置页时由 _switch_page 触发 refresh()。
        """
        self.settings_center = SettingsCenterPage(win=self)
        return self.settings_center

    def _refresh_settings_page(self):
        """兼容入口：切到设置页时刷新动态数据。"""
        if hasattr(self, "settings_center"):
            self.settings_center.refresh()

    # ------------------------------------------------------------
    # 收藏页（UI Phase 3-1，照片级收藏）
    # ------------------------------------------------------------

    def _build_pending_page(self):
        """构建「待处理」页：添加照片/文件夹 → 一键分析（增量链路）。

        入口：选单张/多张照片或整个文件夹 → 检查重复（path+MD5）→
        仅分析真正的新照片 → L1 路由（兽装→Fursee / 人物→Face /
        其他跳过）→ incremental_assign → 后台线程不阻塞 GUI → 摘要
        提示 → 自动刷新各页。
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(16)

        title = QLabel("待处理 · 添加新照片")
        title.setStyleSheet(
            "font-size:24px;font-weight:800;color:#1f2d3d;"
            "background:transparent;border:none;"
        )
        layout.addWidget(title)

        desc = QLabel(
            "选择照片或文件夹后点击「分析新照片」。\n"
            "兽装 → Fursee 识别；人物 → 人脸识别；其他自动跳过。\n"
            "已存在 / 内容重复的照片会自动跳过（不删除文件）。"
        )
        desc.setStyleSheet("font-size:12px;color:#8a97a8;background:transparent;border:none;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # 统计区：新照片/未分析/失败/重复（Phase 3-2）
        self._pending_stats_label = QLabel("点击「扫描新照片」查看待处理统计")
        self._pending_stats_label.setStyleSheet(
            "font-size:13px;color:#4a5a6a;background:rgba(255,255,255,0.55);"
            "border:1px solid rgba(255,255,255,0.75);border-radius:14px;padding:12px 16px;"
        )
        self._pending_stats_label.setWordWrap(True)
        layout.addWidget(self._pending_stats_label)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        add_files_btn = QPushButton("📁 添加照片")
        add_files_btn.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #57c78a,stop:1 #6aaee8);color:white;border:none;"
            "padding:9px 20px;border-radius:18px;font-size:13px;font-weight:600;}"
            "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #49b87c,stop:1 #5a9fd8);}"
        )
        add_files_btn.clicked.connect(self._pick_photos_to_add)
        btn_row.addWidget(add_files_btn)

        add_folder_btn = QPushButton("📂 添加文件夹")
        add_folder_btn.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #6fb7f5,stop:1 #9b8cf0);color:white;border:none;"
            "padding:9px 20px;border-radius:18px;font-size:13px;font-weight:600;}"
            "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #5cabe9,stop:1 #8a7ce6);}"
        )
        add_folder_btn.clicked.connect(self._pick_folder_to_add)
        btn_row.addWidget(add_folder_btn)

        scan_btn = QPushButton("📡 扫描新照片")
        scan_btn.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #c29ae8,stop:1 #9b8cf0);color:white;border:none;"
            "padding:9px 20px;border-radius:18px;font-size:13px;font-weight:600;}"
            "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #b48ad8,stop:1 #8a7ce6);}"
        )
        scan_btn.clicked.connect(self._scan_photos_dir)
        btn_row.addWidget(scan_btn)

        analyze_btn = QPushButton("▶️  分析新照片")
        analyze_btn.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #f0a35e,stop:1 #ef7f7f);color:white;border:none;"
            "padding:9px 24px;border-radius:18px;font-size:13px;font-weight:600;}"
            "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #e8934e,stop:1 #e56e6e);}"
            "QPushButton:disabled{background:rgba(180,190,200,0.6);color:#fff;}"
        )
        analyze_btn.clicked.connect(self._start_analyze_selected)
        self._pending_analyze_btn = analyze_btn
        btn_row.addWidget(analyze_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 已选列表
        list_label = QLabel("待分析文件：")
        list_label.setStyleSheet("font-size:13px;color:#4a5a6a;font-weight:700;background:transparent;border:none;")
        layout.addWidget(list_label)

        self._pending_list = QListWidget()
        self._pending_list.setMaximumHeight(180)
        self._pending_list.setStyleSheet(
            "QListWidget{background:rgba(255,255,255,0.6);border:1px solid rgba(255,255,255,0.8);"
            "border-radius:14px;font-size:12px;padding:6px;}"
            "QListWidget::item{padding:5px 10px;border-radius:8px;}"
            "QListWidget::item:selected{background:rgba(120,160,255,0.25);}"
        )
        layout.addWidget(self._pending_list)

        # 进度
        self._pending_progress = QProgressBar()
        self._pending_progress.setRange(0, 100)
        self._pending_progress.setValue(0)
        self._pending_progress.setTextVisible(True)
        layout.addWidget(self._pending_progress)

        self._pending_status = QLabel("就绪")
        self._pending_status.setStyleSheet("font-size:12px;color:#7c8ba0;background:transparent;border:none;")
        layout.addWidget(self._pending_status)

        layout.addStretch()

        # 状态：选中文件集合 + 后台 worker
        self._pending_files = []       # 绝对路径列表（未去重展示）
        self._pending_worker = None    # QThread

        return page

    def _pick_photos_to_add(self):
        """QFileDialog 多选照片 → 加入待分析列表（不立即分析）。"""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择要添加的照片",
            "",
            "图片文件 (*.jpg *.jpeg *.png *.webp)",
        )
        self._add_pending_files(files)

    def _pick_folder_to_add(self):
        """QFileDialog 选文件夹 → 加入其中全部图片。"""
        folder = QFileDialog.getExistingDirectory(self, "选择包含照片的文件夹")
        if not folder:
            return
        exts = {".jpg", ".jpeg", ".png", ".webp"}
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return
        files = [
            os.path.join(folder, n).replace("\\", "/")
            for n in names
            if os.path.splitext(n)[1].lower() in exts
        ]
        self._add_pending_files(files)

    def _scan_photos_dir(self):
        """扫描项目 photos/ 目录 → 全部图片加入待分析列表。

        与「分析新照片」共用同一后台增量链路（analyze_paths）：
        path/MD5/批内去重 → L1 路由（兽装 Fursee / 人物 Face / 其他跳过）
        → incremental_assign → 完成自动刷新。
        """
        # 项目根 = 本文件上两级（ui/ 下）；photos 目录与生产库同目录。
        photos_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "photos")
        )
        if not os.path.isdir(photos_dir):
            QMessageBox.warning(self, "提示", f"未找到照片目录：{photos_dir}")
            return
        exts = {".jpg", ".jpeg", ".png", ".webp"}
        try:
            names = sorted(os.listdir(photos_dir))
        except OSError as e:
            QMessageBox.warning(self, "扫描失败", f"无法读取照片目录：{e}")
            return
        files = [
            os.path.join(photos_dir, n).replace("\\", "/")
            for n in names
            if os.path.splitext(n)[1].lower() in exts
        ]
        self._add_pending_files(files)
        self._pending_status.setText(
            f"已扫描 photos/：共 {len(files)} 张图片，"
            f"其中 {len(self._pending_files)} 张待分析（重复将自动跳过）"
        )
        self._refresh_pending_stats()

    def _refresh_pending_stats(self):
        """统计 photos/ 待处理情况：总照片/未入库/重复/已入库。

        只读统计（path + MD5），不触发分析；用于待处理页展示。
        """
        from core.identity import IdentityManager
        mgr = IdentityManager()
        try:
            photos_dir = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "photos")
            )
            existing = {
                row[0] for row in mgr.db.conn.execute(
                    "SELECT DISTINCT image_path FROM identity_image"
                )
            }
            known_md5 = set()
            import hashlib
            for p in existing:
                if os.path.exists(p):
                    try:
                        with open(p, "rb") as fh:
                            known_md5.add(hashlib.md5(fh.read()).hexdigest())
                    except OSError:
                        pass
            total = new_cnt = dup_cnt = 0
            if os.path.isdir(photos_dir):
                exts = {".jpg", ".jpeg", ".png", ".webp"}
                for n in sorted(os.listdir(photos_dir)):
                    if os.path.splitext(n)[1].lower() not in exts:
                        continue
                    total += 1
                    p = os.path.join(photos_dir, n).replace("\\", "/")
                    if p in existing:
                        continue
                    try:
                        with open(p, "rb") as fh:
                            m = hashlib.md5(fh.read()).hexdigest()
                        if m in known_md5:
                            dup_cnt += 1
                            continue
                    except OSError:
                        pass
                    new_cnt += 1
        finally:
            mgr.close()
        self._pending_stats_label.setText(
            f"photos/ 照片总数：{total}\n"
            f"未分析（待处理）：{new_cnt} 张\n"
            f"重复副本（将跳过）：{dup_cnt} 张\n"
            f"已入库：{total - new_cnt - dup_cnt} 张"
        )

    def _add_pending_files(self, files):
        """去重并入列（path 级）；刷新列表。"""
        if not files:
            return
        existing = set(self._pending_files)
        added = 0
        for f in files:
            p = str(f).replace("\\", "/")
            if p not in existing:
                existing.add(p)
                self._pending_files.append(p)
                added += 1
        if added:
            self._pending_list.clear()
            for p in self._pending_files:
                self._pending_list.addItem(QListWidgetItem(os.path.basename(p)))
            self._pending_status.setText(
                f"已选 {len(self._pending_files)} 张照片（分析时自动跳过重复）"
            )

    def _start_analyze_selected(self):
        """后台线程执行 analyze_paths（不阻塞 GUI）；完成后刷新各页。"""
        if self._pending_worker is not None and self._pending_worker.isRunning():
            return
        if not self._pending_files:
            QMessageBox.information(self, "提示", "请先添加照片或文件夹。")
            return
        self._pending_analyze_btn.setEnabled(False)
        self._pending_progress.setValue(0)
        self._pending_status.setText("正在准备…")

        worker = _AnalyzeWorker(self._pending_files)
        worker.progress_updated.connect(self._on_analyze_progress)
        worker.finished_ok.connect(self._on_analyze_done)
        worker.failed.connect(self._on_analyze_failed)
        self._pending_worker = worker
        worker.start()

    def _on_analyze_progress(self, current, total, status):
        if total > 0:
            self._pending_progress.setRange(0, total)
            self._pending_progress.setValue(current)
        self._pending_status.setText(f"({current}/{total}) {status}")

    def _on_analyze_done(self, result):
        self._pending_analyze_btn.setEnabled(True)
        self._pending_progress.setValue(self._pending_progress.maximum())
        self._pending_status.setText("分析完成")
        # 清空已选列表
        self._pending_files = []
        self._pending_list.clear()
        self._pending_worker = None
        # 自动刷新各分组页 + 总览
        for key in ("fursuit", "person", "character"):
            if self._group_page_loaded.get(key):
                self._load_groups_into_page(key)
        if self._ui_ready:
            self._refresh_overview()
        # 摘要
        self._show_analyze_summary(result)

    def _on_analyze_failed(self, err):
        self._pending_analyze_btn.setEnabled(True)
        self._pending_worker = None
        self._pending_status.setText("分析失败")
        QMessageBox.critical(self, "分析失败", f"分析新照片时出错：{err}")

    def _show_analyze_summary(self, r):
        dup = r.get("dup_path", 0) + r.get("dup_md5", 0)
        msg = (
            f"新增照片：{r.get('new', 0)}\n"
            f"  兽装：{r.get('fursuit', 0)}\n"
            f"  人物：{r.get('person', 0)}\n"
            f"  其他：{r.get('other', 0)}\n"
            f"重复跳过：{dup}\n"
            f"失败：{r.get('failed', 0)}\n\n"
            f"新增兽装角色：{r.get('created_fursee', 0)}\n"
            f"加入已有兽装角色：{r.get('joined_fursee', 0)}\n"
            f"新增人物角色：{r.get('created_face', 0)}\n"
            f"加入已有人物角色：{r.get('joined_face', 0)}\n\n"
            "兽装页 / 人物页 / 角色页已刷新。"
        )
        QMessageBox.information(self, "分析完成", msg)

    # ------------------------------------------------------------
    # 占位页
    # ------------------------------------------------------------

    def _build_placeholder_page(self, label_text):

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignCenter)

        clean = label_text.strip()
        hint = QLabel(f"{clean}\n\n（Phase 1 骨架 · 后续阶段实现）")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(
            "font-size:18px;color:#95a5a6;"
        )
        layout.addWidget(hint)

        return page

    # ============================================================
    # Phase 2：分组浏览页面（兽装 / 人物 / 角色共用）
    # ------------------------------------------------------------
    # 结构：页内两级 QStackedWidget
    #   [0] 组列表：统计栏 + 组网格（封面/名称/张数）
    #   [1] 组内照片墙：返回 + 标题 + ✏️重命名 + 照片网格
    #
    # 名称优先级：group.name 非空 → 用户定义名；空 → 运行时默认名
    #            「未命名{default_prefix} #001」（不写库）。
    # 重命名：调 IdentityManager.update_name(character_id, name)，
    #         仅写 identity_group.name 列（schema v1 起就有），
    #         不改 schema / character_id / 聚类 / DBSCAN / Fursee。
    # 数据源：只读 IdentityManager.get_groups()（v2 库上纯 SELECT）。
    # 预览：点击组内照片 → 切到照片页 + 复用 show_preview（不新增预览）。
    # ============================================================

    def _on_duplicates_changed(self):
        """重复副本删除后：刷新总览统计 + 角色页失效重载 + 清理照片预览。"""
        if not self._ui_ready:
            return
        self._refresh_overview()
        for k in ("fursuit", "person", "character"):
            self._group_page_loaded[k] = False
        # 清理预览列表中已删除的照片
        self.image_list = [p for p in self.image_list if os.path.exists(p)]
        if getattr(self, "preview_label", None) and self.image_list:
            try:
                from PySide6.QtGui import QPixmap
                self.preview_label.setPixmap(
                    QPixmap(self.image_list[0]).scaled(
                        self.preview_label.size(), Qt.KeepAspectRatio,
                        Qt.SmoothTransformation))
            except Exception:
                pass

    def connect_signal(self):

        self.nav_list.currentRowChanged.connect(
            self._switch_page
        )

        self.btn_open.clicked.connect(
            self.open_folder
        )

        self.btn_ai.clicked.connect(
            self.start_ai_analysis
        )

        self.btn_search.clicked.connect(
            self.search_images
        )

        self.image_list_widget.currentRowChanged.connect(
            self.show_preview
        )

        self.btn_auto.clicked.connect(
            self.auto_classify
        )

        self.btn_organize.clicked.connect(
            self.ai_organize
        )

        self.btn_super.clicked.connect(
            self.super_resolution
        )

        self.btn_video.clicked.connect(
            self.extract_video_frames
        )

        self.btn_refresh_overview.clicked.connect(
            self._refresh_overview
        )

    def open_folder(self):

        folder = QFileDialog.getExistingDirectory(
            self,
            "选择照片文件夹"
        )

        if not folder:
            return

        try:
            # Stage 2B+: load_images_from_folder() internally scans via
            # core.storage.LocalPhotoLibrary and returns list[str] of
            # absolute paths (byte-identical to the legacy output).
            images = load_images_from_folder(
                folder
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "错误",
                f"扫描失败：{e}"
            )
            return

        self.image_list = images
        self._photo_detection_context = None
        self.image_list_widget.clear()

        for path in images:
            item = QListWidgetItem(
                os.path.basename(path)
            )
            pix, _ = self._load_pixmap_cached(path, QSize(110, 110))
            if not pix.isNull():
                icon = QIcon(
                    pix.scaled(110, 110, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
                item.setIcon(icon)
            self.image_list_widget.addItem(item)

        self.statusBar().showMessage(
            f"加载完成，共 {len(images)} 张图片"
        )

    def show_preview(self, row):

        if row < 0 or row >= len(self.image_list):
            return

        path = self.image_list[row]
        resolved_path = self._resolve_display_path(path)
        context = self._photo_detection_context or {}
        has_context = (
            context.get("row") == row
            and context.get("path") == resolved_path
        )
        pix = (
            self._pixmap_for_full_preview(
                path,
                context.get("bbox"),
                context.get("detection_index"),
            )
            if has_context
            else self._load_pixmap_cached(path, self.preview_label.size())[0]
        )

        if not pix.isNull():
            if not has_context:
                pix = pix.scaled(
                    self.preview_label.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
            self.preview_label.setPixmap(pix)

        try:
            size = os.path.getsize(path) / 1024 / 1024
            time = datetime.fromtimestamp(
                os.path.getmtime(path)
            )
            if self.default_info_label.isVisible():
                extra = ""
                if has_context and context.get("detection_index") is not None:
                    extra = (
                        f"\n角色：{context.get('group_name', '')}"
                        f"\n检测：#{context.get('detection_index')}"
                    )
                self.default_info_label.setText(
                    f"文件：{os.path.basename(path)}\n"
                    f"大小：{size:.2f} MB\n"
                    f"时间：{time}\n"
                    f"路径：{path}"
                    f"{extra}"
                )
        except Exception as e:
            if self.default_info_label.isVisible():
                self.default_info_label.setText(str(e))

    # ===== AI面板辅助方法 =====

    def clear_ai_panel(self):
        self.default_info_label.hide()
        while self.ai_layout.count() > 0:
            item = self.ai_layout.takeAt(0)
            if item.widget():
                widget = item.widget()
                if widget == self.default_info_label:
                    continue
                widget.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _clear_layout(self, layout):
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
            elif child.layout():
                self._clear_layout(child.layout())

    def create_section_header(self, text):
        header = QLabel(text)
        header.setStyleSheet("""
            font-size: 15px;
            font-weight: bold;
            color: #333;
            padding: 8px 0 4px 0;
        """)
        return header

    def get_progress_color(self, percentage):
        if percentage >= 90:
            return "#4CAF50"
        elif percentage >= 60:
            return "#2196F3"
        elif percentage >= 30:
            return "#FF9800"
        else:
            return "#F44336"

    def create_classification_item(self, name, percentage):
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(3)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        name_label = QLabel(name)
        name_label.setStyleSheet("""
            font-size: 13px;
            color: #333;
            font-weight: 500;
        """)

        percent_label = QLabel(f"{percentage:.0f}%")
        percent_label.setStyleSheet(f"""
            font-size: 13px;
            color: {self.get_progress_color(percentage)};
            font-weight: bold;
        """)
        percent_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        top_row.addWidget(name_label)
        top_row.addWidget(percent_label)

        progress_bar = QProgressBar()
        progress_bar.setMinimum(0)
        progress_bar.setMaximum(100)
        progress_bar.setValue(int(percentage))
        progress_bar.setTextVisible(False)
        progress_bar.setFixedHeight(18)

        color = self.get_progress_color(percentage)
        progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: #e0e0e0;
                border-radius: 9px;
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 9px;
            }}
        """)

        layout.addLayout(top_row)
        layout.addWidget(progress_bar)

        return container

    def create_separator(self):
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        separator.setStyleSheet("""
            QFrame {
                color: #e0e0e0;
                margin: 5px 0;
            }
        """)
        return separator

    def start_ai_analysis(self):

        if not self.image_list:
            QMessageBox.information(
                self,
                "提示",
                "请先打开图片文件夹"
            )
            return

        row = self.image_list_widget.currentRow()

        if row < 0:
            QMessageBox.information(
                self,
                "提示",
                "请选择一张图片"
            )
            return

        image_path = self.image_list[row]
        self.current_image_path = image_path

        try:
            if self.classifier is None:
                self.classifier = AIClassifier()

            result = self.classifier.analyze(image_path)

            self.clear_ai_panel()

            category_en = result.get("category", "未知")
            category_cn = LABEL_MAP.get(category_en, category_en)

            quality = result.get("quality", 0) * 100

            scores = result.get("scores", {})

            advice = self.advisor.generate_ai_advice(category_en, category_cn, quality, scores, image_path)

            title = QLabel("🤖 AI分析结果")
            title.setStyleSheet("""
                font-size: 18px;
                font-weight: bold;
                color: #1a1a1a;
                padding: 5px 0;
            """)
            self.ai_layout.addWidget(title)

            self.ai_layout.addWidget(
                self.create_section_header("📂 分类")
            )

            l1 = result.get("layer1", {})
            l3 = result.get("layer3", {})

            l1_cn = l1.get("label_cn", "") if l1 else ""
            l3_cn = l3.get("label_cn", "") if l3 else ""

            if l1_cn:
                l1_label = QLabel(f"主体：{l1_cn}")
                l1_label.setStyleSheet("font-size: 15px; color: #555; padding: 2px 0;")
                self.ai_layout.addWidget(l1_label)

            final_display = advice["category_cn"]
            category_display = QLabel(f"物种：{final_display}")
            category_display.setStyleSheet("""
                font-size: 20px;
                font-weight: bold;
                color: #0078D4;
                padding: 4px 0;
            """)
            self.ai_layout.addWidget(category_display)

            if l3_cn:
                l3_label = QLabel(f"照片类型：{l3_cn}")
                l3_label.setStyleSheet("font-size: 14px; color: #888; padding: 2px 0;")
                self.ai_layout.addWidget(l3_label)

            self.ai_layout.addWidget(
                self.create_section_header("⭐ AI可信度")
            )

            quality_container = QWidget()
            quality_container.setStyleSheet("background: transparent;")
            quality_layout = QHBoxLayout(quality_container)
            quality_layout.setContentsMargins(0, 5, 0, 5)
            quality_layout.setSpacing(10)

            quality_bar = QProgressBar()
            quality_bar.setMinimum(0)
            quality_bar.setMaximum(100)
            quality_bar.setValue(int(quality))
            quality_bar.setTextVisible(False)
            quality_bar.setFixedHeight(24)

            q_color = self.get_progress_color(quality)
            quality_bar.setStyleSheet(f"""
                QProgressBar {{
                    background-color: #e0e0e0;
                    border-radius: 12px;
                    border: none;
                }}
                QProgressBar::chunk {{
                    background-color: {q_color};
                    border-radius: 12px;
                }}
            """)

            quality_percent = QLabel(f"AI置信度：{quality:.0f}%")
            quality_percent.setStyleSheet(f"""
                font-size: 18px;
                font-weight: bold;
                color: {q_color};
            """)

            quality_layout.addWidget(quality_bar, 1)
            quality_layout.addWidget(quality_percent)
            self.ai_layout.addWidget(quality_container)

            self.ai_layout.addWidget(self.create_separator())

            self.ai_layout.addWidget(
                self.create_section_header("📊 分类概率")
            )

            if scores:
                sorted_scores = sorted(
                    scores.items(),
                    key=lambda x: x[1],
                    reverse=True
                )

                for name, score in sorted_scores:
                    chinese_name = LABEL_MAP.get(name, name)
                    percentage = score * 100
                    item_widget = self.create_classification_item(
                        chinese_name,
                        percentage
                    )
                    self.ai_layout.addWidget(item_widget)
            else:
                no_data = QLabel("暂无详细概率数据")
                no_data.setStyleSheet("color: #999; font-size: 12px;")
                self.ai_layout.addWidget(no_data)

            self.ai_layout.addWidget(self.create_separator())

            self.ai_layout.addWidget(
                self.create_section_header("💡 AI建议")
            )

            self.current_ai_category = advice["category_cn"]

            detection_text = f"检测结果：\n{advice['detection']}"
            detection_label = QLabel(detection_text)
            detection_label.setWordWrap(True)
            detection_label.setStyleSheet("""
                font-size: 13px;
                color: #555;
                padding: 5px;
                background: #f9f9f9;
                border-left: 3px solid #0078D4;
                border-radius: 4px;
            """)
            self.ai_layout.addWidget(detection_label)

            suggestion_text = advice['suggestion']
            suggestion_label = QLabel(suggestion_text)
            suggestion_label.setWordWrap(True)
            suggestion_label.setStyleSheet("""
                font-size: 13px;
                color: #333;
                padding: 5px;
                margin-top: 5px;
            """)
            self.ai_layout.addWidget(suggestion_label)

            tags_text = "建议标签：\n" + "、".join(advice['tags'])
            tags_label = QLabel(tags_text)
            tags_label.setWordWrap(True)
            tags_label.setStyleSheet("""
                font-size: 13px;
                color: #555;
                padding: 5px;
                margin-top: 5px;
                background: #f0f8ff;
                border-radius: 4px;
            """)
            self.ai_layout.addWidget(tags_label)

            recommend_label = QLabel(f"推荐指数：\n{advice['stars']}")
            recommend_label.setStyleSheet("""
                font-size: 16px;
                color: #FFD700;
                font-weight: bold;
                padding: 5px;
            """)
            self.ai_layout.addWidget(recommend_label)

            self.ai_layout.addWidget(self.create_separator())

            self.ai_layout.addWidget(
                self.create_section_header("✍️ 人工反馈")
            )

            feedback_label = QLabel("如果AI判断有误，请选择正确分类：")
            feedback_label.setStyleSheet("""
                font-size: 13px;
                color: #555;
                padding: 5px 0;
            """)
            self.ai_layout.addWidget(feedback_label)

            human_categories = get_human_categories()

            self.feedback_combo = QComboBox()
            self.feedback_combo.addItems(human_categories)
            self.feedback_combo.setStyleSheet("""
                QComboBox {
                    font-size: 13px;
                    padding: 6px;
                    border: 1px solid #ccc;
                    border-radius: 4px;
                }
                QComboBox:hover {
                    border-color: #0078D4;
                }
            """)
            self.ai_layout.addWidget(self.feedback_combo)

            self.btn_submit_feedback = QPushButton("📤 提交反馈")
            self.btn_submit_feedback.setStyleSheet("""
                QPushButton {
                    font-size: 13px;
                    padding: 8px;
                    background: #0078D4;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background: #005a9e;
                }
            """)
            self.btn_submit_feedback.clicked.connect(self.submit_feedback)
            self.ai_layout.addWidget(self.btn_submit_feedback)

            self.ai_layout.addStretch()

            self.statusBar().showMessage("AI分析完成")

        except Exception as e:
            QMessageBox.critical(
                self,
                "AI分析失败",
                str(e)
            )

    def submit_feedback(self):
        if not self.current_image_path:
            QMessageBox.warning(self, "提示", "没有可反馈的图片")
            return

        human_category = self.feedback_combo.currentText()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            self.advisor.save_feedback(
                self.current_image_path,
                self.current_ai_category or "未知",
                human_category,
                timestamp,
            )

            QMessageBox.information(
                self,
                "反馈成功",
                f"已记录：AI判断为【{self.current_ai_category}】\n"
                f"人工标注为【{human_category}】\n\n"
                f"反馈已保存，正在刷新..."
            )

            self.start_ai_analysis()

        except Exception as e:
            QMessageBox.critical(
                self,
                "反馈失败",
                f"保存反馈时出错：{e}"
            )

    def search_images(self):

        keyword = self.search_edit.text().strip()

        self._photo_detection_context = None
        self.image_list_widget.clear()

        if not keyword:
            result = self.image_list
        else:
            result = [
                p for p in self.image_list
                if keyword.lower()
                in os.path.basename(p).lower()
            ]

        for path in result:
            item = QListWidgetItem(
                os.path.basename(path)
            )
            pix, _ = self._load_pixmap_cached(path, QSize(110, 110))
            if not pix.isNull():
                icon = QIcon(
                    pix.scaled(110, 110, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
                item.setIcon(icon)
            self.image_list_widget.addItem(item)

        self.statusBar().showMessage(
            f"搜索完成：{len(result)} 张图片"
        )

    def auto_classify(self):

        if not self.image_list:
            QMessageBox.information(
                self,
                "提示",
                "请先打开图片文件夹"
            )
            return

        target_folder = QFileDialog.getExistingDirectory(
            self,
            "选择归档目标文件夹"
        )

        if not target_folder:
            return

        reply = QMessageBox.question(
            self,
            "确认自动分类",
            f"将分析 {len(self.image_list)} 张图片，\n"
            f"按分类结果归档到：\n{target_folder}\n\n"
            f"自动跳过重复图片。\n"
            f"图片较多时可能需要较长时间。\n"
            f"是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        from core.auto_organizer import auto_organize

        progress_dialog = QDialog(self)
        progress_dialog.setWindowTitle("自动分类进度")
        progress_dialog.setFixedSize(450, 180)
        progress_dialog.setModal(True)

        dialog_layout = QVBoxLayout(progress_dialog)

        self.progress_label = QLabel("准备开始...")
        self.progress_label.setWordWrap(True)
        self.progress_label.setStyleSheet("""
            font-size: 14px;
            color: #333;
            padding: 15px;
        """)
        dialog_layout.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                height: 24px;
                border-radius: 12px;
                background: #e0e0e0;
                border: none;
            }
            QProgressBar::chunk {
                background: #0078D4;
                border-radius: 12px;
            }
        """)
        dialog_layout.addWidget(self.progress_bar)

        progress_dialog.show()
        QApplication.processEvents()

        def on_progress(current, total, status_text):
            percent = int(current / total * 100) if total > 0 else 0
            self.progress_bar.setValue(percent)
            self.progress_label.setText(
                f"进度：{current}/{total}\n\n{status_text}"
            )
            QApplication.processEvents()

        try:
            stats = auto_organize(
                self.image_list,
                target_folder,
                mode="copy",
                remove_duplicates=True,
                progress_callback=on_progress
            )

            progress_dialog.close()

            report = "自动分类完成！\n\n"
            report += f"✅ 成功：{stats['success']} 张\n"
            report += f"💾 缓存命中：{stats.get('cache_hits', 0)} 张\n"
            report += f"🔄 跳过重复：{stats.get('duplicates_skipped', 0)} 张\n"
            report += f"❌ 失败：{stats['failed']} 张\n\n"

            if stats["categories"]:
                report += "📊 分类统计：\n"
                for cat, count in sorted(
                    stats["categories"].items(),
                    key=lambda x: x[1],
                    reverse=True
                ):
                    report += f"  【{cat}】：{count} 张\n"

            if stats["errors"]:
                report += "\n⚠️ 错误详情（前5条）：\n"
                for path, err in stats["errors"][:5]:
                    report += f"  {os.path.basename(path)}：{err}\n"

            QMessageBox.information(
                self,
                "自动分类完成",
                report
            )

            self.statusBar().showMessage(
                f"自动分类完成，成功 {stats['success']} 张，"
                f"缓存命中 {stats.get('cache_hits', 0)} 张，"
                f"跳过 {stats.get('duplicates_skipped', 0)} 张重复"
            )

        except Exception as e:
            progress_dialog.close()
            QMessageBox.critical(
                self,
                "自动分类失败",
                str(e)
            )

    def ai_organize(self):

        if not self.image_list:
            QMessageBox.information(
                self,
                "提示",
                "请先打开图片文件夹"
            )
            return

        reply = QMessageBox.question(
            self,
            "确认AI智能整理",
            f"将对 {len(self.image_list)} 张图片进行完整AI扫描：\n\n"
            f"1. 三级AI分类（主体/物种/类型）\n"
            f"2. 人物识别与聚合\n"
            f"3. 保存分析结果\n\n"
            f"可能需要较长时间，是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        from core.ai_organizer import AIOrganizer

        progress_dialog = QDialog(self)
        progress_dialog.setWindowTitle("AI智能整理进度")
        progress_dialog.setFixedSize(450, 180)
        progress_dialog.setModal(True)

        dialog_layout = QVBoxLayout(progress_dialog)

        self.progress_label = QLabel("准备开始...")
        self.progress_label.setWordWrap(True)
        self.progress_label.setStyleSheet("""
            font-size: 14px;
            color: #333;
            padding: 15px;
        """)
        dialog_layout.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                height: 24px;
                border-radius: 12px;
                background: #e0e0e0;
                border: none;
            }
            QProgressBar::chunk {
                background: #0078D4;
                border-radius: 12px;
            }
        """)
        dialog_layout.addWidget(self.progress_bar)

        progress_dialog.show()
        QApplication.processEvents()

        self._organizer = AIOrganizer()

        def on_progress(step, message, percent):
            self.progress_bar.setValue(percent)
            self.progress_label.setText(message)
            QApplication.processEvents()

        try:
            result = self._organizer.organize_folder(
                self.image_list,
                progress_callback=on_progress
            )

            if result is None:
                raise RuntimeError("AIOrganizer 返回了空结果")

            progress_dialog.close()

            report = "AI智能整理完成！\n\n"
            report += "📊 分类统计：\n"
            for cat, count in result.get("categories", {}).items():
                report += f"  【{cat}】：{count} 张\n"

            characters = result.get("characters", [])
            if characters:
                real_count = sum(1 for c in characters if c.get("type") == "real_person")
                fursuit_count = sum(1 for c in characters if c.get("type") == "fursuit_character")
                report += f"\n👤 人物分组：{len(characters)} 组\n"
                report += f"  真人分组：{real_count} 组\n"
                report += f"  兽装角色分组：{fursuit_count} 组\n"

            QMessageBox.information(
                self,
                "AI智能整理完成",
                report
            )

            self.statusBar().showMessage(
                f"AI智能整理完成，{result['total']} 张图片，{len(characters)} 个人物分组"
            )

        except Exception as e:
            progress_dialog.close()
            QMessageBox.critical(
                self,
                "AI智能整理失败",
                str(e)
            )

    def super_resolution(self):

        QMessageBox.information(
            self,
            "AI超分",
            "AI超分功能正在开发中"
        )

    def extract_video_frames(self):

        QMessageBox.information(
            self,
            "视频抽帧",
            "视频抽帧功能正在开发中"
        )


if __name__ == "__main__":

    app = QApplication(sys.argv)

    window = MainWindow()

    window.show()

    sys.exit(
        app.exec()
    )


class _AnalyzeWorker(QThread):
    """后台执行 IdentityManager.analyze_paths，避免阻塞 GUI。

    信号：
        progress_updated(current, total, status) 逐张进度
        finished_ok(result_dict)                成功
        failed(err_str)                         异常
    """

    progress_updated = Signal(int, int, str)
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self._paths = list(paths or [])

    def run(self):
        from core.identity import IdentityManager
        mgr = IdentityManager()
        try:
            result = mgr.analyze_paths(
                self._paths,
                progress_callback=lambda i, t, s: self.progress_updated.emit(i, t, s),
            )
            self.finished_ok.emit(result)
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            mgr.close()


class _ScanDirWorker(QThread):
    """后台执行 IdentityManager.analyze_new_photos（扫 photos/ 目录）。

    用于兽装/人物/角色页「📥 分析新照片」按钮——同步执行会冻结 GUI
    数分钟，改后台后界面可继续操作。analyze_new_photos 的进度回调
    为两参格式 progress_callback(i, total)。
    """

    progress_updated = Signal(int, int, str)
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

    def run(self):
        from core.identity import IdentityManager
        mgr = IdentityManager()
        try:
            result = mgr.analyze_new_photos(
                progress_callback=lambda i, t: self.progress_updated.emit(i, t, ""),
            )
            self.finished_ok.emit(result)
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            mgr.close()


class PhotoQualityWorker(QThread):
    """后台执行单角色「评分 + 近似分组 + AI 精选」（不阻塞 GUI）。

    纯计算：只读照片文件 + 写 photo_quality 独立缓存，不触碰 identity_db。
    """

    finished = Signal(str, object)   # (role_key, result|None)
    progress = Signal(int, int)      # (done, total)

    def __init__(self, analyzer, role_key, photos, force=False, parent=None):
        super().__init__(parent)
        self._analyzer = analyzer
        self._role_key = role_key
        self._photos = list(photos)
        self._force = force

    def run(self):
        try:
            r = self._analyzer.analyze_role(
                self._role_key, self._photos, force=self._force,
                progress_callback=lambda d, t: self.progress.emit(d, t),
            )
            self.finished.emit(self._role_key, r)
        except Exception as e:
            print(f"[AI精选] 分析失败: {e}")
            self.finished.emit(self._role_key, None)
