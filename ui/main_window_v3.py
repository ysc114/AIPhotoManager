import os
import sys
from pathlib import Path
from datetime import datetime

_project_root = Path(__file__).resolve().parents[1]

if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from config.settings_manager import settings as S
from core.identity.naming import display_name as role_display_name
from ui.settings_center import SettingsCenterPage


from PySide6.QtCore import Qt, QSize, QTimer, QPoint, QThread, Signal
from PySide6.QtGui import (
    QIcon,
    QPixmap,
    QColor,
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
)


from core.image_loader import load_images_from_folder
from core.ai_classifier import AIClassifier
from core.ai_advisor import AIAdvisor
from core.thumbnail_cache import thumbnail_cache
from ui.bottom_nav import BottomGlassNav
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
        "♻️  重复照片",
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
        self._tile_multi_map = {}    # 合照角标 → (page_key, image_path, 当前角色 id)

        # Spotlight 全局搜索：面板/快捷键/最近搜索（会话内）
        self._global_search = None
        self._gs_shortcuts = []
        self._gs_recents = []
        self._gs_panel_w = 640
        # 语义搜索（CLIP 文本）：后台建索引一次后常驻
        self._sem_building = False
        self._sem_worker = None

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
        # 启动后后台体检一次（只读、0.3s）：有问题就在状态栏提示，
        # 无需用户先点进设置；无问题则完全静默。
        QTimer.singleShot(2500, self._startup_health_check)

    def _startup_health_check(self):
        """启动后后台体检一次（幂等；测试无事件循环 → 不触发）。"""
        w = getattr(self, "_health_worker", None)
        if w is not None and w.isRunning():
            return
        worker = _HealthCheckWorker()
        worker.done.connect(self._on_health_check_done)
        worker.failed.connect(self._on_health_check_failed)
        self._health_worker = worker
        worker.start()

    def _on_health_check_done(self, result):
        self._reap_worker(getattr(self, "_health_worker", None))
        self._health_worker = None
        self._health_snapshot = result
        warn = int((result or {}).get("warnings") or 0)
        err = int((result or {}).get("errors") or 0)
        if not warn and not err:
            return                     # 一切正常：完全静默
        tip = f"🩺 体检发现 {warn} 项待处理"
        if err:
            tip += f" · {err} 项异常"
        self.statusBar().showMessage(f"{tip}（设置 → 🩺 数据体检 可查看/一键修复）", 12000)

    def _on_health_check_failed(self, err):
        self._reap_worker(getattr(self, "_health_worker", None))
        self._health_worker = None
        print(f"[体检] 启动体检失败（忽略）: {err}")

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
        # auto_scan=False：启动不扫全库，首次进入重复照片页再扫描
        self.duplicates_page = DuplicatesPage(auto_scan=False)
        self.content_stack.addWidget(self.duplicates_page)
        self.duplicates_page.data_changed.connect(self._on_duplicates_changed)

        # ── 全局搜索条（顶部 Liquid Glass 胶囊）+ Spotlight 搜索面板 + 内容区 ──
        self.search_bar = GlassSearchBar()
        self.search_bar.role_activated.connect(self._open_group_from_search)
        self.search_bar.photo_activated.connect(self._open_photo_by_path)
        main_area = QWidget()
        main_layout = QVBoxLayout(main_area)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(10)
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.addWidget(self.search_bar, 1)
        # Spotlight 面板入口：紧凑圆形图标钮（悬停提示；Ctrl+K 亦可）
        self.gs_btn = QPushButton("🔍")
        self.gs_btn.setFixedSize(40, 40)
        self.gs_btn.setCursor(Qt.PointingHandCursor)
        self.gs_btn.setToolTip("全局搜索（Ctrl+K）")
        self.gs_btn.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.7);color:#3a5a7a;"
            "border:1px solid rgba(255,255,255,0.9);border-radius:20px;"
            "font-size:16px;}"
            "QPushButton:hover{background:rgba(255,255,255,0.95);}"
        )
        self.gs_btn.clicked.connect(self._toggle_global_search)
        top_row.addWidget(self.gs_btn)
        main_layout.addLayout(top_row)
        main_layout.addWidget(self.content_stack, 1)

        # ── Spotlight 全局搜索面板（组件化；数据与跳转由 MainWindow 决定）──
        from ui.components.global_search import GlobalSearchPanel, PANEL_W
        self._gs_panel_w = PANEL_W
        self._global_search = GlobalSearchPanel(main_area)
        self._global_search.search_requested.connect(self._on_global_search_query)
        self._global_search.result_selected.connect(self._on_global_result_selected)
        self._global_search.hide()
        self._gs_recents = []   # 最近搜索（会话内）

        root.addWidget(main_area, 1)
        self._root_layout = root

        # ── 底部悬浮液态导航（新版模式；经典模式恢复左侧导航）──
        self.bottom_nav = BottomGlassNav(parent=central)
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
            # 内容区底部预留悬浮 Dock 空间（栏高 86 + 悬浮间隙 26 + 余量）
            self._root_layout.setContentsMargins(16, 16, 16, 86 + 26 + 12)
        self._layout_bottom_nav()

    def _layout_bottom_nav(self):
        """底部导航：自适应宽度 + 居中悬浮定位（窗口缩放不溢出）。"""
        if not getattr(self, "bottom_nav", None) or self.bottom_nav.isHidden():
            return
        central = self.centralWidget()
        cw = central.width() if central and central.width() > 0 else self.width()
        if cw <= 0:
            return
        margin = 40
        nav_w = min(cw - 2 * margin, int(self.bottom_nav.natural_width()))
        nav_w = max(320, int(nav_w))
        self.bottom_nav.setFixedWidth(nav_w)
        x = (cw - nav_w) // 2
        y = self.height() - self.bottom_nav.height() - 26
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
        if getattr(self, "_global_search", None):
            self._layout_global_search()

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

        # 🔎 查找相似照片（智能搜索第 2 层 · 视觉 Embedding + FAISS）
        self.btn_similar = QPushButton(
            "🔎 查找相似照片"
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

        button_layout.addWidget(
            self.btn_similar
        )

        # ↩️ 返回全部：相似搜索会替换照片列表，这里提供一键恢复
        self.btn_restore_list = QPushButton(
            "↩️ 返回全部"
        )
        self.btn_restore_list.setToolTip("退出相似搜索，恢复原来的照片列表")
        self.btn_restore_list.hide()
        button_layout.addWidget(
            self.btn_restore_list
        )

        # 🎭 同框角色（反向查询：这张照片里有哪些角色，可跳转）
        self.btn_roles = QPushButton(
            "🎭 同框角色"
        )
        self.btn_roles.setToolTip("查看这张照片里的角色，并跳转到对应角色页")
        button_layout.addWidget(
            self.btn_roles
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
        from core.identity import get_reader
        from core.duplicates import cached_md5
        mgr = get_reader()   # 共享只读连接：避免每次刷新都新建连接 + WAL checkpoint
        try:
            photos_dir = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "photos")
            )
            existing = {
                row[0] for row in mgr.db.conn.execute(
                    "SELECT DISTINCT image_path FROM identity_image"
                )
            }
            total = 0
            candidates = []
            if os.path.isdir(photos_dir):
                exts = {".jpg", ".jpeg", ".png", ".webp"}
                for n in sorted(os.listdir(photos_dir)):
                    if os.path.splitext(n)[1].lower() not in exts:
                        continue
                    total += 1
                    p = os.path.join(photos_dir, n).replace("\\", "/")
                    if p not in existing:
                        candidates.append(p)
            # 两级过滤（2026-09-25）：先按文件大小筛（只 stat 不读盘），
            # 只有大小相同的才可能内容重复，再对这几组算 MD5。
            # 此前无条件为全库照片算 MD5 → 待处理页冷启动 ~3s 纯 I/O。
            size_map = {}
            for p in existing:
                try:
                    size_map.setdefault(os.stat(p).st_size, []).append(p)
                except OSError:
                    continue
            new_cnt = dup_cnt = 0
            for p in candidates:
                try:
                    st = os.stat(p)
                except OSError:
                    new_cnt += 1
                    continue
                peers = size_map.get(st.st_size) or []
                if not peers:
                    new_cnt += 1        # 大小唯一 → 不可能与库内重复
                    continue
                try:
                    m = cached_md5(p)
                except OSError:
                    new_cnt += 1
                    continue
                dup = False
                for q in peers:
                    try:
                        if cached_md5(q) == m:
                            dup = True
                            break
                    except OSError:
                        continue
                if dup:
                    dup_cnt += 1
                else:
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
        self._reap_worker(getattr(self, "_pending_worker", None))
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
        # 入库完成 → 后台增量更新视觉/语义搜索索引（设置中心可关）
        if S.get("data.auto_update_visual_index", True):
            self._update_visual_index_async()

    def _on_analyze_failed(self, err):
        self._reap_worker(getattr(self, "_pending_worker", None))
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

        self.btn_restore_list.clicked.connect(
            self._restore_photo_list
        )

        self.btn_roles.clicked.connect(
            self._show_photo_roles
        )

        self.btn_similar.clicked.connect(
            self._find_similar_photos
        )

        self.btn_refresh_overview.clicked.connect(
            self._refresh_overview
        )

        # ── Spotlight 全局搜索快捷键（Ctrl+K 新增；Ctrl+Shift+F 既有约定复用）──
        from PySide6.QtGui import QKeySequence, QShortcut
        for ks in ("Ctrl+K", "Ctrl+Shift+F"):
            sc = QShortcut(QKeySequence(ks), self)
            sc.activated.connect(self._open_global_search)
            self._gs_shortcuts.append(sc)

    # ------------------------------------------------------------
    # Spotlight 全局搜索面板（组件化：组件只发信号，此处决定数据/跳转）
    # ------------------------------------------------------------
    def _layout_global_search(self):
        """面板定位：内容区顶部居中。"""
        panel = getattr(self, "_global_search", None)
        if panel is None:
            return
        parent = panel.parentWidget()
        pw = parent.width() if parent else self.width()
        x = max(0, (pw - getattr(self, "_gs_panel_w", 640)) // 2)
        panel.move(x, 64)

    def _open_global_search(self):
        self._layout_global_search()
        self._global_search.show_panel()

    def _toggle_global_search(self):
        if self._global_search.is_visible():
            self._global_search.hide_panel()
        else:
            self._open_global_search()

    def _on_global_search_query(self, query):
        """组件查询回调：用现有只读数据构造分区结果（不写库/不改业务）。"""
        self._global_search.set_query(query or "")   # 同步组件内部状态
        q = (query or "").strip().lower()
        if not q:
            self._global_search.set_results([], recent=self._gs_recents)
            return
        sections = []

        # 🐺/👤 角色（get_groups 只读，名称或 id 子串匹配）
        char_items = []
        try:
            from core.identity import get_reader
            mgr = get_reader()
            try:
                groups = mgr.get_groups("all") or []
            finally:
                mgr.close()
        except Exception:
            groups = []
        for g in groups:
            name = (g.get("name") or "").lower()
            cid = str(g.get("character_id") or "").lower()
            cat = (self._format_group_category(g) or "").lower()
            disp = (g.get("name") or
                    f"未命名角色 #{str(g.get('character_id') or '')[:8]}").lower()
            if q not in name and q not in cid and q not in cat and q not in disp:
                continue
            st = g.get("source_types") or []
            icon = ("🐺" if st == ["fursuit_fursee"] else
                    "👤" if st == ["face"] else "🎭")
            char_items.append({
                "icon": icon,
                "title": role_display_name(
                    g.get("name"), g.get("type"), g.get("serial")),
                "subtitle": self._format_group_category(g),
                "badge": "角色",
                "payload": {"kind": "character", "group": g},
            })
            if len(char_items) >= 6:
                break
        if char_items:
            sections.append({"title": "🐺 角色", "items": char_items})

        # 📷 照片（photos/ 文件名子串匹配；预留标签/文件分区）
        photos_dir = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "photos"))
        photo_items = []
        if os.path.isdir(photos_dir):
            for n in sorted(os.listdir(photos_dir)):
                if q in n.lower():
                    p = os.path.join(photos_dir, n).replace("\\", "/")
                    photo_items.append({
                        "icon": "📷", "title": n, "subtitle": "照片",
                        "badge": "照片",
                        "payload": {"kind": "photo", "path": p},
                    })
                    if len(photo_items) >= 6:
                        break
        if photo_items:
            sections.append({"title": "📷 照片", "items": photo_items})

        # ⭐ 收藏（favorite_image 只读，文件名匹配）
        fav_items = []
        try:
            from core.identity import get_reader
            mgr = get_reader()
            try:
                favs = mgr.db.list_favorites()
            finally:
                mgr.close()
        except Exception:
            favs = []
        for p in favs:
            if q in os.path.basename(p).lower():
                fav_items.append({
                    "icon": "⭐", "title": os.path.basename(p),
                    "subtitle": "收藏", "badge": "收藏",
                    "payload": {"kind": "photo", "path": p},
                })
                if len(fav_items) >= 4:
                    break
        if fav_items:
            sections.append({"title": "⭐ 收藏", "items": fav_items})

        # 🧠 语义搜索（CLIP 文本 embedding → FAISS；第 2 层第二阶段）
        semantic_items = []
        try:
            from core.visual_search import get_encoder, get_index
            s_idx = get_index()
            if s_idx.count() > 0:
                enc = get_encoder()
                if not enc.is_loaded():
                    # 首次语义搜索要加载 CLIP（约 10s）：放后台预热，
                    # 完成后由 _on_semantic_build_done 自动重渲染结果，
                    # 避免主线程卡住整个界面。
                    self._start_semantic_build()
                    semantic_items.append({
                        "icon": "⏳",
                        "title": "语义模型加载中…（首次约 10 秒）",
                        "subtitle": "完成后自动刷新结果", "badge": "语义",
                        "payload": {"kind": "hint"},
                    })
                else:
                    for r in s_idx.search_by_text(q, enc, top_k=4):
                        semantic_items.append({
                            "icon": "🧠",
                            "title": os.path.basename(r["path"]),
                            "subtitle": f"语义相似 {r['similarity'] * 100:.0f}%",
                            "badge": "语义",
                            "payload": {"kind": "photo", "path": r["path"]},
                        })
            else:
                # 索引为空：后台构建一次（幂等），完成后自动刷新结果
                self._start_semantic_build()
                semantic_items.append({
                    "icon": "⏳", "title": "语义索引构建中…（首次）",
                    "subtitle": "完成后自动刷新结果", "badge": "语义",
                    "payload": {"kind": "hint"},
                })
        except Exception as e:
            semantic_items.append({
                "icon": "🧠", "title": "语义搜索暂不可用",
                "subtitle": str(e)[:60], "badge": "语义",
                "payload": {"kind": "hint"},
            })
        if semantic_items:
            sections.append({"title": "🧠 语义（CLIP）", "items": semantic_items})

        self._global_search.set_results(sections)

    def _index_worker_running(self):
        """是否有视觉索引构建任务在跑（自动维护 / 语义搜索共用）。"""
        for w in (getattr(self, "_visual_index_worker", None),
                  getattr(self, "_sem_worker", None)):
            if w is not None and w.isRunning():
                return True
        return False

    @staticmethod
    def _reap_worker(worker):
        """等后台线程真正退出后再丢引用（避免 QThread 析构时仍在运行）。"""
        from core.qt_threads import reap_thread
        return reap_thread(worker)

    def _update_visual_index_async(self):
        """入库完成后台增量更新视觉索引。

        失败只提示不弹窗、不打断主流程；已有任务在跑则跳过（返回 False）。
        """
        if self._index_worker_running():
            return False
        w = _SemanticBuildWorker()
        w.progress_updated.connect(self._on_visual_index_progress)
        w.finished_build.connect(self._on_visual_index_done)
        w.failed.connect(self._on_visual_index_failed)
        self._visual_index_worker = w
        w.start()
        return True

    def _on_visual_index_progress(self, cur, total):
        if total:
            self.statusBar().showMessage(f"视觉索引更新 {cur}/{total}…", 1500)

    def _on_visual_index_done(self, stats):
        self._reap_worker(getattr(self, "_visual_index_worker", None))
        self._visual_index_worker = None
        self._notify_index_updated(stats or {})
        # 搜索面板开着 → 用当前查询重渲染（语义结果自动刷新）
        if self._global_search is not None and self._global_search.isVisible():
            self._on_global_search_query(self._global_search.query())

    def _on_visual_index_failed(self, err):
        self._reap_worker(getattr(self, "_visual_index_worker", None))
        self._visual_index_worker = None
        print(f"[视觉索引] 更新失败: {err}")
        self.statusBar().showMessage(f"视觉索引更新失败：{err}", 10000)
        self._refresh_settings_index_status()

    def _notify_index_updated(self, stats):
        """索引更新完成的统一反馈（状态栏 + 设置页状态行，不弹模态框）。"""
        new = int(stats.get("new") or 0)
        skipped = int(stats.get("skipped_existing") or 0)
        msg = f"视觉索引已更新：新增 {new} 张"
        if skipped:
            msg += f"，跳过已索引 {skipped} 张"
        try:
            from core.visual_search import read_index_status
            st = read_index_status()
            msg += "（已索引 %d/%d）" % (st["indexed"], st["photos_total"])
        except Exception:
            pass
        self.statusBar().showMessage(msg, 8000)
        self._refresh_settings_index_status()

    def _refresh_settings_index_status(self):
        page = getattr(self, "settings_center", None)
        if page is not None and hasattr(page, "refresh_index_status"):
            try:
                page.refresh_index_status()
            except Exception as e:
                print(f"[视觉索引] 设置页状态刷新失败: {e}")

    def _start_semantic_build(self):
        """后台构建/增量更新视觉索引（首次全量，之后增量秒级）。"""
        if getattr(self, "_sem_building", False):
            return
        if self._index_worker_running():
            return  # 自动维护已在跑：完成后会统一刷新搜索结果
        self._sem_building = True
        w = _SemanticBuildWorker()
        w.progress_updated.connect(self._on_visual_index_progress)
        w.finished_build.connect(self._on_semantic_build_done)
        w.failed.connect(self._on_semantic_build_failed)
        self._sem_worker = w
        w.start()

    def _on_semantic_build_done(self, stats=None):
        self._reap_worker(getattr(self, "_sem_worker", None))
        self._sem_building = False
        self._sem_worker = None
        self._notify_index_updated(stats or {})
        # 索引就绪：用当前查询重新渲染（自动出现语义结果）
        if self._global_search is not None and self._global_search.isVisible():
            self._on_global_search_query(self._global_search.query())

    def _on_semantic_build_failed(self, err):
        self._reap_worker(getattr(self, "_sem_worker", None))
        self._sem_building = False
        self._sem_worker = None
        print(f"[语义搜索] 索引构建失败: {err}")
        self._refresh_settings_index_status()

    def _on_global_result_selected(self, item):
        """结果打开：角色 → 角色详情；照片/收藏/文件 → 照片页预览。"""
        payload = item.get("payload") or {}
        title = str(item.get("title") or "").strip()
        kind = payload.get("kind")
        if kind == "hint":
            return  # 提示行不可打开
        if title:
            self._gs_recents = [title] + [r for r in self._gs_recents if r != title]
            self._gs_recents = self._gs_recents[:8]
        if kind == "character" and payload.get("group"):
            self._open_group_from_search(payload["group"])
        elif payload.get("path"):
            self._open_photo_by_path(payload["path"])

    def _populate_photo_list(self, paths, select=0, labels=None):
        """统一填充照片列表：磁盘缩略图缓存优先，未命中走后台补图。

        此前每行同步 `_load_pixmap_cached`（解码原图）——194 张实测 5.5s 主线程
        卡顿；改走 thumbnail_cache（256px）后首次填充仅需 stat/查缓存，图标由
        后台线程生成后回填（与角色页/重复页同一套缓存）。

        labels: 可选，与 paths 等长的显示文本（如相似搜索带相似度）。
        """
        self.image_list_widget.clear()
        for i, path in enumerate(paths or []):
            text = (labels[i] if labels and i < len(labels)
                    else os.path.basename(path))
            item = QListWidgetItem(text)
            self.image_list_widget.addItem(item)
            self._set_photo_list_icon(item, path)
        if paths:
            row = max(0, min(int(select or 0), len(paths) - 1))
            self.image_list_widget.setCurrentRow(row)
        self.btn_restore_list.setVisible(
            getattr(self, "_photo_list_mode", "") == "similar")

    def _set_photo_list_icon(self, item, path, size=110):
        """列表行图标：缓存命中直接显示，未命中后台生成后回填（主线程回调）。"""
        from core.thumbnail_cache import thumbnail_cache
        local = self._resolve_display_path(path)
        if not local:
            return
        size = max(24, int(size))
        try:
            cp = thumbnail_cache.get_cached(local, size)
        except Exception:
            cp = None
        if cp:
            try:
                pix = QPixmap(cp)
                if not pix.isNull():
                    item.setIcon(QIcon(pix.scaled(
                        size, size, Qt.KeepAspectRatio,
                        Qt.SmoothTransformation)))
                    return
            except Exception:
                pass

        def _apply(cache_path, it=item, px=size):
            if not cache_path:
                return
            try:
                pix = QPixmap(cache_path)
                if pix.isNull():
                    return
                it.setIcon(QIcon(pix.scaled(
                    px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            except Exception:
                pass          # 列表已重建 → 旧 item 失效，忽略

        try:
            thumbnail_cache.request(local, size, None, _apply)
        except Exception:
            pass

    def _restore_photo_list(self):
        """退出相似搜索：恢复搜索前的照片列表与选中项。"""
        backup = getattr(self, "_photo_list_backup", None)
        if not backup:
            self.btn_restore_list.hide()
            self.statusBar().showMessage("没有可恢复的照片列表", 4000)
            return
        row = getattr(self, "_photo_list_backup_row", 0) or 0
        self.image_list = list(backup)
        self._photo_list_backup = None
        self._photo_list_mode = ""
        self._populate_photo_list(self.image_list, select=row)
        self.show_preview(self.image_list_widget.currentRow())
        self.statusBar().showMessage(
            f"已返回全部照片（{len(self.image_list)} 张）", 5000)

    def _photo_roles_refs(self, path):
        """照片页反向查询：这张照片里的角色（先原样查，再标准化兜底）。"""
        refs = self._image_group_refs(path)
        if not refs:
            alt = os.path.abspath(path).replace("\\", "/")
            if alt != path:
                refs = self._image_group_refs(alt)
        return refs

    def _show_photo_roles(self):
        """照片页：列出当前预览照片里的角色，选中即跳转该角色详情页。"""
        path = self.current_image_path
        if not path and self.image_list:
            row = self.image_list_widget.currentRow()
            path = (self.image_list[row]
                    if 0 <= row < len(self.image_list) else self.image_list[0])
        if not path:
            self.statusBar().showMessage("请先在照片页选择一张照片", 3000)
            return
        refs = self._photo_roles_refs(path)
        if not refs:
            self.statusBar().showMessage("这张照片还没有角色归属", 4000)
            return
        menu = self._build_multi_role_menu(
            refs, header=f"📸 这张照片里有 {len(refs)} 个角色")
        chosen = menu.exec(self.btn_roles.mapToGlobal(
            QPoint(0, self.btn_roles.height() + 2)))
        if chosen is None:
            return
        cid = chosen.data()
        if cid:
            self._open_group_by_id(cid)

    # ------------------------------------------------------------
    # 🔎 查找相似照片（视觉 Embedding + FAISS，后台线程；智能搜索第 2 层）
    # ------------------------------------------------------------
    def _find_similar_photos(self):
        worker = getattr(self, "_similar_worker", None)
        if worker is not None and worker.isRunning():
            self.statusBar().showMessage("相似照片搜索已在进行中…", 3000)
            return
        if self._index_worker_running():
            self.statusBar().showMessage(
                "视觉索引正在更新，请稍候几秒再试…", 5000)
            return
        path = self.current_image_path
        if not path and self.image_list:
            row = self.image_list_widget.currentRow()
            path = self.image_list[row] if 0 <= row < len(self.image_list) \
                else self.image_list[0]
        if not path or not os.path.exists(path):
            QMessageBox.information(self, "查找相似照片",
                                    "请先在照片页打开一张照片。")
            return
        # 快照当前列表（已处于相似搜索模式则不覆盖快照，便于连续搜索后仍能返回）
        if getattr(self, "_photo_list_mode", "") != "similar":
            self._photo_list_backup = list(self.image_list or [])
            self._photo_list_backup_row = self.image_list_widget.currentRow()
        self.statusBar().showMessage(
            "正在建立/更新视觉索引并搜索…（首次全量较慢，之后增量秒级）")
        w = _SimilarSearchWorker(path)
        w.progress_updated.connect(self._on_similar_progress)
        w.done_sig.connect(self._on_similar_done)
        w.failed.connect(self._on_similar_failed)
        self._similar_worker = w
        w.start()

    def _on_similar_progress(self, cur, total):
        self.statusBar().showMessage(f"视觉索引 {cur}/{total}…", 1000)

    def _on_similar_done(self, results):
        self._reap_worker(getattr(self, "_similar_worker", None))
        self._similar_worker = None
        if not results:
            QMessageBox.information(self, "查找相似照片",
                                    "没有找到相似照片（索引可能为空）。")
            return
        paths = [r["path"] for r in results]
        labels = [os.path.basename(r["path"])
                  + f"  · 相似 {r['similarity'] * 100:.0f}%"
                  for r in results]
        self.image_list = paths
        self._photo_list_mode = "similar"
        self._populate_photo_list(paths, select=0, labels=labels)
        self.show_preview(self.image_list_widget.currentRow())
        self.statusBar().showMessage(
            f"找到 {len(paths)} 张相似照片（视觉搜索）", 8000)

    def _on_similar_failed(self, err):
        self._reap_worker(getattr(self, "_similar_worker", None))
        self._similar_worker = None
        QMessageBox.critical(self, "查找相似照片失败", str(err))

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
        self._photo_list_mode = ""
        self._photo_list_backup = None
        self._populate_photo_list(images)

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

        self._populate_photo_list(result)

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
            self.progress_updated.emit(
                0, len(self._paths),
                "准备中：加载 AI 模型（CLIP / Fursee 首次约 1 分钟）…")
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
            self.progress_updated.emit(
                0, 0,
                "准备中：加载 AI 模型（CLIP / Fursee 首次约 1 分钟）…")
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


class _SimilarSearchWorker(QThread):
    """后台执行「以图搜图」：增量建索引（首次全量）→ search_by_image。

    只读照片 + 写 cache/visual_search 独立索引，不触碰 identity_db。
    """

    progress_updated = Signal(int, int)   # (done, total)
    done_sig = Signal(object)             # [ {photo_id, path, similarity}, ... ]
    failed = Signal(str)

    _EXTS = {".jpg", ".jpeg", ".png", ".webp"}

    def __init__(self, query_path, parent=None):
        super().__init__(parent)
        self._path = query_path

    def run(self):
        import os
        try:
            from core.visual_search import get_encoder, get_index
            from core.visual_search.search import default_photos_dir
            photos_dir = default_photos_dir()
            files = []
            if os.path.isdir(photos_dir):
                files = sorted(
                    os.path.join(photos_dir, n) for n in os.listdir(photos_dir)
                    if os.path.splitext(n)[1].lower() in self._EXTS
                )
            encoder = get_encoder()
            index = get_index()
            index.add_images(
                files, encoder,
                progress_cb=lambda c, t: self.progress_updated.emit(c, t))
            results = index.search_by_image(self._path, encoder, top_k=20)
            self.done_sig.emit(results)
        except Exception as e:
            self.failed.emit(str(e))


class _HealthCheckWorker(QThread):
    """后台数据体检（只读、不加载 AI 模型；约 0.3s）。"""

    done = Signal(dict)
    failed = Signal(str)

    def run(self):
        try:
            from core.health_check import run_health_check
            self.done.emit(run_health_check())
        except Exception as e:
            self.failed.emit(str(e))


class _SemanticBuildWorker(QThread):
    """后台构建/增量更新视觉索引（语义搜索前置；不触碰 identity_db）。"""

    progress_updated = Signal(int, int)   # (done, total)
    finished_build = Signal(dict)         # 增量统计 {new, skipped_existing, ...}
    failed = Signal(str)

    _EXTS = {".jpg", ".jpeg", ".png", ".webp"}

    def run(self):
        import os
        try:
            from core.visual_search import get_encoder, get_index
            from core.visual_search.search import default_photos_dir
            photos_dir = default_photos_dir()
            files = []
            if os.path.isdir(photos_dir):
                files = sorted(
                    os.path.join(photos_dir, n) for n in os.listdir(photos_dir)
                    if os.path.splitext(n)[1].lower() in self._EXTS
                )
            index = get_index()
            stats = index.add_images(
                files, get_encoder(),
                progress_cb=lambda d, t: self.progress_updated.emit(d, t))
            self.finished_build.emit(dict(stats or {}))
        except Exception as e:
            self.failed.emit(str(e))
