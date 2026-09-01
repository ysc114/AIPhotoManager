"""
favorites_mixin —— MainWindow 页面方法拆分（纯移动，方法体零修改）。

由 ui/main_window_v3.py 拆分而来，保持接口/行为完全一致。
"""

import os

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap, QIcon
from PySide6.QtWidgets import (
    QLabel, QWidget, QFrame, QPushButton, QGridLayout, QVBoxLayout,
    QMessageBox, QScrollArea, QListWidgetItem,
)



class _FavoritesMixinMixin:
    """收藏/预览等页面方法（运行时绑定 MainWindow 实例）。"""

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
        from core.identity import get_reader
        mgr = get_reader()   # 共享只读连接
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

