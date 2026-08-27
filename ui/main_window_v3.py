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


from PySide6.QtCore import Qt, QSize, QTimer, QEvent, QRect, QThread, Signal, QPropertyAnimation, QEasingCurve
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
    QDialogButtonBox,
    QAbstractItemView,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QSplitter,
    QStackedWidget,
    QGridLayout,
    QInputDialog,
)


from core.image_loader import load_images_from_folder
from core.ai_classifier import AIClassifier
from core.ai_advisor import AIAdvisor

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

class MainWindow(QMainWindow):

    # 左侧导航项（顺序即 QStackedWidget 页索引）
    NAV_ITEMS = [
        "🏠  总览",
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

        # 启动后事件循环运转时刷新一次总览（测试无事件循环 → 不触发，
        # 避免在测试进程中打开真实 identity_db）。
        QTimer.singleShot(0, self._refresh_overview)

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

        # 页 1：照片（承载原有全部功能）
        self.photo_page = self._build_photo_page()
        self.content_stack.addWidget(self.photo_page)

        # 页 2：兽装 / 页 3：人物 / 页 4：角色（Phase 2 真实页面）
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

        # 页 5-7：收藏（真页面）/ 待处理（真页面）/ 设置（真页面）
        self.favorites_page = self._build_favorites_page()
        self.content_stack.addWidget(self.favorites_page)
        self.pending_page = self._build_pending_page()
        self.content_stack.addWidget(self.pending_page)
        self.settings_page = self._build_settings_page()
        self.content_stack.addWidget(self.settings_page)

        root.addWidget(self.content_stack, 1)

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

    # ------------------------------------------------------------
    # 总览页
    # ------------------------------------------------------------

    def _build_overview_page(self):

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(14)

        title = QLabel("AIPhotoManager")
        title.setStyleSheet(
            "font-size:30px;font-weight:800;color:#1f2d3d;"
            "letter-spacing:1px;background:transparent;border:none;"
        )
        layout.addWidget(title)

        subtitle = QLabel("欢迎回来 · 本地 AI 照片管理")
        subtitle.setStyleSheet(
            "font-size:13px;color:#8a97a8;background:transparent;border:none;"
        )
        layout.addWidget(subtitle)

        layout.addSpacing(10)

        # 统计卡片网格 3x2（玻璃卡片）
        grid = QGridLayout()
        grid.setSpacing(14)

        self._stat_value_labels = {}

        cards = [
            ("analyzed",       "AI 已分析"),
            ("fursuit_photos", "兽装照片"),
            ("person_photos",  "人物照片"),
            ("group_count",    "角色分组"),
            ("feedback",       "人工反馈"),
            ("favorites",      "收藏"),
        ]

        for idx, (key, label_text) in enumerate(cards):
            card, value_label = self._make_stat_card(label_text)
            grid.addWidget(card, idx // 3, idx % 3)
            self._stat_value_labels[key] = value_label

        layout.addLayout(grid)

        layout.addSpacing(8)

        # 待处理摘要（玻璃条）
        self._pending_label = QLabel("正在统计…")
        self._pending_label.setStyleSheet(
            "font-size:13px;color:#5a6a7a;"
            "background:rgba(255,255,255,0.55);"
            "border:1px solid rgba(255,255,255,0.7);border-radius:14px;padding:14px 18px;"
        )
        self._pending_label.setWordWrap(True)
        layout.addWidget(self._pending_label)

        layout.addStretch()

        # 刷新按钮（胶囊）
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_refresh_overview = QPushButton("🔄 刷新统计")
        self.btn_refresh_overview.setStyleSheet("""
            QPushButton {
                font-size:13px;padding:9px 22px;
                background:qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #6fb7f5, stop:1 #9b8cf0
                );
                color:white;border:none;
                border-radius:18px;font-weight:600;
            }
            QPushButton:hover {
                background:qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #5cabe9, stop:1 #8a7ce6
                );
            }
            QPushButton:pressed { padding:10px 22px 8px 22px; }
        """)
        btn_row.addWidget(self.btn_refresh_overview)
        layout.addLayout(btn_row)

        return page

    def _make_stat_card(self, title_text):

        card = QFrame()
        _ga = self._glass_alpha()
        _cr = self._corner()
        card.setStyleSheet("""
            QFrame {
                background: rgba(255,255,255,%f);
                border: 1px solid rgba(255,255,255,0.75);
                border-radius: %dpx;
            }
        """ % (_ga, _cr))
        self._glass_shadow(card, blur=22, dy=4, alpha=40)
        card.setMinimumSize(150, 108)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        value_label = QLabel("—")
        value_label.setStyleSheet(
            "font-size:32px;font-weight:700;color:#1f2d3d;background:transparent;border:none;"
        )
        value_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(value_label)

        title_label = QLabel(title_text)
        title_label.setStyleSheet(
            "font-size:12px;color:#8a97a8;background:transparent;border:none;"
        )
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        return card, value_label

    # ------------------------------------------------------------
    # 照片页（承载原有全部功能）
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
        self.btn_fav_page.clicked.connect(lambda: self._switch_page(5))
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

    def _build_favorites_page(self):
        """收藏页：展示已收藏的唯一照片，点击打开完整原图。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(16)

        title = QLabel("⭐ 收藏")
        title.setStyleSheet(
            "font-size:24px;font-weight:800;color:#1f2d3d;"
            "background:transparent;border:none;"
        )
        layout.addWidget(title)

        stats = QLabel("")
        stats.setStyleSheet("font-size:13px;color:#8a97a8;background:transparent;border:none;")
        layout.addWidget(stats)
        self._fav_stats_label = stats

        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.6);color:#3a5a7a;"
            "border:1px solid rgba(255,255,255,0.8);"
            "padding:6px 18px;border-radius:15px;font-size:12px;font-weight:600;}"
            "QPushButton:hover{background:rgba(255,255,255,0.9);}"
        )
        refresh_btn.clicked.connect(self._load_favorites_page)
        layout.addWidget(refresh_btn, alignment=Qt.AlignLeft)

        grid_container = QWidget()
        self._fav_grid_layout = QGridLayout(grid_container)
        self._fav_grid_layout.setSpacing(10)
        self._fav_grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._fav_grid_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(grid_container)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        layout.addWidget(scroll, 1)

        self._fav_paths = []          # 当前页收藏 path 列表
        self._fav_tile_map = {}       # widget → path
        return page

    def _load_favorites_page(self):
        """从数据库读取收藏并渲染照片网格（唯一照片）。"""
        from core.identity import IdentityManager
        mgr = IdentityManager()
        try:
            paths = mgr.db.list_favorites() or []
        finally:
            mgr.close()
        self._fav_paths = paths
        self._fav_stats_label.setText(f"共 {len(paths)} 张收藏照片")

        self._clear_grid(self._fav_grid_layout)
        for tile in list(self._fav_tile_map.keys()):
            self._fav_tile_map.pop(tile, None)

        if not paths:
            empty = QLabel("暂无收藏。在照片页点击「⭐ 收藏当前」添加。")
            empty.setStyleSheet("font-size:14px;color:#95a5a6;padding:30px;")
            self._fav_grid_layout.addWidget(empty, 0, 0)
            return

        cols = 6
        for idx, path in enumerate(paths):
            tile = self._render_favorite_tile(path)
            r, c = divmod(idx, cols)
            self._fav_grid_layout.addWidget(tile, r, c)

    def _render_favorite_tile(self, path):
        """单个收藏缩略图（完整原图缩略；点击预览原图；右键取消收藏）。"""
        tile = QFrame()
        tile.setFixedSize(152, 152)
        _ga = self._glass_alpha()
        _tr = self._thumb_radius()
        tile.setStyleSheet("""
            QFrame {
                background: rgba(255,255,255,%f);
                border-radius: %dpx;
                border: 1px solid rgba(255,255,255,0.8);
            }
            QFrame:hover {
                background: rgba(255,255,255,0.9);
                border: 1px solid rgba(240,130,150,0.6);
            }
        """ % (_ga, _tr))
        tile.setCursor(Qt.PointingHandCursor)
        tile.setContextMenuPolicy(Qt.CustomContextMenu)
        tile_layout = QVBoxLayout(tile)
        tile_layout.setContentsMargins(6, 6, 6, 6)

        label = QLabel()
        label.setAlignment(Qt.AlignCenter)
        label.setFixedSize(138, 138)
        label.setStyleSheet("background:transparent;border:none;")
        resolved = self._resolve_display_path(path)
        pix = self._load_pixmap_cached(resolved, QSize(138, 138))[0]
        if not pix.isNull():
            label.setPixmap(
                pix.scaled(138, 138, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        else:
            label.setText("无图")
            label.setStyleSheet(
                "background:transparent;border:none;color:#bdc3c7;font-size:10px;"
            )
        tile_layout.addWidget(label)

        for widget in (tile, label):
            widget.installEventFilter(self)
            self._fav_tile_map[widget] = path
        tile.customContextMenuRequested.connect(
            lambda _pos, p=path: self._remove_favorite(p)
        )
        return tile

    def _toggle_favorite_current(self):
        """收藏/取消收藏照片页当前预览的照片（image_path 级）。"""
        from core.identity import IdentityManager
        path = self._photo_detection_context.get("path") if self._photo_detection_context else None
        if not path:
            QMessageBox.information(self, "提示", "请先在照片页打开一张照片。")
            return
        # 收藏键用库内绝对路径（还原 backslash → 与 identity_image 一致）
        raw = path.replace("\\", "/")
        mgr = IdentityManager()
        try:
            if mgr.db.is_favorite(raw):
                mgr.db.remove_favorite(raw)
                msg = "已取消收藏"
            else:
                mgr.db.add_favorite(raw)
                msg = "已收藏"
        finally:
            mgr.close()
        self.statusBar().showMessage(f"{msg}：{os.path.basename(raw)}", 3000)

    def _remove_favorite(self, path):
        from core.identity import IdentityManager
        mgr = IdentityManager()
        try:
            mgr.db.remove_favorite(path)
        finally:
            mgr.close()
        self._load_favorites_page()

    # ------------------------------------------------------------
    # 待处理页（添加新照片 → 自动识别 → 兽装 Fursee / 人物 Face）
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

    def _build_groups_page(self, page_key, group_type_filter, page_title, default_prefix):
        """构建分组浏览页面。返回 page widget，状态存入 self._group_pages。"""

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(30, 26, 30, 26)
        page_layout.setSpacing(16)

        title = QLabel(page_title)
        title.setStyleSheet(
            "font-size:24px;font-weight:800;color:#1f2d3d;"
            "background:transparent;border:none;"
        )
        page_layout.addWidget(title)

        page_stack = QStackedWidget()

        # ===== [0] 组列表视图 =====
        list_view = QWidget()
        list_layout = QVBoxLayout(list_view)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(14)

        stats_bar = QHBoxLayout()
        stats_bar.setSpacing(12)
        stats_label = QLabel("点击刷新加载…")
        stats_label.setStyleSheet("font-size:13px;color:#5a6a7a;background:transparent;border:none;")
        stats_bar.addWidget(stats_label)
        stats_bar.addStretch()
        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.6);color:#3a5a7a;border:1px solid rgba(255,255,255,0.8);"
            "padding:6px 18px;border-radius:15px;font-size:12px;font-weight:600;}"
            "QPushButton:hover{background:rgba(255,255,255,0.85);}"
        )
        stats_bar.addWidget(refresh_btn)
        analyze_btn = QPushButton("📥 分析新照片")
        analyze_btn.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #57c78a,stop:1 #6aaee8);color:white;border:none;"
            "padding:6px 18px;border-radius:15px;font-size:12px;font-weight:600;}"
            "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #49b87c,stop:1 #5a9fd8);}"
        )
        stats_bar.addWidget(analyze_btn)
        list_layout.addLayout(stats_bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        grid_container = QWidget()
        grid_layout = QGridLayout(grid_container)
        grid_layout.setSpacing(14)
        grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(grid_container)
        list_layout.addWidget(scroll, 1)

        empty_label = QLabel("暂无数据")
        empty_label.setAlignment(Qt.AlignCenter)
        empty_label.setStyleSheet(
            "font-size:16px;color:#a5b2c2;padding:60px;background:transparent;border:none;"
        )
        empty_label.hide()
        list_layout.addWidget(empty_label)

        page_stack.addWidget(list_view)

        # ===== [1] 组内照片墙 =====
        wall_view = QWidget()
        wall_layout = QVBoxLayout(wall_view)
        wall_layout.setContentsMargins(0, 0, 0, 0)
        wall_layout.setSpacing(14)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(12)
        back_btn = QPushButton("← 返回")
        back_btn.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.6);color:#3a5a7a;"
            "border:1px solid rgba(255,255,255,0.8);"
            "padding:6px 16px;border-radius:15px;font-size:12px;font-weight:600;}"
            "QPushButton:hover{background:rgba(255,255,255,0.9);}"
        )
        top_bar.addWidget(back_btn)
        wall_title = QLabel("")
        wall_title.setStyleSheet(
            "font-size:19px;font-weight:800;color:#1f2d3d;background:transparent;border:none;"
        )
        top_bar.addWidget(wall_title)
        wall_count = QLabel("")
        wall_count.setStyleSheet("font-size:13px;color:#8a97a8;background:transparent;border:none;")
        top_bar.addWidget(wall_count)
        top_bar.addStretch()
        rename_btn = QPushButton("✏️ 重命名")
        rename_btn.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #c29ae8,stop:1 #9b8cf0);color:white;border:none;"
            "padding:6px 16px;border-radius:15px;font-size:12px;font-weight:600;}"
            "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #b48ad8,stop:1 #8a7ce6);}"
        )
        top_bar.addWidget(rename_btn)
        merge_btn = QPushButton("🔗 合并角色")
        merge_btn.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #4fd1c0,stop:1 #6aaee8);color:white;border:none;"
            "padding:6px 16px;border-radius:15px;font-size:12px;font-weight:600;}"
            "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #43c2b1,stop:1 #5a9fd8);}"
        )
        top_bar.addWidget(merge_btn)
        wall_layout.addLayout(top_bar)

        wall_scroll = QScrollArea()
        wall_scroll.setWidgetResizable(True)
        wall_scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        wall_grid_container = QWidget()
        wall_grid_layout = QGridLayout(wall_grid_container)
        wall_grid_layout.setSpacing(10)
        wall_grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        wall_grid_layout.setContentsMargins(0, 0, 0, 0)
        wall_scroll.setWidget(wall_grid_container)
        wall_layout.addWidget(wall_scroll, 1)

        page_stack.addWidget(wall_view)

        page_stack.setCurrentIndex(0)
        page_layout.addWidget(page_stack, 1)

        self._group_pages[page_key] = {
            "page_stack": page_stack,
            "group_type_filter": group_type_filter,
            "default_prefix": default_prefix,
            "stats_label": stats_label,
            "grid_layout": grid_layout,
            "grid_container": grid_container,
            "empty_label": empty_label,
            "refresh_btn": refresh_btn,
            "back_btn": back_btn,
            "wall_title": wall_title,
            "wall_count": wall_count,
            "rename_btn": rename_btn,
            "merge_btn": merge_btn,
            "wall_grid_layout": wall_grid_layout,
            "wall_grid_container": wall_grid_container,
            "current_group": None,
            "current_display_name": "",
            "groups": [],
        }

        refresh_btn.clicked.connect(lambda _, k=page_key: self._load_groups_into_page(k))
        analyze_btn.clicked.connect(lambda _, k=page_key: self._analyze_new_photos(k))
        back_btn.clicked.connect(lambda _, k=page_key: self._back_to_group_list(k))
        rename_btn.clicked.connect(lambda _, k=page_key: self._rename_current_group(k))
        merge_btn.clicked.connect(lambda _, k=page_key: self._merge_current_group(k))

        return page

    def _dedup_paths(self, paths):
        """按 path 去重保序（schema v2 同 path 多 detection 防御）。"""
        seen = set()
        out = []
        for p in paths:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return out

    def _path_md5(self, path):
        """计算照片内容 MD5（临时内存缓存，不落库）。

        仅用于 UI 照片墙「唯一照片内容」去重；key=image_path,
        value=md5 hex。读取失败返回 None（调用方按不参与 MD5 去重处理）。
        """
        if not path:
            return None
        cached = self._path_md5_cache.get(path)
        if cached is not None:
            return cached
        import hashlib
        try:
            with open(path, "rb") as f:
                m = hashlib.md5(f.read()).hexdigest()
        except OSError:
            m = None
        self._path_md5_cache[path] = m
        return m

    def _unique_photo_count(self, group):
        """组内「唯一照片内容」数量：同 path 多 detection = 1；
        同 MD5 不同 path = 1。数据库 detection 数量完全不变。"""
        seen_md5 = set()
        count = 0
        for path in self._dedup_paths(group.get("images", [])):
            m = self._path_md5(path)
            if m is not None:
                if m in seen_md5:
                    continue
                seen_md5.add(m)
            count += 1
        return count

    # ---------- detection 级展示支持（Phase 2.5，只读查询，不改后端） ----------
    @staticmethod
    def _resolve_display_path(path):
        """将数据库中的相对路径解析为项目内的可读路径，不回写原值。"""
        if not path:
            return ""
        raw_path = os.fspath(path)
        if os.path.isabs(raw_path):
            if Path(raw_path).is_file():
                return raw_path
            fallback = _project_root / "photos" / Path(raw_path).name
            if fallback.is_file():
                return str(fallback)
            return raw_path
        candidate = _project_root / Path(raw_path)
        if candidate.is_file():
            return str(candidate)
        fallback = _project_root / "photos" / Path(raw_path).name
        return str(fallback) if fallback.is_file() else raw_path

    def _load_pixmap_cached(self, path, target_size=None):
        """按显示尺寸读取并缓存图片，避免界面反复解码原始大图。"""
        resolved = self._resolve_display_path(path)
        if not resolved:
            return QPixmap(), QSize()

        size_key = None
        if (
            target_size
            and target_size.isValid()
            and target_size.width() > 0
            and target_size.height() > 0
        ):
            size_key = (target_size.width(), target_size.height())
        cache_key = (resolved, size_key)
        cached = self._pixmap_cache.get(cache_key)
        if cached is not None:
            return cached

        reader = QImageReader(resolved)
        reader.setAutoTransform(True)
        original_size = reader.size()
        if size_key and original_size.isValid():
            requested_size = original_size.scaled(
                QSize(*size_key),
                Qt.KeepAspectRatio,
            )
            if requested_size.isValid():
                reader.setScaledSize(requested_size)

        image = reader.read()
        pixmap = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
        result = (pixmap, original_size)

        # 对浏览场景保留足够的近期图片，同时防止长时间运行无限占内存。
        if len(self._pixmap_cache) >= 512:
            self._pixmap_cache.pop(next(iter(self._pixmap_cache)))
        self._pixmap_cache[cache_key] = result
        return result

    def _fetch_group_detections(self, group):
        """只读查询该组的 (path, detection_index) → (bbox, embedding_type) 映射。

        优先使用 get_groups() 已带出的 detection 数据；旧调用方或异常
        数据没有 detections 时，再通过现有公开方法查询。任何失败返回
        空 dict，UI 自动回退完整原图显示（legacy/异常数据安全兜底）。
        """
        cid = group.get("character_id") or ""
        if not cid:
            return {}
        rows = group.get("detections") or []
        if rows:
            det_map = {}
            for row in rows:
                if not row:
                    continue
                key = (row.get("image_path"), row.get("detection_index", 0))
                det_map[key] = (
                    row.get("bbox"),
                    row.get("embedding_type", ""),
                )
            if det_map:
                return det_map
        try:
            from core.identity import IdentityManager
            mgr = IdentityManager()
            try:
                rows = mgr.db.get_images_by_group(cid) or []
            finally:
                mgr.close()
        except Exception as e:
            print(f"[分组页] detection 查询失败（回退原图）: {e}")
            return {}
        det_map = {}
        for row in rows:
            if not row:
                continue
            key = (row.get("image_path"), row.get("detection_index"))
            det_map[key] = (row.get("bbox"), row.get("embedding_type"))
        return det_map

    @staticmethod
    def _parse_bbox(bbox_json, img_w, img_h):
        """bbox JSON → QRect（像素）。兼容两种历史格式：

        - fursuit_fursee：绝对像素 [x1, y1, x2, y2]（P-C4-C2 起写入）
        - fursuit_visual / face：0-1 归一化 [x1, y1, x2, y2]（legacy）

        判定规则：全部值 ≤ 1.0 且图片宽高 > 1 → 归一化坐标（乘以宽高）；
        否则视为绝对像素。无效输入返回 None（调用方回退完整原图）。
        """
        if not bbox_json or img_w <= 0 or img_h <= 0:
            return None
        try:
            vals = json.loads(bbox_json) if isinstance(bbox_json, str) else bbox_json
            x1, y1, x2, y2 = (float(v) for v in vals[:4])
        except (ValueError, TypeError, ZeroDivisionError):
            return None
        if any(v != v for v in (x1, y1, x2, y2)):  # NaN 防御
            return None
        if x1 >= x2 or y1 >= y2:
            return None
        if x2 <= 1.0 and y2 <= 1.0 and x1 >= 0.0 and y1 >= 0.0 and img_w > 1 and img_h > 1:
            x1, y1, x2, y2 = x1 * img_w, y1 * img_h, x2 * img_w, y2 * img_h
        # clamp 到图片边界并取整
        x1 = max(0, min(int(x1), img_w - 1))
        y1 = max(0, min(int(y1), img_h - 1))
        x2 = max(x1 + 1, min(int(x2), img_w))
        y2 = max(y1 + 1, min(int(y2), img_h))
        return QRect(x1, y1, x2 - x1, y2 - y1)

    @staticmethod
    def _display_rect_to_raw(rect, raw_w, raw_h, transform):
        """把「显示方向」的 bbox rect 映射回「原始图像方向」坐标。

        Fursee 的 bbox 由 YOLO 在已按 EXIF 旋转的图上检测（显示方向）；
        QImageReader.setClipRect 需要原始图像坐标，故需逆变换。
        仅处理 3 种常见方向（None/Rotate180/Rotate90=EXIF6）；其余
        组合（镜像等极罕见）返回 None → 调用方回退整图。
        """
        T = QImageIOHandler.Transformation
        if transform == T.TransformationNone:
            return QRect(rect)
        if transform == T.TransformationRotate180:
            return QRect(
                raw_w - rect.x() - rect.width(),
                raw_h - rect.y() - rect.height(),
                rect.width(),
                rect.height(),
            )
        if transform == T.TransformationRotate90:
            # EXIF 6（显示 = 原始顺时针旋转 90°）：逆映射 x=yd, y=H-(xd+wd)
            return QRect(
                rect.y(),
                raw_h - rect.x() - rect.width(),
                rect.height(),
                rect.width(),
            )
        return None  # 镜像/270° 组合，回退整图（安全）

    def _load_detection_crop(self, path, bbox_json, target_size=None):
        """方案A：先裁后缩加载 detection 主体图（2026-08-24 修复）。

        旧实现「先缩整图→再裁 bbox」，裁剪分辨率 = 目标尺寸×bbox占比，
        小主体被压成几十像素再放大 → 缩略图糊。本方法用
        QImageReader.setClipRect(原始坐标 bbox) + setScaledSize(目标)，
        解码器直接输出「裁剪+缩放」结果，裁剪分辨率 = min(bbox 原始像素,
        目标尺寸)，保证清晰且不整图解码（内存友好）。

        EXIF 旋转：bbox 为显示方向坐标，先经 _display_rect_to_raw 逆映射
        到原始坐标再裁剪；setAutoTransform 负责把输出旋转回显示方向。
        """
        resolved = self._resolve_display_path(path)
        if not resolved:
            return QPixmap()
        size_key = None
        if (
            target_size
            and target_size.isValid()
            and target_size.width() > 0
            and target_size.height() > 0
        ):
            size_key = (target_size.width(), target_size.height())
        cache_key = (resolved, bbox_json, size_key)
        cached = self._det_crop_cache.get(cache_key)
        if cached is not None:
            return cached

        probe = QImageReader(resolved)
        raw_size = probe.size()
        if not raw_size.isValid():
            return QPixmap()
        transform = probe.transformation()

        rect = self._parse_bbox(
            bbox_json,
            raw_size.width(),
            raw_size.height(),
        )
        if rect is None:
            return QPixmap()
        raw_rect = self._display_rect_to_raw(
            rect,
            raw_size.width(),
            raw_size.height(),
            transform,
        )
        if raw_rect is None:
            return QPixmap()

        reader = QImageReader(resolved)
        reader.setAutoTransform(True)
        reader.setClipRect(raw_rect)
        # 注意：不用 setScaledSize —— 它与 clipRect 组合时走 JPEG DCT 快速缩放，
        # 输出宽高比会失真（实测 0.94 比例的 bbox 输出 0.8 比例）。改为裁剪区域
        # 原始分辨率解码（JPEG 插件支持局部解码，内存可控）后手动等比缩放，
        # 比例严格等于 bbox、清晰度 = min(bbox 原始像素, 目标尺寸)。
        image = reader.read()
        pixmap = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
        if pixmap.isNull():
            return pixmap
        if size_key:
            pixmap = pixmap.scaled(
                QSize(*size_key),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )

        if len(self._det_crop_cache) >= 512:
            self._det_crop_cache.pop(next(iter(self._det_crop_cache)))
        self._det_crop_cache[cache_key] = pixmap
        return pixmap

    def _pixmap_for_detection(self, path, det_info, target_size=None):
        """加载 path 的 QPixmap；若 det_info 含有效 bbox → 返回裁剪后的主体图。

        方案A（2026-08-24）：有 bbox 时走 _load_detection_crop「先裁后缩」，
        裁剪分辨率 = min(bbox 原始像素, 目标尺寸)，修复旧实现「先缩后裁」
        导致的小图放大模糊。bbox 无效 / 解析失败 / 文件缺失 → 回退完整
        原图（安全兜底）。
        """
        bbox_json = det_info[0] if det_info else None
        if bbox_json:
            cropped = self._load_detection_crop(path, bbox_json, target_size)
            if not cropped.isNull():
                return cropped
        pix, _ = self._load_pixmap_cached(path, target_size)
        return pix

    def _pixmap_for_full_preview(self, path, bbox=None, detection_index=None):
        """加载完整原图并在预览尺寸上叠加当前 detection 的 bbox。"""
        pix, original_size = self._load_pixmap_cached(
            path,
            self.preview_label.size(),
        )
        if pix.isNull():
            return pix

        target_size = self.preview_label.size()
        if (
            target_size.width() <= 0
            or target_size.height() <= 0
            or not original_size.isValid()
        ):
            return pix
        scaled = pix.copy()
        if not bbox:
            return scaled

        rect = self._parse_bbox(
            bbox,
            original_size.width(),
            original_size.height(),
        )
        if rect is None:
            return scaled

        scale_x = scaled.width() / original_size.width()
        scale_y = scaled.height() / original_size.height()
        overlay_rect = QRect(
            int(round(rect.x() * scale_x)),
            int(round(rect.y() * scale_y)),
            max(1, int(round(rect.width() * scale_x))),
            max(1, int(round(rect.height() * scale_y))),
        )
        painter = QPainter(scaled)
        # 保留完整原图，但压暗 bbox 外区域，让当前角色 detection 更明确。
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 95))
        painter.drawRect(scaled.rect())

        scaled_rect = QRect(
            int(round(rect.x() * scale_x)),
            int(round(rect.y() * scale_y)),
            max(1, int(round(rect.width() * scale_x))),
            max(1, int(round(rect.height() * scale_y))),
        )
        highlighted = pix.copy(scaled_rect).scaled(
            overlay_rect.size(),
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation,
        )
        painter.drawPixmap(overlay_rect.topLeft(), highlighted)

        pen = QPen(QColor("#e74c3c"))
        pen.setWidth(max(2, int(round(min(scale_x, scale_y) * 4))))
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(overlay_rect)
        if detection_index is not None:
            painter.setPen(QColor("#e74c3c"))
            painter.drawText(
                overlay_rect.x() + 4,
                max(18, overlay_rect.y() + 18),
                f"detection #{detection_index}",
            )
        painter.end()
        return scaled

    def _compute_display_name(self, group, idx, default_prefix):
        """名称优先级：group.name 非空 → 用户名；空 → 运行时默认名。"""
        name = (group.get("name") or "").strip()
        if name:
            return name
        return f"未命名{default_prefix} #{idx:03d}"

    @staticmethod
    def _format_source_types(group):
        types = group.get("source_types") or []
        if not types:
            return ""
        if types == ["fursuit_fursee"]:
            return "Fursee"
        if types == ["fursuit_visual"]:
            return "Legacy"
        if types == ["face"]:
            return "Face"
        return " + ".join(sorted({t for t in types if t}))

    @staticmethod
    def _format_group_category(group):
        """角色类别 + 识别模型，供卡片/详情显示（不暴露 embedding 等参数）。

        返回 "兽装角色 · Fursee" / "人物角色 · Face" / ""。
        """
        types = group.get("source_types") or []
        if types == ["fursuit_fursee"]:
            return "兽装角色 · Fursee"
        if types == ["face"]:
            return "人物角色 · Face"
        if "fursuit_fursee" in types:
            return "兽装角色 · Fursee"
        if "face" in types:
            return "人物角色 · Face"
        return ""

    def _clear_grid(self, grid_layout):
        """清空 QGridLayout 内全部 widget。"""
        while grid_layout.count():
            item = grid_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

    def _analyze_new_photos(self, page_key):
        """一键增量分析：photos/ 未入库照片 → Fursee 入库 → 增量归组 → 刷新。

        后台线程执行（不阻塞 GUI）：仅处理未入库照片，聚类只走
        incremental_assign（不重跑 DBSCAN、不拆散已有组）。完成后自动刷新。
        """
        if getattr(self, "_scan_worker", None) and self._scan_worker.isRunning():
            self.statusBar().showMessage("分析已在进行中…", 3000)
            return
        state = self._group_pages.get(page_key)
        if state is None:
            return
        self._scan_worker_page = page_key
        worker = _ScanDirWorker()
        worker.progress_updated.connect(self._on_scan_progress)
        worker.finished_ok.connect(self._on_scan_done)
        worker.failed.connect(self._on_scan_failed)
        self._scan_worker = worker
        state["refresh_btn"].setEnabled(False)
        self.statusBar().showMessage("正在分析新照片…（后台运行，界面可继续操作）")
        worker.start()

    def _on_scan_progress(self, current, total, status=None):
        self.statusBar().showMessage(
            f"分析新照片 {current}/{total}：{status or ''}".strip(), 1500
        )

    def _on_scan_done(self, result):
        state = self._group_pages.get(self._scan_worker_page)
        if state is not None:
            state["refresh_btn"].setEnabled(True)
        self._load_groups_into_page(self._scan_worker_page)
        self.statusBar().showMessage("分析完成，列表已刷新", 8000)
        QMessageBox.information(
            self,
            "分析完成",
            f"扫描 {result.get('scanned', 0)} 张\n"
            f"新增入库 {result.get('new', 0)} 张\n"
            f"已存在跳过 {result.get('skipped', 0)} 张\n"
            f"失败 {result.get('failed', 0)} 张\n\n"
            f"新角色已按增量分配（0.79）归组，列表已刷新。",
        )

    def _on_scan_failed(self, err):
        state = self._group_pages.get(self._scan_worker_page)
        if state is not None:
            state["refresh_btn"].setEnabled(True)
        QMessageBox.critical(self, "分析失败", f"分析新照片时出错：{err}")

    def _load_groups_into_page(self, page_key):
        """从 IdentityManager.get_groups() 读取并渲染组列表（只读）。"""
        state = self._group_pages.get(page_key)
        if state is None:
            return
        group_type_filter = state["group_type_filter"]
        default_prefix = state["default_prefix"]

        self._clear_grid(state["grid_layout"])
        self._group_page_pending.pop(page_key, None)
        # 清理旧卡片映射
        for card in list(self._card_group_map.keys()):
            if self._card_group_map[card][0] == page_key:
                self._card_group_map.pop(card, None)
        state["empty_label"].show()
        state["_has_data"] = False

        groups = []
        try:
            from core.identity import IdentityManager
            mgr = IdentityManager()
            try:
                groups = mgr.get_groups(group_type=group_type_filter) or []
            finally:
                mgr.close()
        except Exception as e:
            print(f"[分组页 {page_key}] 读取失败: {e}")
            groups = []

        state["groups"] = groups

        if not groups:
            state["stats_label"].setText("暂无数据")
            return

        state["_has_data"] = True
        state["empty_label"].hide()

        total_photos = sum(self._unique_photo_count(g) for g in groups)
        state["stats_label"].setText(
            f"共 {len(groups)} 个角色组 · {total_photos} 张照片"
        )

        cols = 4
        pending = []
        for idx, group in enumerate(groups):
            display_name = self._compute_display_name(group, idx + 1, default_prefix)
            pending.append((idx, group, display_name))
        token = object()
        self._group_page_pending[page_key] = {
            "items": pending,
            "index": 0,
            "cols": cols,
            "token": token,
        }
        self._append_group_cards(page_key, token=token)

    def _append_group_cards(self, page_key, batch_size=12, token=None):
        state = self._group_pages.get(page_key)
        pending = self._group_page_pending.get(page_key)
        if state is None or not pending:
            return
        if token is not None and pending.get("token") is not token:
            return
        items = pending.get("items") or []
        start = pending.get("index", 0)
        end = min(start + batch_size, len(items))
        for idx, group, display_name in items[start:end]:
            card = self._render_group_card(group, display_name, page_key)
            r, c = divmod(idx, pending.get("cols", 4))
            state["grid_layout"].addWidget(card, r, c)
        pending["index"] = end
        if end < len(items):
            current_token = pending.get("token")
            QTimer.singleShot(
                0,
                lambda k=page_key, t=current_token: self._append_group_cards(
                    k,
                    batch_size,
                    t,
                ),
            )
        else:
            self._group_page_pending.pop(page_key, None)

    def _render_group_card(self, group, display_name, page_key):
        """渲染单个角色组卡片（QFrame，左键进组 / 右键重命名）。"""
        card = AuroraGlassCard()
        card.setFixedSize(226, 248)
        # 玻璃底 / 极光 / 高光 / 描边由 AuroraGlassCard.paintEvent 绘制
        self._glass_shadow(card, blur=20, dy=4, alpha=38)
        card.setCursor(Qt.PointingHandCursor)
        card.setContextMenuPolicy(Qt.CustomContextMenu)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        cover_path = group.get("cover_image") or (group.get("images") or [""])[0]
        cover_label = QLabel()
        cover_label.setFixedSize(200, 148)
        cover_label.setAlignment(Qt.AlignCenter)
        cover_label.setStyleSheet("background:rgba(240,244,250,0.40);border-radius:12px;")
        # 封面优先选该原图里置信度最高的 detection，避免同图多 detection
        # 时封面随机落到别的主体上。
        cover_det = None
        cover_candidates = [
            det for det in (group.get("detections") or [])
            if det and det.get("image_path") == cover_path
        ]
        if cover_candidates:
            cover_det = max(
                cover_candidates,
                key=lambda det: (
                    float(det.get("confidence") or 0.0),
                    -(int(det.get("detection_index") or 0)),
                ),
            )
        pix = self._pixmap_for_detection(
            cover_path,
            (
                cover_det.get("bbox"),
                cover_det.get("embedding_type"),
            ) if cover_det else None,
            cover_label.size(),
        )
        if not pix.isNull():
            cover_label.setPixmap(
                pix.scaled(200, 148, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            )
        else:
            cover_label.setText("无封面")
            cover_label.setStyleSheet(
                "background:rgba(240,244,250,0.7);border-radius:12px;"
                "color:#b9c4d2;font-size:12px;"
            )
        layout.addWidget(cover_label)

        name_label = QLabel(f"{display_name}")
        name_label.setStyleSheet(
            "font-size:14.5px;font-weight:700;color:#1f2d3d;"
            "background:transparent;border:none;"
        )
        name_label.setWordWrap(True)
        layout.addWidget(name_label)

        # 类别胶囊 + 次级信息行
        meta_row = QHBoxLayout()
        meta_row.setSpacing(6)
        category_text = self._format_group_category(group)
        if category_text:
            category_label = QLabel(category_text)
            category_label.setStyleSheet(
                "font-size:10.5px;color:#5b7bd5;font-weight:700;"
                "background:rgba(120,150,255,0.14);border-radius:9px;"
                "padding:2px 9px;border:none;"
            )
            meta_row.addWidget(category_label)

        source_text = self._format_source_types(group)
        if source_text:
            source_label = QLabel(source_text)
            source_label.setStyleSheet(
                "font-size:10.5px;color:#8a97a8;background:transparent;border:none;"
            )
            meta_row.addWidget(source_label)
        meta_row.addStretch()
        layout.addLayout(meta_row)

        count = self._unique_photo_count(group)
        count_label = QLabel(f"{count} 张照片")
        count_label.setStyleSheet(
            "font-size:11.5px;color:#8a97a8;background:transparent;border:none;"
        )
        layout.addWidget(count_label)

        self._card_group_map[card] = (page_key, group, display_name)
        card.installEventFilter(self)
        card.customContextMenuRequested.connect(
            lambda pos, c=card, k=page_key: self._rename_group_via_card(k, c)
        )
        return card

    def eventFilter(self, obj, event):
        """卡片/缩略图左键点击派发 + hover 浮起效果。"""
        et = event.type()
        # ── hover 浮起：角色卡片 / 照片墙 tile（frame 级）──
        if et in (QEvent.HoverEnter, QEvent.HoverLeave):
            is_frame = isinstance(obj, QFrame)
            if obj in self._card_group_map or (is_frame and obj in self._tile_path_map):
                if et == QEvent.HoverEnter:
                    if obj in self._card_group_map:
                        # Aurora 角色卡：浮起幅度受 aurora.hover_lift 控制
                        # （0 = 不浮起，保持静态阴影）
                        lift = max(0.0, float(S.get("aurora.hover_lift", 0.5)))
                        if lift > 0.01:
                            eff = QGraphicsDropShadowEffect(obj)
                            eff.setBlurRadius(max(1, int(12 + 12 * lift)))
                            eff.setOffset(0, int(2 + 6 * lift))
                            eff.setColor(QColor(40, 70, 130, int(40 + 80 * lift)))
                            obj.setGraphicsEffect(eff)
                    else:
                        eff = QGraphicsDropShadowEffect(obj)
                        eff.setBlurRadius(18)
                        eff.setOffset(0, 5)
                        eff.setColor(QColor(40, 70, 130, 80))
                        obj.setGraphicsEffect(eff)
                else:
                    if obj in self._card_group_map:
                        self._glass_shadow(obj, blur=20, dy=4, alpha=38)  # 恢复静态阴影
                    else:
                        obj.setGraphicsEffect(None)
                # 不拦截：hover 事件继续传播，供 AuroraGlassCard 内部驱动极光
                return False
        if et == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            if obj in self._card_group_map:
                page_key, group, display_name = self._card_group_map[obj]
                self._open_group(page_key, group, display_name)
                return True
            if obj in self._tile_path_map:
                page_key, group, image_path, detection_index = self._tile_path_map[obj]
                self._open_photo_in_photo_page(group, image_path, detection_index)
                return True
            if obj in self._fav_tile_map:
                path = self._fav_tile_map[obj]
                self._preview_favorite(path)
                return True
        return super().eventFilter(obj, event)

    def _preview_favorite(self, path):
        """收藏页点击 → 照片页预览完整原图（复用现有预览链路）。"""
        resolved = self._resolve_display_path(path)
        self.image_list = [resolved]
        self.image_list_widget.clear()
        item = QListWidgetItem(os.path.basename(path))
        pix = QPixmap(resolved)
        if not pix.isNull():
            item.setIcon(
                QIcon(pix.scaled(110, 110, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            )
        self.image_list_widget.addItem(item)
        self._photo_detection_context = {
            "row": 0, "path": resolved, "bbox": None,
            "detection_index": None, "group_name": "收藏",
        }
        self.nav_list.setCurrentRow(1)
        self.image_list_widget.setCurrentRow(0)
        self.show_preview(0)

    def _open_group(self, page_key, group, display_name):
        """点击组卡片 → 切到组内照片墙。

        照片墙显示单位 =「唯一照片内容」：
        ① 同一 image_path 多 detection → 只显示 1 格，取当前组内该
           image_path 的 detection 中 confidence 最高者做 bbox crop；
        ② 不同 image_path 但 MD5 内容相同 → 只显示 1 格（局部 seen_md5，
           每次 _open_group 新建，跨角色组不共享去重）；
        ③ bbox 无效回退完整原图；点击仍打开完整原图。
        数据库 detection 数据完全不变。
        """
        state = self._group_pages.get(page_key)
        if state is None:
            return
        det_map = self._fetch_group_detections(group)
        # 第一层：按 image_path 聚合 → 组内每个 path 选 confidence 最高的 det
        # （保持 group["images"] 顺序）。
        best_det_by_path = {}
        for det in (group.get("detections") or []):
            if not det or not det.get("image_path"):
                continue
            p = det["image_path"]
            cur = best_det_by_path.get(p)
            if cur is None or float(det.get("confidence") or 0.0) > float(cur.get("confidence") or 0.0):
                best_det_by_path[p] = det
        # 第二层：MD5 内容去重（局部集合，跨组不共享）。
        seen_md5 = set()
        members = []
        for path in self._dedup_paths(group.get("images", [])):
            m = self._path_md5(path)
            if m is not None:
                if m in seen_md5:
                    continue
                seen_md5.add(m)
            det = best_det_by_path.get(path)
            det_idx = int(det.get("detection_index") or 0) if det else 0
            members.append((path, det_idx))
        # 兜底：无 images（legacy 异常数据）但 det_map 有 → 按 det 键保序。
        if not members and det_map:
            for key in sorted(det_map.keys(), key=lambda k: (k[0] or "", k[1] or 0)):
                if key[0] is None:
                    continue
                members.append(key)
        state["current_group"] = group
        state["current_display_name"] = display_name
        state["current_members"] = members
        state["current_det_map"] = det_map

        state["wall_title"].setText(f"🐾  {display_name}")
        category_text = self._format_group_category(group)
        if category_text:
            state["wall_count"].setText(
                f"{category_text} · {len(members)} 张照片"
            )
        else:
            state["wall_count"].setText(f"{len(members)} 张照片")

        self._clear_grid(state["wall_grid_layout"])
        for tile in list(self._tile_path_map.keys()):
            if self._tile_path_map[tile][0] == page_key:
                self._tile_path_map.pop(tile, None)

        cols = 6
        for idx, (path, det_idx) in enumerate(members):
            tile = self._render_photo_tile(path, det_idx, det_map.get((path, det_idx)), page_key, group)
            r, c = divmod(idx, cols)
            state["wall_grid_layout"].addWidget(tile, r, c)

        state["page_stack"].setCurrentIndex(1)

    def _render_photo_tile(self, path, det_idx, det_info, page_key, group):
        """渲染单张主体缩略图并显示 detection 编号。

        显示该 detection 的 bbox 裁剪；bbox 无效回退完整原图。
        """
        tile = QFrame()
        tile.setFixedSize(124, 140)
        _ga = self._glass_alpha()
        _tr = self._thumb_radius()
        tile.setStyleSheet("""
            QFrame {
                background: rgba(255,255,255,%f);
                border-radius: %dpx;
                border: 1px solid rgba(255,255,255,0.8);
            }
            QFrame:hover {
                background: rgba(255,255,255,0.9);
                border: 1px solid rgba(130,170,240,0.7);
            }
        """ % (_ga, _tr))
        tile.setCursor(Qt.PointingHandCursor)
        tile.setToolTip(f"detection #{det_idx}")
        tile_layout = QVBoxLayout(tile)
        tile_layout.setContentsMargins(5, 4, 5, 4)
        tile_layout.setSpacing(2)

        image_label = QLabel()
        image_label.setFixedSize(110, 110)
        image_label.setAlignment(Qt.AlignCenter)
        image_label.setStyleSheet("background:transparent;border:none;")
        image_label.setCursor(Qt.PointingHandCursor)
        pix = self._pixmap_for_detection(path, det_info, image_label.size())
        if not pix.isNull():
            image_label.setPixmap(
                pix.scaled(110, 110, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        else:
            image_label.setText("无图")
            image_label.setStyleSheet(
                "background:transparent;border:none;color:#b9c4d2;font-size:10px;"
            )

        caption = QLabel(f"detection #{det_idx}")
        caption.setFixedHeight(15)
        caption.setAlignment(Qt.AlignCenter)
        caption.setStyleSheet(
            "background:transparent;border:none;color:#7c8ba0;font-size:10px;"
        )
        caption.setCursor(Qt.PointingHandCursor)

        tile_layout.addWidget(image_label, 0, Qt.AlignCenter)
        tile_layout.addWidget(caption, 0, Qt.AlignCenter)

        mapping = (page_key, group, path, det_idx)
        for widget in (tile, image_label, caption):
            self._tile_path_map[widget] = mapping
            widget.installEventFilter(self)
        return tile

    def _back_to_group_list(self, page_key):
        """返回组列表。"""
        state = self._group_pages.get(page_key)
        if state is None:
            return
        state["page_stack"].setCurrentIndex(0)

    def _open_photo_in_photo_page(self, group, image_path, detection_index=None):
        """点击照片墙缩略图 → 切到照片页 + 填充该组照片 + 选中预览。

        复用 Phase 1 的 image_list_widget + show_preview，不新增预览 widget。
        detection_index 只用于保留点击来源的 detection 语义；照片页仍显示
        该角色出现过的完整原图。
        """
        raw_images = self._dedup_paths(group.get("images", []))
        if image_path not in raw_images:
            return
        images = [self._resolve_display_path(path) for path in raw_images]
        det_map = self._fetch_group_detections(group)
        selected_info = (
            det_map.get((image_path, detection_index))
            if detection_index is not None
            else None
        )
        idx = raw_images.index(image_path)
        self.image_list = images
        self._photo_detection_context = {
            "row": idx,
            "path": self._resolve_display_path(image_path),
            "bbox": selected_info[0] if selected_info else None,
            "detection_index": detection_index,
            "group_name": self._compute_display_name(
                group, 1, "角色"
            ),
        }
        self.image_list_widget.clear()
        for path in images:
            item = QListWidgetItem(os.path.basename(path))
            pix = QPixmap(self._resolve_display_path(path))
            if not pix.isNull():
                item.setIcon(
                    QIcon(pix.scaled(110, 110, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                )
            self.image_list_widget.addItem(item)
        self.nav_list.setCurrentRow(1)
        self.image_list_widget.setCurrentRow(idx)
        self.statusBar().showMessage(
            f"已在照片页打开：{os.path.basename(image_path)}"
            + (f"（detection {detection_index}）" if detection_index is not None else "")
        )

    def _rename_group_via_card(self, page_key, card):
        """组卡片右键 → 重命名（定位到该卡片对应的组）。"""
        info = self._card_group_map.get(card)
        if info is None:
            return
        _, group, _ = info
        self._do_rename(page_key, group)

    def _rename_current_group(self, page_key):
        """组内照片墙顶部 ✏️ → 重命名当前组。"""
        state = self._group_pages.get(page_key)
        if state is None:
            return
        group = state.get("current_group")
        if group is None:
            return
        self._do_rename(page_key, group)

    def _merge_current_group(self, page_key):
        """将选中的其他同类型角色组并入当前组，保留 detection 全字段。"""
        state = self._group_pages.get(page_key)
        if state is None:
            return
        target = state.get("current_group")
        if not target:
            return
        target_id = target.get("character_id") or ""
        candidates = [
            group for group in state.get("groups", [])
            if group.get("character_id")
            and group.get("character_id") != target_id
            and group.get("type") == target.get("type")
        ]
        if not candidates:
            QMessageBox.information(self, "无法合并", "当前页面没有可合并的同类型角色组。")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("合并角色组")
        dialog.resize(520, 560)
        layout = QVBoxLayout(dialog)
        hint = QLabel("选择要并入当前角色组的其他角色组：")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        group_list = QListWidget()
        group_list.setSelectionMode(QAbstractItemView.MultiSelection)
        page_index = {
            group.get("character_id"): idx + 1
            for idx, group in enumerate(state.get("groups", []))
        }
        for group in candidates:
            idx = page_index.get(group.get("character_id"), 0)
            display_name = self._compute_display_name(
                group, idx, state.get("default_prefix", "角色")
            )
            item = QListWidgetItem(
                f"{display_name} · {self._unique_photo_count(group)} 张照片"
            )
            item.setData(Qt.UserRole, group.get("character_id"))
            group_list.addItem(item)
        layout.addWidget(group_list, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return
        source_ids = [
            item.data(Qt.UserRole)
            for item in group_list.selectedItems()
            if item.data(Qt.UserRole)
        ]
        if not source_ids:
            QMessageBox.information(self, "未选择角色组", "请至少选择一个要合并的角色组。")
            return
        confirm = QMessageBox.question(
            self,
            "确认合并",
            f"将 {len(source_ids)} 个角色组合并到“"
            f"{self._compute_display_name(target, page_index.get(target_id, 1), state.get('default_prefix', '角色'))}”，"
            "并保留所有 detection、裁剪框和特征数据？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            from core.identity import IdentityManager
            mgr = IdentityManager()
            try:
                result = mgr.merge_groups(target_id, source_ids)
            finally:
                mgr.close()
        except Exception as e:
            QMessageBox.critical(self, "合并失败", f"角色组合并失败：{e}")
            return

        state["page_stack"].setCurrentIndex(0)
        state["current_group"] = None
        self._load_groups_into_page(page_key)
        self.statusBar().showMessage(
            f"已合并 {len(result.get('source_ids', source_ids))} 个角色组，"
            f"保留 {result.get('moved', 0)} 条 detection"
        )

    def _do_rename(self, page_key, group):
        """执行重命名：弹输入框 → 调 update_name → 刷新当前页。

        仅写 identity_group.name 列（schema v1 起就有的字段），不改
        schema / character_id / 聚类 / DBSCAN / Fursee。空名或取消不写。
        """
        character_id = group.get("character_id") or ""
        if not character_id:
            return
        old_name = (group.get("name") or "").strip()
        text, ok = QInputDialog.getText(
            self, "重命名角色组",
            "输入新名称（留空或取消则保持原状，不写库）：",
            text=old_name,
        )
        if not ok:
            return
        new_name = text.strip()
        if not new_name:
            return  # 空白：不写库（运行时显示默认名）
        try:
            from core.identity import IdentityManager
            mgr = IdentityManager()
            try:
                mgr.update_name(character_id, new_name)
            finally:
                mgr.close()
        except Exception as e:
            QMessageBox.critical(self, "重命名失败", f"写入名称失败：{e}")
            return
        group["name"] = new_name  # 更新内存，避免刷新前显示旧值
        self._load_groups_into_page(page_key)  # 重新读取（反映新名 + 重排序）
        self.statusBar().showMessage(f"已重命名：{new_name}")

    # ------------------------------------------------------------
    # 页面切换
    # ------------------------------------------------------------

    def _switch_page(self, row):

        if row < 0 or row >= self.content_stack.count():
            return

        self.content_stack.setCurrentIndex(row)
        self._fade_in_page()

        # 切到总览页时刷新统计（构造期间 _ui_ready=False 不触发，
        # 避免测试进程打开真实库；启动后由 QTimer / 用户点击触发）
        if row == 0 and self._ui_ready:
            self._refresh_overview()

        # Phase 2：切到分组页（兽装2/人物3/角色4）时懒加载组列表
        # （同样受 _ui_ready 保护，避免测试进程触发后端读取）
        if self._ui_ready:
            page_key_map = {2: "fursuit", 3: "person", 4: "character"}
            key = page_key_map.get(row)
            if key and not self._group_page_loaded.get(key, False):
                self._load_groups_into_page(key)
                self._group_page_loaded[key] = True
            # Phase 3-1：收藏页（row 5）懒加载收藏列表
            if row == 5:
                self._load_favorites_page()
            # Phase 3-3：设置页（row 7）懒刷新状态
            if row == 7:
                self._refresh_settings_page()

    def _fade_in_page(self):
        """页面切换轻微淡入（时长受动画速度参数控制，动画关闭时跳过）。"""
        if not S.get("ui.animation", True):
            return
        page = self.content_stack.currentWidget()
        if page is None:
            return
        speed = float(S.get("ui.animation_speed", 1.0))
        eff = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", self)
        anim.setDuration(max(40, int(140 * speed)))
        anim.setStartValue(0.35)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(lambda: page.setGraphicsEffect(None))
        anim.start(QPropertyAnimation.DeleteWhenStopped)
        self._page_fade_anim = anim

    # ------------------------------------------------------------
    # 总览数据
    # ------------------------------------------------------------

    def _refresh_overview(self):
        """读取真实后端数据刷新总览统计。

        全程 try/except 容错：任何后端读取失败时卡片显示「—」，
        不影响窗口其余功能。
        """
        try:
            stats = self._compute_overview_stats()
        except Exception as e:
            print(f"[总览] 统计读取失败: {e}")
            stats = {}

        mapping = {
            "analyzed":       "analyzed",
            "fursuit_photos": "fursuit_photos",
            "person_photos":  "person_photos",
            "group_count":    "group_count",
            "feedback":       "feedback",
        }
        for key, stat_key in mapping.items():
            label = self._stat_value_labels.get(key)
            if label is None:
                continue
            val = stats.get(stat_key, "—")
            label.setText(str(val))

        # 收藏：真实计数（favorite_image 表）
        fav_label = self._stat_value_labels.get("favorites")
        if fav_label is not None:
            try:
                from core.identity import IdentityManager
                mgr = IdentityManager()
                try:
                    n_fav = len(mgr.db.list_favorites() or [])
                finally:
                    mgr.close()
            except Exception:
                n_fav = 0
            fav_label.setText(str(n_fav))
            fav_label.setStyleSheet(
                "font-size:18px;font-weight:bold;color:#e74c3c;"
            )

        # 待处理摘要
        analyzed = stats.get("analyzed", 0)
        avg_q = stats.get("avg_quality", 0.0)
        group_count = stats.get("group_count", 0)
        fur_grp = stats.get("fursuit_group_count", 0)
        per_grp = stats.get("person_group_count", 0)
        pending_text = (
            f"已分析照片：{analyzed} 张　·　平均 AI 置信度：{avg_q*100:.1f}%\n"
            f"角色分组：{group_count} 组（兽装 {fur_grp} / 人物 {per_grp}）\n"
            f"提示：打开文件夹可查看待分析照片。"
        )
        self._pending_label.setText(pending_text)

    def _compute_overview_stats(self):
        """从现有后端 API 读取真实统计数字（只读，不写库/缓存）。

        数据源：
          - AnalysisCache.get_cache() → 已分析照片数 / 兽装/人物照片数 / 低置信度
          - IdentityManager.get_groups() → 角色分组数
          - feedback.json → 人工反馈数
        """
        stats = {
            "analyzed": 0,
            "fursuit_photos": 0,
            "person_photos": 0,
            "group_count": 0,
            "fursuit_group_count": 0,
            "person_group_count": 0,
            "feedback": 0,
            "avg_quality": 0.0,
        }

        # ---- AnalysisCache（已分析照片统计）----
        # AnalysisCache 无公开遍历接口，._cache 为内存字典；
        # 此处只读访问，不修改缓存。独立 try/except 容错。
        try:
            from core.analysis_cache import get_cache

            cache = get_cache()
            cache_dict = getattr(cache, "_cache", {})

            analyzed = 0
            fursuit_photos = 0
            person_photos = 0
            quality_sum = 0.0
            quality_count = 0

            for v in cache_dict.values():
                if not isinstance(v, dict):
                    continue
                if v.get("category") is not None:
                    analyzed += 1
                quality = v.get("quality", 0)
                try:
                    quality = float(quality)
                except (TypeError, ValueError):
                    quality = 0
                quality_sum += quality
                quality_count += 1
                l1 = v.get("layer1") or {}
                l1_cn = ""
                if isinstance(l1, dict):
                    l1_cn = str(l1.get("label_cn", ""))
                if "兽装" in l1_cn:
                    fursuit_photos += 1
                elif "普通人物" in l1_cn:
                    person_photos += 1

            stats["analyzed"] = analyzed
            stats["fursuit_photos"] = fursuit_photos
            stats["person_photos"] = person_photos
            # quality 为 L1 top-1 概率（0-1），取平均值反映整体置信水平
            stats["avg_quality"] = (
                quality_sum / quality_count if quality_count else 0.0
            )
        except Exception as e:
            print(f"[总览] 缓存读取失败（不影响其余统计）: {e}")

        # ---- IdentityManager（角色分组统计，只读）----
        # IdentityManager() 构造懒加载模型；get_groups() 纯 SELECT，
        # 在已迁移的 v2 库上不产生任何写入。
        # 独立 try/except：即便身份库读取失败，上方 cache 统计仍保留。
        try:
            from core.identity import IdentityManager

            mgr = IdentityManager()
            try:
                groups = mgr.get_groups() or []
                stats["group_count"] = len(groups)
                stats["fursuit_group_count"] = sum(
                    1 for g in groups
                    if g.get("type") == "fursuit_character"
                )
                stats["person_group_count"] = sum(
                    1 for g in groups
                    if g.get("type") == "real_person"
                )
            finally:
                mgr.close()
        except Exception as e:
            print(f"[总览] 身份库读取失败（不影响其余统计）: {e}")

        # ---- 人工反馈数（feedback.json，只读计数）----
        feedback_path = self.advisor.feedback_file
        if feedback_path and os.path.exists(feedback_path):
            try:
                with open(feedback_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    stats["feedback"] = len(json.loads(content)) if content else 0
            except (json.JSONDecodeError, IOError):
                stats["feedback"] = 0

        return stats

    # ============================================================
    # 信号连接
    # ============================================================

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
