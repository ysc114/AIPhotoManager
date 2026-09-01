"""
role_center_mixin —— MainWindow 页面方法拆分（纯移动，方法体零修改）。

由 ui/main_window_v3.py 拆分而来，保持接口/行为完全一致。
"""

import json
import os
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QSize, QPoint, QEvent, QRect, QThread
from PySide6.QtGui import (
    QPixmap, QColor, QFont, QPainter, QImage, QPen,
    QImageReader, QImageIOHandler,
)
from PySide6.QtWidgets import (
    QLabel, QWidget, QFrame, QPushButton, QGridLayout, QVBoxLayout,
    QHBoxLayout, QMessageBox, QScrollArea, QStackedWidget, QLineEdit,
    QComboBox, QFileDialog, QListWidget, QSplitter, QInputDialog,
)

from config.settings_manager import settings as S
from core.thumbnail_cache import thumbnail_cache
from core.photo_quality.scorer import get_analyzer as get_pq_analyzer
from ui.aurora_card import AuroraGlassCard
from ui.components.glass_button import GlassButton
from ui.components.toast import toast

# 与 main_window_v3.py 一致的模块级根路径
_project_root = Path(__file__).resolve().parents[1]


class _RoleCenterMixinMixin:
    """角色中心 / 分组页 / AI 精选方法（运行时绑定 MainWindow 实例）。"""

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

        # ── 角色中心 2.0：搜索 / 类型筛选 / 排序（仅「全部角色」页）──
        if page_key == "character":
            toolbar = self._build_character_toolbar(stats_label)
            list_layout.addWidget(toolbar)

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
        rename_btn = GlassButton("✏️ 重命名", variant="accent")
        top_bar.addWidget(rename_btn)
        merge_btn = GlassButton("🔗 合并角色", variant="normal")
        top_bar.addWidget(merge_btn)
        wall_layout.addLayout(top_bar)

        # ===== ⭐ AI 精选（角色内照片精选，纯视觉层，不触碰数据库）=====
        ai_pick_widget = QFrame()
        _ga = float(S.get("ui.glass_opacity", 0.55))
        _cr = int(S.get("ui.corner_radius", 18))
        ai_pick_widget.setStyleSheet("""
            QFrame {
                background: rgba(255,255,255,%f);
                border: 1px solid rgba(255,255,255,0.75);
                border-radius: %dpx;
            }
        """ % (_ga, _cr))
        ai_pick_layout = QVBoxLayout(ai_pick_widget)
        ai_pick_layout.setContentsMargins(14, 10, 14, 12)
        ai_pick_layout.setSpacing(8)

        ai_head = QHBoxLayout()
        ai_head.setSpacing(10)
        ai_title = QLabel("⭐ AI精选")
        ai_title.setStyleSheet(
            "font-size:15px;font-weight:800;color:#1f2d3d;background:transparent;border:none;"
        )
        ai_head.addWidget(ai_title)
        ai_status = QLabel("")
        ai_status.setStyleSheet(
            "font-size:12px;color:#8a97a8;background:transparent;border:none;"
        )
        ai_head.addWidget(ai_status)
        ai_head.addStretch(1)
        ai_count = QLabel("")
        ai_count.setStyleSheet(
            "font-size:12px;color:#5b7bd5;font-weight:700;"
            "background:rgba(255,255,255,0.5);border-radius:9px;padding:2px 10px;"
            "border:1px solid rgba(255,255,255,0.6);"
        )
        ai_head.addWidget(ai_count)
        ai_reanalyze = QPushButton("↻ 重新分析")
        ai_reanalyze.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #ffd166,stop:1 #f4a261);color:#5a4320;border:none;"
            "padding:5px 14px;border-radius:13px;font-size:12px;font-weight:700;}"
            "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #f5c255,stop:1 #e8964f);}"
            "QPushButton:disabled{background:rgba(255,255,255,0.5);color:#a0aab8;}"
        )
        ai_head.addWidget(ai_reanalyze)
        ai_pick_layout.addLayout(ai_head)

        ai_body = QScrollArea()
        ai_body.setWidgetResizable(True)
        ai_body.setFixedHeight(0)          # 无精选时收起
        ai_body.setStyleSheet(
            "QScrollArea{border:none;background:transparent;}"
            "QScrollArea > QWidget > QWidget{background:transparent;}"
        )
        ai_body_container = QWidget()
        ai_body_layout = QHBoxLayout(ai_body_container)
        ai_body_layout.setSpacing(10)
        ai_body_layout.setContentsMargins(0, 0, 0, 0)
        ai_body_layout.setAlignment(Qt.AlignLeft)
        ai_body.setWidget(ai_body_container)
        ai_pick_layout.addWidget(ai_body)
        wall_layout.addWidget(ai_pick_widget)

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
            "ai_pick_widget": ai_pick_widget,
            "ai_status": ai_status,
            "ai_count": ai_count,
            "ai_reanalyze": ai_reanalyze,
            "ai_body": ai_body,
            "ai_body_layout": ai_body_layout,
            "current_group": None,
            "current_display_name": "",
            "groups": [],
            "pq_worker": None,
        }

        refresh_btn.clicked.connect(lambda _, k=page_key: self._load_groups_into_page(k))
        analyze_btn.clicked.connect(lambda _, k=page_key: self._analyze_new_photos(k))
        back_btn.clicked.connect(lambda _, k=page_key: self._back_to_group_list(k))
        rename_btn.clicked.connect(lambda _, k=page_key: self._rename_current_group(k))
        merge_btn.clicked.connect(lambda _, k=page_key: self._merge_current_group(k))
        ai_reanalyze.clicked.connect(
            lambda _, k=page_key: self._start_ai_pick_analysis(k, force=True)
        )

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
        """清空 QGridLayout 内全部 widget（deleteLater 后显式派发删除事件）。

        PySide6 下 app.processEvents() 不派发 DeferredDelete（offscreen 测试
        环境尤为明显）——不显式派发会导致卡片 C++ 对象滞留内存。真实
        app.exec() 事件循环中 sendPostedEvents 为幂等无副作用操作。
        """
        while grid_layout.count():
            item = grid_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        try:
            from PySide6.QtCore import QEvent
            from PySide6.QtWidgets import QApplication
            QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        except Exception:
            pass


    def _analyze_new_photos(self, page_key):
        """一键增量分析：photos/ 未入库照片 → Fursee 入库 → 增量归组 → 刷新。

        后台线程执行（不阻塞 GUI）：仅处理未入库照片，聚类只走
        incremental_assign（不重跑 DBSCAN、不拆散已有组）。完成后自动刷新。
        """
        from ui.main_window_v3 import _ScanDirWorker
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

    # --------------------------------------------------------
    # 角色中心 2.0：搜索 / 筛选 / 排序（纯 UI，只读 get_groups 数据）
    # --------------------------------------------------------

    def _build_character_toolbar(self, stats_label):
        """角色中心顶部工具栏：搜索框 + 类型筛选 + 排序（Liquid Glass 样式）。"""
        from PySide6.QtWidgets import QLineEdit, QComboBox

        bar = QWidget()
        bar.setStyleSheet("background:transparent;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # 搜索框（Liquid Glass 胶囊）
        edit = QLineEdit()
        edit.setPlaceholderText("🔍 搜索角色…")
        edit.setFixedWidth(230)
        edit.setStyleSheet(
            "QLineEdit{background:rgba(255,255,255,0.78);border:1px solid rgba(255,255,255,0.95);"
            "border-radius:16px;padding:7px 14px;font-size:12.5px;color:#2a3a4e;}"
            "QLineEdit:focus{border:1px solid rgba(110,160,255,0.8);}"
            "QLineEdit::placeholder{color:#9aa6b8;}"
        )
        edit.setAttribute(Qt.WA_MacShowFocusRect, False)
        edit.textChanged.connect(self._on_character_filter_changed)
        lay.addWidget(edit)

        # 类型筛选
        type_combo = QComboBox()
        for label, data in (("全部", "all"),
                            ("兽装角色", "fursuit_character"),
                            ("人物角色", "real_person")):
            type_combo.addItem(label, data)
        type_combo.setStyleSheet(self._filter_combo_qss())
        type_combo.currentIndexChanged.connect(self._on_character_filter_changed)
        lay.addWidget(type_combo)

        # 排序
        sort_combo = QComboBox()
        for label, data in (("照片数量最多", "count_desc"),
                            ("照片数量最少", "count_asc"),
                            ("名称 A-Z", "name_asc"),
                            ("名称 Z-A", "name_desc"),
                            ("最近更新", "updated")):
            sort_combo.addItem(label, data)
        sort_combo.setStyleSheet(self._filter_combo_qss())
        sort_combo.currentIndexChanged.connect(self._on_character_filter_changed)
        lay.addWidget(sort_combo)

        # 计数
        counter = QLabel("")
        counter.setStyleSheet("font-size:11.5px;color:#8a97a8;background:transparent;border:none;")
        lay.addWidget(counter)
        lay.addStretch(1)

        # state 在页面构建末尾才建立 → 控件暂存 self，加载时挂载到 state
        self._char_toolbar_controls = (edit, type_combo, sort_combo, counter)
        return bar


    @staticmethod
    def _filter_combo_qss():
        return (
            "QComboBox{background:rgba(255,255,255,0.78);border:1px solid rgba(255,255,255,0.95);"
            "border-radius:14px;padding:6px 12px;font-size:12px;color:#3a5a7a;}"
            "QComboBox::drop-down{border:none;width:18px;}"
            "QComboBox QAbstractItemView{background:#f8faff;border-radius:10px;"
            "selection-background-color:rgba(120,160,255,0.25);color:#2a3a4e;}"
        )


    def _on_character_filter_changed(self, *args):
        """工具栏变化 → 实时过滤（搜索 150ms 防抖）。"""
        state = self._group_pages.get("character")
        if state is None:
            return
        if not hasattr(self, "_char_filter_timer"):
            from PySide6.QtCore import QTimer
            self._char_filter_timer = QTimer(self)
            self._char_filter_timer.setSingleShot(True)
            self._char_filter_timer.setInterval(150)
            self._char_filter_timer.timeout.connect(
                lambda: self._apply_character_filters(force=True))
        self._char_filter_timer.start()


    def _character_updated_map(self):
        """角色 id → updated_at（UI 层只读补充查询；失败返回空 dict）。"""
        try:
            import sqlite3
            db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "identity_db.sqlite")
            if not os.path.exists(db):
                return {}
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                return {r[0]: (r[1] or "") for r in con.execute(
                    "SELECT id, updated_at FROM identity_group")}
            finally:
                con.close()
        except Exception:
            return {}


    def _apply_character_filters(self, force=False):
        """按工具栏状态过滤 + 排序 + 重渲染角色卡片（复用 _render_group_card）。"""
        state = self._group_pages.get("character")
        if state is None:
            return
        edit = state.get("filter_edit")
        if edit is None:
            return
        query = edit.text().strip().lower()
        gtype = state["filter_type"].currentData() or "all"
        sort_key = state["filter_sort"].currentData() or "count_desc"

        groups = list(state.get("groups") or [])

        # 类型筛选
        if gtype != "all":
            groups = [g for g in groups if str(g.get("type") or "") == gtype]

        # 搜索（名称 / character_id 子串）
        if query:
            groups = [
                g for g in groups
                if query in str(g.get("name") or "").lower()
                or query in str(g.get("character_id") or "").lower()
            ]

        # 排序
        if sort_key == "count_desc":
            groups.sort(key=lambda g: self._unique_photo_count(g), reverse=True)
        elif sort_key == "count_asc":
            groups.sort(key=lambda g: self._unique_photo_count(g))
        elif sort_key == "name_asc":
            groups.sort(key=lambda g: (str(g.get("name") or "").lower(),
                                       str(g.get("character_id") or "")))
        elif sort_key == "name_desc":
            groups.sort(key=lambda g: (str(g.get("name") or "").lower(),
                                       str(g.get("character_id") or "")), reverse=True)
        elif sort_key == "updated":
            upd = getattr(self, "_char_updated_map_cache", None)
            if upd is None:
                upd = self._character_updated_map()
                self._char_updated_map_cache = upd
            groups.sort(
                key=lambda g: upd.get(str(g.get("character_id") or ""), ""),
                reverse=True)

        # 重渲染网格（清空 + 重建）
        self._clear_grid(state["grid_layout"])
        for card in list(self._card_group_map.keys()):
            if self._card_group_map[card][0] == "character":
                self._card_group_map.pop(card, None)

        state["empty_label"].hide()
        if not groups:
            state["empty_label"].show()
            state["empty_label"].setText("没有符合条件的角色")
            state["filter_counter"].setText("0 / %d 个角色" % len(state.get("groups") or []))
            state["stats_label"].setText("共 0 个角色")
            return

        cols = max(3, self.width() // (226 + 14))
        for i, group in enumerate(groups):
            display_name = (group.get("name") or
                            f"{state['default_prefix']} {str(group.get('character_id') or '')[:10]}")
            card = self._render_group_card(group, display_name, "character")
            r, c = divmod(i, cols)
            state["grid_layout"].addWidget(card, r, c)

        state["filter_counter"].setText(
            "%d / %d 个角色" % (len(groups), len(state.get("groups") or [])))
        total_photos = sum(self._unique_photo_count(g) for g in groups)
        state["stats_label"].setText(
            f"共 {len(groups)} 个角色 · {total_photos} 张唯一照片")


    def _load_groups_into_page(self, page_key):
        """从 IdentityManager.get_groups() 读取并渲染组列表（只读）。"""
        state = self._group_pages.get(page_key)
        if state is None:
            return
        # 角色中心：挂载工具栏控件到 state（幂等）
        if page_key == "character" and "filter_edit" not in state:
            c = getattr(self, "_char_toolbar_controls", None)
            if c:
                state["filter_edit"], state["filter_type"], \
                    state["filter_sort"], state["filter_counter"] = c
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
            from core.identity import get_reader
            mgr = get_reader()   # 共享只读连接（close 为 no-op，模式不变）
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

        # 角色中心：加载后重应用搜索/筛选/排序（保持当前工具栏状态）
        if page_key == "character" and state.get("filter_edit") is not None:
            self._apply_character_filters(force=True)
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
        # 性能优化（2026-08-31）：封面**占位优先**——不在此处同步解码原图
        # （大图单卡可达 100ms+，226 卡首屏累计 7s+）。改为：
        #   1) 磁盘缩略图缓存命中 → 直接显示（最快路径）
        #   2) 未命中 → 灰底占位 + 后台生成，完成后回调刷新（Immich 式骨架屏）
        # 占位立即渲染，图片陆续填充；任何失败保持占位，不影响卡片。
        cover_local = self._resolve_display_path(cover_path)
        bbox_json = cover_det.get("bbox") if cover_det else None
        shown = False
        if cover_local and self._thumb_cache.enabled:
            try:
                cp = self._thumb_cache.get_cached(cover_local, 256, bbox_json)
                if cp:
                    cpix = QPixmap(cp)
                    if not cpix.isNull():
                        cover_label.setPixmap(
                            cpix.scaled(200, 148, Qt.KeepAspectRatioByExpanding,
                                        Qt.SmoothTransformation)
                        )
                        shown = True
                if not shown:
                    # 后台生成，完成后回调刷新
                    self._thumb_cache.request(
                        cover_local, 256, bbox_json,
                        on_ready=lambda cpath, lab=cover_label: self._on_cover_thumb_ready(lab, cpath),
                    )
            except Exception as e:
                print(f"[封面缓存] {cover_path}: {e}")
        if not shown:
            # 占位（同步解码路径已移除；缩略图不可用时显示占位文字）
            cover_label.setText("…")
            cover_label.setStyleSheet(
                "background:rgba(240,244,250,0.55);border-radius:12px;"
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


    def _on_cover_thumb_ready(self, label, cache_path):
        """缩略图后台生成完成 → 主线程更新角色卡片封面。

        失败（cache_path=None）或卡片已销毁时静默忽略（保持原图/占位显示）。
        """
        if not cache_path or label.parent() is None:
            return
        try:
            pix = QPixmap(cache_path)
            if pix.isNull():
                return
            label.setPixmap(
                pix.scaled(200, 148, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            )
        except Exception as e:
            print(f"[封面缓存] 更新失败: {e}")

    # --------------------------------------------------------
    # ⭐ AI 精选（角色内照片精选）
    # --------------------------------------------------------

    def _ai_role_key(self, page_key):
        state = self._group_pages.get(page_key)
        if state is None or not state.get("current_group"):
            return None
        g = state["current_group"]
        return str(g.get("id") or g.get("character_id") or state.get("current_display_name", ""))


    def _ai_photos(self, page_key):
        """当前角色组唯一照片列表（本地路径），供质量分析。"""
        state = self._group_pages.get(page_key)
        if state is None:
            return []
        out = []
        for path, _det_idx in state.get("current_members", []):
            local = self._resolve_display_path(path)
            if local:
                out.append(local)
        return out


    def _refresh_ai_picks(self, page_key):
        """进入角色详情页：有缓存直接渲染；无缓存自动启动单角色分析。"""
        state = self._group_pages.get(page_key)
        if state is None:
            return
        role_key = self._ai_role_key(page_key)
        if not role_key:
            return
        try:
            analyzer = get_pq_analyzer()
            cached = analyzer.get_role_result(role_key)
        except Exception as e:
            print(f"[AI精选] 缓存读取失败: {e}")
            cached = None
        if cached and cached.get("picks") is not None:
            self._render_ai_picks(page_key, cached)
            state["ai_status"].setText("已分析" if cached.get("total") else "无推荐")
            if cached.get("total"):
                state["ai_count"].setText(f"{cached['total']} 张精选")
        else:
            state["ai_status"].setText("尚未分析 · 自动开始…")
            state["ai_count"].setText("")
            self._clear_ai_picks(page_key)
            self._start_ai_pick_analysis(page_key, force=False)


    def _start_ai_pick_analysis(self, page_key, force=False):
        from ui.main_window_v3 import PhotoQualityWorker
        state = self._group_pages.get(page_key)
        if state is None or state.get("pq_worker") and state["pq_worker"].isRunning():
            return  # 防重入
        role_key = self._ai_role_key(page_key)
        photos = self._ai_photos(page_key)
        if not role_key or not photos:
            state["ai_status"].setText("无照片可分析")
            return
        try:
            analyzer = get_pq_analyzer()
        except Exception as e:
            state["ai_status"].setText(f"分析器不可用: {e}")
            return
        state["ai_status"].setText(f"分析中… 共 {len(photos)} 张")
        state["ai_reanalyze"].setEnabled(False)
        self._clear_ai_picks(page_key)
        worker = PhotoQualityWorker(analyzer, role_key, photos, force=force)
        worker.finished.connect(lambda k, r, pk=page_key: self._on_ai_pick_done(pk, r))
        state["pq_worker"] = worker
        worker.start()


    def _on_ai_pick_done(self, page_key, result):
        state = self._group_pages.get(page_key)
        if state is None:
            return
        state["ai_reanalyze"].setEnabled(True)
        state["pq_worker"] = None
        if result is None:
            state["ai_status"].setText("分析失败，请重试")
            return
        n = result.get("total", 0)
        analyzed = result.get("analyzed", 0)
        cached = result.get("cached", 0)
        state["ai_status"].setText(
            f"已分析 {analyzed} 张" + (f" · 缓存 {cached} 张" if cached else "")
        )
        state["ai_count"].setText(f"{n} 张精选" if n else "暂无精选")
        self._render_ai_picks(page_key, result)
        toast.show(self, f"AI 精选完成 · {n} 张推荐", kind="success")


    def _clear_ai_picks(self, page_key):
        state = self._group_pages.get(page_key)
        if state is None:
            return
        lay = state["ai_body_layout"]
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        state["ai_body"].setFixedHeight(0)


    def _render_ai_picks(self, page_key, result):
        """渲染精选照片卡（横向滚动），并标注相似组。"""
        state = self._group_pages.get(page_key)
        if state is None:
            return
        self._clear_ai_picks(page_key)
        picks = result.get("picks") or []
        if not picks:
            state["ai_body"].setFixedHeight(0)
            return

        det_map = state.get("current_det_map") or {}
        det_by_local = {}
        for (p, det_idx), det in det_map.items():
            local = self._resolve_display_path(p)
            if local:
                det_by_local[local] = det

        for pk in picks[:20]:  # 最多展示 20 张
            card = self._make_pick_card(
                pk, det_by_local.get(pk["path"]),
                card_w=130, card_h=190, img_w=116, img_h=96,
            )
            state["ai_body_layout"].addWidget(card)

        state["ai_body_layout"].addStretch(1)
        state["ai_body"].setFixedHeight(206)


    def _make_pick_card(self, pk, det_info, card_w=150, card_h=210,
                        img_w=134, img_h=110):
        """构建一张 AI 精选照片卡（玻璃卡：缩略图 + ★分 + 理由 + 相似组徽标）。

        角色详情页（横向）与 AI 精选一级页（网格）共用。
        """
        path = pk["path"]
        card = QFrame()
        card.setFixedSize(card_w, card_h)
        _ga = float(S.get("ui.glass_opacity", 0.55))
        _cr = int(S.get("ui.corner_radius", 18))
        card.setStyleSheet("""
            QFrame {
                background: rgba(255,255,255,%f);
                border: 1px solid rgba(255,255,255,0.8);
                border-radius: %dpx;
            }
        """ % (_ga, _cr))
        vl = QVBoxLayout(card)
        vl.setContentsMargins(6, 6, 6, 6)
        vl.setSpacing(3)

        img_lab = QLabel()
        img_lab.setFixedSize(img_w, img_h)
        img_lab.setAlignment(Qt.AlignCenter)
        img_lab.setStyleSheet("background:rgba(240,244,250,0.4);border-radius:10px;border:none;")
        pix = self._pixmap_for_detection(path, det_info, img_lab.size())
        if not pix.isNull():
            img_lab.setPixmap(
                pix.scaled(img_w, img_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        else:
            img_lab.setText("无图")
        vl.addWidget(img_lab)

        score_lab = QLabel(f"★ {pk['score']:.2f}")
        score_lab.setStyleSheet(
            "font-size:12.5px;font-weight:800;color:#e8964f;background:transparent;border:none;"
        )
        vl.addWidget(score_lab)

        reason_lab = QLabel(pk.get("reason", ""))
        reason_lab.setStyleSheet(
            "font-size:10px;color:#8a97a8;background:transparent;border:none;"
        )
        reason_lab.setWordWrap(True)
        reason_lab.setFixedHeight(34)
        vl.addWidget(reason_lab)

        if pk.get("group"):
            gb = QLabel(f"相似组 {pk.get('group_size', 0)} 张")
            gb.setStyleSheet(
                "font-size:9.5px;color:#5b7bd5;font-weight:700;"
                "background:rgba(120,150,255,0.14);border-radius:8px;"
                "padding:1px 8px;border:none;"
            )
            gb.setAlignment(Qt.AlignCenter)
            vl.addWidget(gb)
        vl.addStretch(1)
        return card

    # --------------------------------------------------------
    # 🤖 AI 精选（一级页：全库照片挑最值得保留的）
    # --------------------------------------------------------
    _PQ_ALL_KEY = "__all__"


    def _build_ai_pick_page(self):
        """一级「AI精选」页：Aurora 入口卡 + 全库精选网格。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        title = QLabel("🤖 AI精选")
        title.setStyleSheet(
            "font-size:26px;font-weight:800;color:#1f2d3d;background:transparent;border:none;"
        )
        layout.addWidget(title)
        sub = QLabel("AI帮你从照片中挑出最值得保留的照片")
        sub.setStyleSheet(
            "font-size:13px;color:#8a97a8;background:transparent;border:none;"
        )
        layout.addWidget(sub)

        # ── Aurora 入口卡（轻微极光 + hover 彩色流动）──
        hero = AuroraGlassCard()
        hero.setFixedHeight(120)
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(20, 14, 20, 14)
        hero_layout.setSpacing(8)

        hero_top = QHBoxLayout()
        hero_top.setSpacing(10)
        self.ai_pick_status = QLabel("尚未分析 · 点击「开始 AI 精选」扫描全部照片")
        self.ai_pick_status.setStyleSheet(
            "font-size:13px;color:#4a5a6a;background:transparent;border:none;"
        )
        hero_top.addWidget(self.ai_pick_status)
        hero_top.addStretch(1)
        self.ai_pick_count = QLabel("")
        self.ai_pick_count.setStyleSheet(
            "font-size:12px;color:#5b7bd5;font-weight:700;"
            "background:rgba(255,255,255,0.5);border-radius:9px;padding:2px 10px;"
            "border:1px solid rgba(255,255,255,0.6);"
        )
        hero_top.addWidget(self.ai_pick_count)
        hero_layout.addLayout(hero_top)

        self.ai_pick_start_btn = GlassButton("✨  开始AI精选", variant="accent")
        self.ai_pick_start_btn.setStyleSheet(
            "QPushButton{font-size:14px;font-weight:800;padding:9px 26px;}"
        )
        hero_layout.addWidget(self.ai_pick_start_btn, alignment=Qt.AlignLeft)
        layout.addWidget(hero)

        # ── 结果标题 + 网格 ──
        self.ai_pick_result_title = QLabel("")
        self.ai_pick_result_title.setStyleSheet(
            "font-size:15px;font-weight:700;color:#1f2d3d;background:transparent;border:none;"
        )
        layout.addWidget(self.ai_pick_result_title)

        self.ai_pick_scroll = QScrollArea()
        self.ai_pick_scroll.setWidgetResizable(True)
        self.ai_pick_scroll.setStyleSheet(
            "QScrollArea{border:none;background:transparent;}"
            "QScrollArea > QWidget > QWidget{background:transparent;}"
        )
        self.ai_pick_grid_container = QWidget()
        self.ai_pick_grid = QGridLayout(self.ai_pick_grid_container)
        self.ai_pick_grid.setSpacing(12)
        self.ai_pick_grid.setContentsMargins(0, 0, 0, 0)
        self.ai_pick_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.ai_pick_scroll.setWidget(self.ai_pick_grid_container)
        layout.addWidget(self.ai_pick_scroll, 1)

        self.ai_pick_start_btn.clicked.connect(self._start_ai_pick_all)
        return page


    def _all_photo_paths(self):
        """photos/ 目录全部图片（本地路径），供全库 AI 精选。"""
        photos_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "photos")
        )
        if not os.path.isdir(photos_dir):
            return []
        exts = {".jpg", ".jpeg", ".png", ".webp"}
        try:
            names = sorted(os.listdir(photos_dir))
        except OSError:
            return []
        return [
            os.path.join(photos_dir, n).replace("\\", "/")
            for n in names
            if os.path.splitext(n)[1].lower() in exts
        ]


    def _refresh_ai_pick_page(self):
        """进入 AI 精选页：读缓存渲染；无结果显示空态（不自动全库扫描）。"""
        try:
            analyzer = get_pq_analyzer()
            cached = analyzer.get_role_result(self._PQ_ALL_KEY)
        except Exception as e:
            print(f"[AI精选页] 缓存读取失败: {e}")
            cached = None
        if cached and cached.get("picks") is not None:
            self._render_ai_pick_page(cached)
            if cached.get("total"):
                self.ai_pick_status.setText("全库精选已生成")
                self.ai_pick_count.setText(f"{cached['total']} 张精选")
            else:
                self.ai_pick_status.setText("已分析，但没有达到精选标准的照片")
                self.ai_pick_count.setText("")
        else:
            self.ai_pick_status.setText("尚未分析 · 点击「开始 AI 精选」扫描全部照片")
            self.ai_pick_count.setText("")
            self.ai_pick_result_title.setText("")
            self._clear_ai_pick_grid()


    def _start_ai_pick_all(self):
        """后台分析全库照片（复用 photo_quality，不阻塞 GUI）。"""
        from ui.main_window_v3 import PhotoQualityWorker
        if getattr(self, "_ai_pick_worker", None) and self._ai_pick_worker.isRunning():
            return
        photos = self._all_photo_paths()
        if not photos:
            self.ai_pick_status.setText("未找到照片目录（photos/）")
            return
        try:
            analyzer = get_pq_analyzer()
        except Exception as e:
            self.ai_pick_status.setText(f"分析器不可用: {e}")
            return
        self.ai_pick_status.setText(f"分析中… 0/{len(photos)}")
        self.ai_pick_start_btn.setEnabled(False)
        self.ai_pick_result_title.setText("")
        self._clear_ai_pick_grid()
        worker = PhotoQualityWorker(analyzer, self._PQ_ALL_KEY, photos, force=False)
        worker.progress.connect(self._on_ai_pick_progress)
        worker.finished.connect(self._on_ai_pick_all_done)
        self._ai_pick_worker = worker
        worker.start()


    def _on_ai_pick_progress(self, done, total):
        if total:
            self.ai_pick_status.setText(f"分析中… {done}/{total}")


    def _on_ai_pick_all_done(self, role_key, result):
        self.ai_pick_start_btn.setEnabled(True)
        self._ai_pick_worker = None
        if result is None:
            self.ai_pick_status.setText("分析失败，请重试")
            return
        n = result.get("total", 0)
        analyzed = result.get("analyzed", 0)
        cached = result.get("cached", 0)
        if analyzed or cached:
            self.ai_pick_status.setText(
                f"已分析 {analyzed} 张" + (f" · 缓存 {cached} 张" if cached else "")
            )
        else:
            self.ai_pick_status.setText("没有可分析的照片")
        self.ai_pick_count.setText(f"{n} 张精选" if n else "暂无精选")
        self._render_ai_pick_page(result)
        toast.show(self, f"AI 精选完成 · {n} 张推荐", kind="success")


    def _render_ai_pick_page(self, result):
        """渲染「今日 AI 精选」照片网格。"""
        picks = result.get("picks") or []
        self.ai_pick_result_title.setText(
            f"✨ 今日 AI 精选 · {len(picks)} 张" if picks else "✨ 今日 AI 精选 · 暂无推荐"
        )
        self._clear_ai_pick_grid()
        cols = 6
        for idx, pk in enumerate(picks[:60]):  # 最多展示 60 张
            card = self._make_pick_card(pk, None)
            r, c = divmod(idx, cols)
            self.ai_pick_grid.addWidget(card, r, c)


    def _clear_ai_pick_grid(self):
        while self.ai_pick_grid.count():
            item = self.ai_pick_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

