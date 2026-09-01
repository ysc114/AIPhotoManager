"""
overview_mixin —— MainWindow 页面方法拆分（纯移动，方法体零修改）。

由 ui/main_window_v3.py 拆分而来，保持接口/行为完全一致。
"""

import json
import os

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QSize, QPoint, QEvent
from PySide6.QtCore import QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QPixmap, QColor, QFont, QPainter, QImage, QIcon
from PySide6.QtWidgets import (
    QLabel, QWidget, QFrame, QPushButton, QGridLayout, QVBoxLayout,
    QHBoxLayout, QMessageBox, QScrollArea, QStackedWidget, QLineEdit,
    QComboBox, QFileDialog, QListWidget, QListWidgetItem, QSplitter,
    QInputDialog, QDialog, QDialogButtonBox, QAbstractItemView,
    QGraphicsOpacityEffect, QGraphicsDropShadowEffect,
)

from config.settings_manager import settings as S
from core.thumbnail_cache import thumbnail_cache


class _OverviewMixinMixin:
    """收藏/预览等页面方法（运行时绑定 MainWindow 实例）。"""

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
                from core.identity import get_reader
                mgr = get_reader()   # 共享只读连接
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


    def _switch_page(self, row):

        if row < 0 or row >= self.content_stack.count():
            return

        self.content_stack.setCurrentIndex(row)
        self._fade_in_page()

        # 底部导航胶囊跟随（任何切页路径统一同步）
        if hasattr(self, "bottom_nav"):
            self.bottom_nav.set_current(row)

        # 切到总览页时刷新统计（构造期间 _ui_ready=False 不触发，
        # 避免测试进程打开真实库；启动后由 QTimer / 用户点击触发）
        if row == 0 and self._ui_ready:
            self._refresh_overview()

        # 内存优化（2026-08-31）：切离分组页时释放其卡片（销毁 pixmap /
        # 投影 effect / widget，数据保留在 state["groups"]，重建仅 ~0.8s 且
        # 缩略图缓存命中）。避免 226 张卡片常驻 ~270MB。
        if self._ui_ready:
            cur_key = {3: "fursuit", 4: "person", 5: "character"}.get(row)
            for k in ("fursuit", "person", "character"):
                if k != cur_key and self._group_page_loaded.get(k):
                    st = self._group_pages.get(k)
                    if st:
                        self._clear_grid(st["grid_layout"])
                        for card in list(self._card_group_map.keys()):
                            if self._card_group_map[card][0] == k:
                                self._card_group_map.pop(card, None)

        # Phase 2：切到分组页（兽装3/人物4/角色5）时懒加载组列表
        # （同样受 _ui_ready 保护，避免测试进程触发后端读取）
        if self._ui_ready:
            page_key_map = {3: "fursuit", 4: "person", 5: "character"}
            key = page_key_map.get(row)
            if key and not self._group_page_loaded.get(key, False):
                self._load_groups_into_page(key)
                self._group_page_loaded[key] = True
            # AI 精选页（row 1）：进入时刷新（读缓存或空态）
            if row == 1:
                self._refresh_ai_pick_page()
            # Phase 3-1：收藏页（row 6）懒加载收藏列表
            if row == 6:
                self._load_favorites_page()
            # Phase 3-3：设置页（row 8）懒刷新状态
            if row == 8:
                self._refresh_settings_page()
            # ♻️ 重复照片页（row 9）：进入时重新扫描
            if row == 9:
                self.duplicates_page.refresh()


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


    def _open_group_from_search(self, group):
        """搜索结果点击角色 → 进入现有角色详情页（复用 _open_group）。"""
        gtype = str(group.get("type") or "")
        if gtype == "fursuit_character":
            page_key, row = "fursuit", 3
        elif gtype == "real_person":
            page_key, row = "person", 4
        else:
            page_key, row = "character", 5
        # 未加载则懒加载（_switch_page 内部也会处理）
        if not self._group_page_loaded.get(page_key, False):
            self._load_groups_into_page(page_key)
            self._group_page_loaded[page_key] = True
        display_name = group.get("name") or f"角色 {str(group.get('character_id') or '')[:10]}"
        self._switch_page(row)
        self._open_group(page_key, group, display_name)


    def _open_photo_by_path(self, path):
        """搜索结果点击照片 → 打开现有照片预览（复用 show_preview）。"""
        resolved = self._resolve_display_path(path) or path
        if resolved in self.image_list:
            idx = self.image_list.index(resolved)
        else:
            self.image_list.append(resolved)
            self.image_list_widget.addItem(os.path.basename(resolved))
            idx = len(self.image_list) - 1
        self._switch_page(2)  # 照片页
        self.show_preview(idx)


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
        self._refresh_ai_picks(page_key)


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

