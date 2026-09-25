"""
favorites_mixin —— MainWindow 页面方法拆分（纯移动，方法体零修改）。

由 ui/main_window_v3.py 拆分而来，保持接口/行为完全一致。
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel, QWidget, QFrame, QPushButton, QGridLayout, QVBoxLayout,
    QMessageBox, QScrollArea,
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
        # 缓存优先 + 后台补图：此前逐张同步解码原图（50 张实测 2.2s 主线程卡顿）
        def _apply(cache_path, lab=label, px=138):
            if not cache_path:
                return
            try:
                from PySide6.QtGui import QPixmap
                pix = QPixmap(cache_path)
                if pix.isNull():
                    return
                lab.setPixmap(pix.scaled(
                    px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            except Exception:
                pass          # 页面已重建 → 旧 label 失效，忽略

        pix = self._thumb_cache_pixmap(path, 138, on_ready=_apply)
        if not pix.isNull():
            label.setPixmap(
                pix.scaled(138, 138, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        else:
            label.setText("…")
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
        raw = self._current_photo_path()
        if not raw:
            QMessageBox.information(self, "提示", "请先在照片页打开一张照片。")
            return
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
        self._sync_favorite_button(raw)      # 按钮文案跟随新状态
        self.statusBar().showMessage(f"{msg}：{os.path.basename(raw)}", 3000)


    def _current_photo_path(self):
        """当前预览照片的库内路径（正斜杠，与收藏表一致）。优先照片页预览记录，回退角色墙跳转的 detection 上下文。"""

        p = getattr(self, "_preview_path", None)
        if not p:
            ctx = getattr(self, "_photo_detection_context", None)
            p = ctx.get("path") if isinstance(ctx, dict) else None
        return str(p or "").replace("\\", "/")


    def _sync_favorite_button(self, path=None):
        """按当前照片的收藏状态刷新「⭐ 收藏当前」按钮（避免误以为只是收藏）。

        无照片时恢复默认文案；读库失败按未收藏处理（只影响文案）。
        """
        btn = getattr(self, "btn_fav_toggle", None)
        if btn is None:
            return
        raw = path if path is not None else self._current_photo_path()
        raw = str(raw or "").replace("\\", "/")
        if not raw:
            btn.setText("⭐ 收藏当前")
            btn.setToolTip("请先在照片页打开一张照片")
            return
        fav = False
        try:
            from core.identity import get_reader
            fav = bool(get_reader().db.is_favorite(raw))
        except Exception:
            pass
        if fav:
            btn.setText("★ 已收藏（点击取消）")
            btn.setToolTip("当前照片已在收藏中，点击取消收藏")
        else:
            btn.setText("⭐ 收藏当前")
            btn.setToolTip("把当前照片加入收藏")

    def _remove_favorite(self, path):
        from core.identity import IdentityManager
        mgr = IdentityManager()
        try:
            mgr.db.remove_favorite(path)
        finally:
            mgr.close()
        self._load_favorites_page()
        self._sync_favorite_button()      # 当前预览若正是这张 → 文案回落

    # ------------------------------------------------------------
    # 待处理页（添加新照片 → 自动识别 → 兽装 Fursee / 人物 Face）
    # ------------------------------------------------------------


    def _preview_favorite(self, path):
        """收藏页点击 → 照片页预览完整原图（复用现有预览链路）。"""
        resolved = self._resolve_display_path(path)
        # 快照原列表（未处于过滤态时）→ 照片页可「↩️ 返回全部」
        if getattr(self, "_photo_list_mode", "") not in (
                "similar", "group", "favorite"):
            self._photo_list_backup = list(self.image_list or [])
            self._photo_list_backup_row = self.image_list_widget.currentRow()
        self.image_list = [resolved]
        self._photo_list_mode = "favorite"
        self._photo_detection_context = {
            "row": 0, "path": resolved, "bbox": None,
            "detection_index": None, "group_name": "收藏",
        }
        # 统一填充（缩略图缓存优先）+ 按内容栈索引切到照片页
        self._populate_photo_list([resolved], select=0)
        self._switch_page(self.content_stack.indexOf(self.photo_page))
        self.show_preview(0)

