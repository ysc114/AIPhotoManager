"""
duplicates_page.py —— ♻️ 重复照片管理中心（MD5 完全一致 + 👀 疑似重复视觉相似）

Liquid Glass / Aurora 风格独立页面，分两个区块：
- ⑵ 👀 疑似重复（视觉相似）：dHash + 直方图 + 技术指标 找"连拍/构图几乎
  一致/轻微糊/曝光不同"的候选组；**AI 只推荐，绝不自动删除**；
  人工「保留此张」→ 其余仅标记为待清理候选（落盘 JSON，不删文件）；
  「忽略该组」→ 永久不再推荐。首次计算在后台线程，结果缓存复用。
- ⑰ 完全相同（MD5 一致）：原功能原样保留（扫描/勾选/安全删除）。

与角色身份彻底分离：本页不写 identity_db、不触碰角色组/character_id；
MD5 删除仍走 core.duplicates.DuplicateCleaner（只清理该文件自身记录）。
"""

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QScrollArea, QGridLayout,
    QCheckBox, QFrame, QPushButton, QMessageBox, QFileDialog,
)

from core.duplicates import DuplicateScanner, DuplicateCleaner
from core.visual_duplicates import VisualDuplicateIndex
from core.thumbnail_cache import thumbnail_cache
from config.settings_manager import settings as S


def _fmt_size(b):
    if b >= 1 << 20:
        return f"{b / (1 << 20):.1f} MB"
    if b >= 1 << 10:
        return f"{b // (1 << 10)} KB"
    return f"{b} B"


def _apply_thumb(label, cache_path, w, h):
    """后台缩略图就绪 → 主线程刷新（页面已关闭时静默忽略）。"""
    if not cache_path or label.parent() is None:
        return
    try:
        from PySide6.QtGui import QPixmap
        px = QPixmap(cache_path)
        if px.isNull():
            return
        label.setPixmap(px.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    except Exception as e:
        print(f"[重复照片] 缩略图刷新失败: {e}")


def _load_thumb(label, path, w, h):
    """读取缩略图：缓存命中直接显示；未命中显示占位并后台生成后刷新。"""
    cp = None
    try:
        cp = thumbnail_cache.get_cached(path, 128)
    except Exception:
        cp = None
    if cp:
        try:
            from PySide6.QtGui import QPixmap
            px = QPixmap(cp)
            if not px.isNull():
                label.setPixmap(px.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                return
        except Exception:
            pass
    label.setText("🖼")
    try:
        thumbnail_cache.request(
            path, 128, None,
            on_ready=lambda c, lab=label, W=w, H=h: _apply_thumb(lab, c, W, H))
    except Exception:
        pass


class _VisualScanWorker(QThread):
    """后台计算视觉指纹（首次/有新照片时），完成后返回候选组。"""

    progress = Signal(int, int)         # (done, total)
    done = Signal(object)               # groups list
    failed = Signal(str)

    def __init__(self, index, parent=None):
        super().__init__(parent)
        self._index = index

    def run(self):
        try:
            self._index.compute_all(
                progress_cb=lambda d, t: self.progress.emit(d, t))
            groups = self._index.groups()
            self.done.emit(groups)
        except Exception as e:
            self.failed.emit(str(e))


class DuplicatesPage(QWidget):
    """重复照片管理页（视觉相似 + MD5 完全相同）。"""

    data_changed = Signal()          # 删除完成后通知主窗口（图库/角色/统计刷新）

    def __init__(self, photos_dir=None, index_path=None, parent=None):
        super().__init__(parent)
        self._photos_dir = photos_dir
        self._scanner = DuplicateScanner(photos_dir)
        self._cleaner = DuplicateCleaner(photos_dir)
        self._visual = VisualDuplicateIndex(photos_dir, index_path)
        self._groups = []            # MD5 扫描结果 [{md5, paths:[...]}]
        self._sel = {}               # 绝对路径 -> 是否选中（删除）
        self._visual_groups = []     # 视觉候选组
        self._visual_worker = None
        self._similar_query = ""     # ③ 相似搜索当前查询照片
        self._similar_results = []   # ③ 相似搜索结果

        self._build_ui()
        self.refresh()

    # --------------------------------------------------------
    # UI 构建
    # --------------------------------------------------------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 14)
        outer.setSpacing(12)

        # 标题行
        head = QHBoxLayout()
        title = QLabel("♻️ 重复照片")
        title.setStyleSheet(
            "font-size:20px;font-weight:800;color:#2a3a52;background:transparent;border:none;")
        head.addWidget(title)
        self._stats = QLabel("")
        self._stats.setStyleSheet(
            "font-size:13px;color:#6b7a90;background:transparent;border:none;")
        head.addWidget(self._stats)
        head.addStretch(1)

        # 视觉相似区块操作（扫描/状态/待清理统计）
        self._visual_btn = QPushButton("👀 检测视觉相似")
        self._visual_btn.setCursor(Qt.PointingHandCursor)
        self._visual_btn.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #7fb2ff,stop:1 #8f8cff);color:white;border:none;"
            "padding:7px 16px;border-radius:14px;font-size:12.5px;font-weight:700;}"
            "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #6ba3f5,stop:1 #7f7cf0);}"
            "QPushButton:disabled{background:rgba(200,200,210,0.6);color:#9aa6b8;}"
        )
        self._visual_btn.clicked.connect(self._start_visual_scan)
        head.addWidget(self._visual_btn)
        self._visual_stats = QLabel("")
        self._visual_stats.setStyleSheet(
            "font-size:12px;color:#3f7bd5;background:rgba(110,160,255,0.14);"
            "border-radius:9px;padding:3px 10px;border:none;font-weight:700;")
        head.addWidget(self._visual_stats)
        outer.addLayout(head)

        # MD5 区块操作行（原功能保留）
        bar = QHBoxLayout()
        for text, slot, danger in (
            ("全部选择", self._select_all, False),
            ("反选", self._invert, False),
            ("删除选中", self._delete_selected, True),
        ):
            btn = QPushButton(text)
            btn.setCursor(Qt.PointingHandCursor)
            if danger:
                btn.setStyleSheet(
                    "QPushButton{background:#e8707e;color:white;border:none;"
                    "padding:7px 18px;border-radius:14px;font-size:12.5px;font-weight:700;}"
                    "QPushButton:hover{background:#dd5f6e;}"
                    "QPushButton:disabled{background:rgba(220,220,225,0.6);color:#a0aab8;}"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton{background:rgba(255,255,255,0.75);color:#3a5a7a;"
                    "border:1px solid rgba(255,255,255,0.9);padding:7px 18px;"
                    "border-radius:14px;font-size:12.5px;font-weight:600;}"
                    "QPushButton:hover{background:rgba(255,255,255,0.95);}"
                )
            btn.clicked.connect(slot)
            bar.addWidget(btn)
        bar.addStretch(1)
        self._delete_btn = bar.itemAt(2).widget()
        outer.addLayout(bar)

        # 滚动区
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:vertical{background:transparent;width:8px;margin:2px;}"
            "QScrollBar::handle:vertical{background:rgba(150,165,190,0.5);border-radius:4px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
        )
        self._cards_host = QWidget()
        self._cards_host.setStyleSheet("background:transparent;")
        self._cards_layout = QVBoxLayout(self._cards_host)
        self._cards_layout.setContentsMargins(0, 0, 4, 0)
        self._cards_layout.setSpacing(12)
        self._scroll.setWidget(self._cards_host)
        outer.addWidget(self._scroll, 1)

    # --------------------------------------------------------
    # 刷新（MD5 + 视觉）
    # --------------------------------------------------------
    def refresh(self):
        """重新扫描 MD5（同步）+ 视觉指纹（增量后台或缓存直出）。"""
        self._groups = self._scanner.scan()
        self._sel = {}
        for g in self._groups:
            for item in g["paths"]:
                self._sel[item["path"]] = False
        # 视觉：有新/变更照片才后台重算；否则直接用缓存分组
        if self._visual_worker is not None and self._visual_worker.isRunning():
            self._rebuild()
            return
        if self._visual.stale_files():
            self._start_visual_scan()
        else:
            self._visual_groups = self._visual.groups()
            self._rebuild()

    def _start_visual_scan(self):
        if self._visual_worker is not None and self._visual_worker.isRunning():
            return
        self._visual_btn.setEnabled(False)
        self._visual_stats.setText("检测中…")
        worker = _VisualScanWorker(self._visual)
        worker.progress.connect(self._on_visual_progress)
        worker.done.connect(self._on_visual_done)
        worker.failed.connect(self._on_visual_failed)
        self._visual_worker = worker
        worker.start()

    def _on_visual_progress(self, done, total):
        self._visual_stats.setText(f"检测中… {done}/{total}")

    def _on_visual_done(self, groups):
        self._visual_worker = None
        self._visual_btn.setEnabled(True)
        self._visual_groups = groups or []
        self._rebuild()

    def _on_visual_failed(self, err):
        self._visual_worker = None
        self._visual_btn.setEnabled(True)
        self._visual_stats.setText("检测失败")
        QMessageBox.information(self, "视觉检测失败", f"计算照片指纹时出错：{err}")

    # --------------------------------------------------------
    # 重建列表（视觉区块 + MD5 区块）
    # --------------------------------------------------------
    def _rebuild(self):
        # 清空全部
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        # ── ③ 🔎 相似照片搜索（给定一张 → 库内最相似 N 张）──
        self._cards_layout.addWidget(self._build_search_section())

        # ── ⑵ 👀 疑似重复（视觉相似）──
        self._cards_layout.addWidget(self._build_visual_section())

        # ── ⑰ 完全相同（MD5）──
        n_groups = len(self._groups)
        n_files = sum(len(g["paths"]) for g in self._groups)
        md5_head = QLabel(f"⑰ 完全相同（MD5） · {n_groups} 组 / {n_files} 个文件")
        md5_head.setStyleSheet(
            "font-size:13.5px;font-weight:800;color:#2a3a52;background:transparent;border:none;")
        self._cards_layout.addWidget(md5_head)
        if n_groups == 0:
            empty = QLabel("✅ 未发现完全相同的重复照片。")
            empty.setStyleSheet(
                "font-size:13px;color:#7c8ba0;padding:10px 0;background:transparent;border:none;")
            empty.setAlignment(Qt.AlignCenter)
            self._cards_layout.addWidget(empty)
        else:
            for gi, g in enumerate(self._groups):
                self._cards_layout.addWidget(self._build_group_card(g))
        self._cards_layout.addStretch(1)
        self._update_delete_btn()

    # --------------------------------------------------------
    # ③ 🔎 相似照片搜索区块
    # --------------------------------------------------------
    def _build_search_section(self):
        frame = QFrame()
        _ga = float(S.get("ui.glass_opacity", 0.55))
        _cr = int(S.get("ui.corner_radius", 18))
        frame.setStyleSheet(f"""
            QFrame {{
                background: rgba(255,255,255,{max(0.3, _ga - 0.18)});
                border: 1px solid rgba(255,255,255,0.8);
                border-radius: {_cr}px;
            }}
        """)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(10)

        head = QHBoxLayout()
        tag = QLabel("🔎 相似照片搜索")
        tag.setStyleSheet(
            "font-size:12px;color:#0f9d8a;background:rgba(80,210,190,0.16);"
            "border-radius:9px;padding:3px 10px;border:none;font-weight:700;")
        head.addWidget(tag)
        hint = QLabel("选一张照片 → 找出同场景/同角色/连拍的最相似照片")
        hint.setStyleSheet("font-size:11px;color:#8a97a8;background:transparent;border:none;")
        head.addWidget(hint)
        head.addStretch(1)
        pick = QPushButton("📂 选择照片")
        pick.setCursor(Qt.PointingHandCursor)
        pick.setStyleSheet(
            "QPushButton{background:rgba(80,210,190,0.18);color:#0f9d8a;border:none;"
            "padding:5px 14px;border-radius:12px;font-size:11.5px;font-weight:700;}"
            "QPushButton:hover{background:rgba(80,210,190,0.30);}")
        pick.clicked.connect(self._on_pick_similar)
        head.addWidget(pick)
        lay.addLayout(head)

        status = QLabel(
            f"查询：{os.path.basename(self._similar_query)}" if self._similar_query else
            "尚未选择照片")
        status.setStyleSheet("font-size:11px;color:#6b7a90;background:transparent;border:none;")
        lay.addWidget(status)

        if not self._similar_results:
            empty = QLabel("点击「📂 选择照片」开始（不会修改任何数据）")
            empty.setStyleSheet(
                "font-size:12px;color:#a5b2c2;padding:6px 0;background:transparent;border:none;")
            lay.addWidget(empty)
            return frame

        grid = QGridLayout()
        grid.setSpacing(10)
        for i, item in enumerate(self._similar_results[:12]):
            grid.addWidget(self._build_similar_card(item), i // 4, i % 4)
        lay.addLayout(grid)
        return frame

    def _build_similar_card(self, item):
        card = QFrame()
        card.setFixedSize(150, 132)
        card.setStyleSheet(
            "QFrame{background:rgba(255,255,255,0.5);border-radius:12px;border:none;}")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(8, 8, 8, 8)
        cl.setSpacing(4)
        img = QLabel()
        img.setFixedSize(96, 72)
        img.setAlignment(Qt.AlignCenter)
        img.setStyleSheet("background:rgba(240,244,250,0.6);border-radius:8px;border:none;")
        _load_thumb(img, item["path"], 96, 72)
        cl.addWidget(img, 0, Qt.AlignHCenter)
        name = QLabel(item["name"])
        name.setStyleSheet(
            "font-size:10.5px;font-weight:600;color:#33445c;background:transparent;border:none;")
        name.setToolTip(item["path"])
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(name.font())
        name.setText(fm.elidedText(item["name"], Qt.ElideMiddle, 132))
        cl.addWidget(name)
        score = QLabel(f"相似 {item['score'] * 100:.0f}%")
        score.setStyleSheet(
            "font-size:10.5px;color:#0f9d8a;font-weight:700;background:transparent;border:none;")
        cl.addWidget(score)
        return card

    def _on_pick_similar(self):
        default_dir = self._photos_dir or str(
            Path(__file__).resolve().parent.parent / "photos")
        path, _ = QFileDialog.getOpenFileName(
            self, "选择照片（找相似）", default_dir,
            "图片 (*.jpg *.jpeg *.png *.webp)")
        if path:
            self._run_similar_search(path)

    def _run_similar_search(self, query_path):
        """同步检索（指纹已索引或现场计算，~200 张毫秒级），重建区块。"""
        from core.visual_duplicates import norm_path
        self._similar_query = norm_path(query_path)
        try:
            self._similar_results = self._visual.search_similar(query_path) or []
        except Exception as e:
            print(f"[相似搜索] 失败: {e}")
            self._similar_results = []
        self._rebuild()

    # --------------------------------------------------------
    # 👀 视觉相似区块
    # --------------------------------------------------------
    def _build_visual_section(self):
        frame = QFrame()
        _ga = float(S.get("ui.glass_opacity", 0.55))
        _cr = int(S.get("ui.corner_radius", 18))
        frame.setStyleSheet(f"""
            QFrame {{
                background: rgba(255,255,255,{max(0.3, _ga - 0.18)});
                border: 1px solid rgba(255,255,255,0.8);
                border-radius: {_cr}px;
            }}
        """)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(10)

        head = QHBoxLayout()
        tag = QLabel("👀 疑似重复 · 视觉相似")
        tag.setStyleSheet(
            "font-size:12px;color:#7a52e8;background:rgba(150,130,255,0.16);"
            "border-radius:9px;padding:3px 10px;border:none;font-weight:700;")
        head.addWidget(tag)
        hint = QLabel("连拍/构图相似/轻微糊/曝光不同 · AI 只推荐，绝不自动删除")
        hint.setStyleSheet("font-size:11px;color:#8a97a8;background:transparent;border:none;")
        head.addWidget(hint)
        head.addStretch(1)
        pending = self._visual.pending_cleanup()
        self._cleanup_btn = QPushButton(f"🗑 清理已标记 ({len(pending)})")
        self._cleanup_btn.setCursor(Qt.PointingHandCursor)
        self._cleanup_btn.setEnabled(bool(pending))
        self._cleanup_btn.setStyleSheet(
            "QPushButton{background:#e8707e;color:white;border:none;"
            "padding:5px 14px;border-radius:12px;font-size:11.5px;font-weight:700;}"
            "QPushButton:hover{background:#dd5f6e;}"
            "QPushButton:disabled{background:rgba(220,220,225,0.7);color:#a0aab8;}")
        self._cleanup_btn.clicked.connect(self._on_cleanup_marked)
        head.addWidget(self._cleanup_btn)
        self._visual_stats.setText(
            f"候选 {len(self._visual_groups)} 组 · 待清理标记 {len(pending)} 张")
        lay.addLayout(head)

        if not self._visual_groups:
            note = QLabel(
                "尚未发现疑似重复（点击右上「👀 检测视觉相似」）"
                if self._visual.stale_files() else
                "✅ 未发现疑似重复照片。")
            note.setStyleSheet(
                "font-size:13px;color:#7c8ba0;padding:6px 0;background:transparent;border:none;")
            note.setAlignment(Qt.AlignCenter)
            lay.addWidget(note)
            return frame
        for g in self._visual_groups:
            lay.addWidget(self._build_visual_group_card(g))
        return frame

    def _build_visual_group_card(self, g):
        card = QFrame()
        card.setStyleSheet(
            "QFrame{background:rgba(255,255,255,0.45);border-radius:14px;border:none;}")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)

        head = QHBoxLayout()
        score = QLabel(f"相似 {g['score'] * 100:.0f}%")
        score.setStyleSheet(
            "font-size:11.5px;color:#7a52e8;background:rgba(150,130,255,0.18);"
            "border-radius:9px;padding:2px 10px;border:none;font-weight:700;")
        head.addWidget(score)
        reason = QLabel(g.get("reason", ""))
        reason.setStyleSheet("font-size:11px;color:#6b7a90;background:transparent;border:none;")
        head.addWidget(reason)
        head.addStretch(1)
        ignore = QPushButton("忽略该组")
        ignore.setCursor(Qt.PointingHandCursor)
        ignore.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.7);color:#6b7a90;border:1px solid rgba(255,255,255,0.9);"
            "padding:4px 12px;border-radius:11px;font-size:11px;font-weight:600;}"
            "QPushButton:hover{background:rgba(255,255,255,0.95);}")
        ignore.clicked.connect(lambda _=False, gg=g: self._on_visual_ignore(gg))
        head.addWidget(ignore)
        lay.addLayout(head)

        for ph in g["photos"]:
            lay.addWidget(self._build_visual_photo_row(g, ph))
        return card

    def _build_visual_photo_row(self, g, ph):
        row = QFrame()
        row.setStyleSheet("QFrame{background:rgba(255,255,255,0.42);border-radius:12px;border:none;}")
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(12)

        img = QLabel()
        img.setFixedSize(72, 72)
        img.setAlignment(Qt.AlignCenter)
        img.setStyleSheet("background:rgba(240,244,250,0.6);border-radius:10px;border:none;")
        _load_thumb(img, ph["path"], 72, 72)
        h.addWidget(img)

        info = QVBoxLayout()
        info.setSpacing(2)
        name = QLabel(ph["name"])
        name.setStyleSheet(
            "font-size:12.5px;font-weight:600;color:#33445c;background:transparent;border:none;")
        meta = QLabel(
            f"{_fmt_size(ph['size'])} · {ph['w']}×{ph['h']} · "
            f"清晰 {ph['sharp']:.2f} · 曝光 {ph['exposure']:.2f}")
        meta.setStyleSheet("font-size:10.5px;color:#8a97a8;background:transparent;border:none;")
        info.addWidget(name)
        info.addWidget(meta)
        h.addLayout(info, 1)

        if self._visual.candidate_mark(ph["path"]):
            mark = QLabel("已标记待清理")
            mark.setStyleSheet(
                "font-size:10.5px;color:#e8964f;background:rgba(255,170,80,0.16);"
                "border-radius:9px;padding:2px 10px;border:none;font-weight:700;")
            h.addWidget(mark)
        elif self._visual.is_resolved(ph["path"]):
            mark = QLabel("保留")
            mark.setStyleSheet(
                "font-size:10.5px;color:#3f9d6b;background:rgba(90,200,140,0.16);"
                "border-radius:9px;padding:2px 10px;border:none;font-weight:700;")
            h.addWidget(mark)
        else:
            keep = QPushButton("保留此张")
            keep.setCursor(Qt.PointingHandCursor)
            keep.setStyleSheet(
                "QPushButton{background:rgba(90,170,255,0.16);color:#3f7bd5;border:none;"
                "padding:5px 14px;border-radius:12px;font-size:11.5px;font-weight:700;}"
                "QPushButton:hover{background:rgba(90,170,255,0.28);}")
            keep.clicked.connect(
                lambda _, gg=g, p=ph: self._on_visual_keep(gg, p))
            h.addWidget(keep)
        return row

    # --------------------------------------------------------
    # 视觉交互（只记录决策，不删除文件）
    # --------------------------------------------------------
    def _on_visual_keep(self, g, photo):
        """保留 photo：同组其余照片标记为待清理候选。"""
        candidates = [p["path"] for p in g["photos"] if p["path"] != photo["path"]]
        self._visual.resolve(photo["path"], candidates)
        self._visual_groups = self._visual.groups()
        self._rebuild()

    def _on_visual_ignore(self, g):
        """忽略该组：组内全部两两对记录为忽略（整组不再推荐）。"""
        paths = [p["path"] for p in g["photos"]]
        for i in range(len(paths)):
            for j in range(i + 1, len(paths)):
                self._visual.ignore_group(paths[i], paths[j])
        self._visual_groups = self._visual.groups()
        self._rebuild()

    # --------------------------------------------------------
    # 🗑 清理已标记待清理（人工确认后删除；同步索引/数据库/缓存）
    # --------------------------------------------------------
    def _on_cleanup_marked(self):
        candidates = self._visual.pending_cleanup()
        if not candidates:
            return
        ret = QMessageBox.question(
            self, "确认清理",
            f"删除 {len(candidates)} 张已标记「待清理」的照片？\n\n"
            "· 每张都是你「保留此张」确认过：同组保留的那张不会删除\n"
            "· 删除文件并同步清理对应照片记录（不影响角色分组/合照归属）\n"
            "· 不可恢复",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        result = self._commit_cleanup(candidates)
        msg = f"已删除 {len(result['deleted'])} 张"
        if result["failed"]:
            msg += f" · {len(result['failed'])} 张失败（未删除）"
        QMessageBox.information(self, "清理完成", msg)
        self.refresh()
        if result["deleted"]:
            self.data_changed.emit()

    def _commit_cleanup(self, candidates):
        """执行清理：删除文件 + 记录清理 + 视觉索引条目移除（可测试入口）。"""
        result = self._cleaner.delete_paths(candidates)
        try:
            self._visual.remove_entries(result["deleted"])
        except Exception as e:
            print(f"[重复照片] 视觉索引清理失败: {e}")
        return result

    # --------------------------------------------------------
    # MD5 区块（原功能原样保留）
    # --------------------------------------------------------
    def _build_group_card(self, g):
        """单个 MD5 重复组卡片。"""
        card = QFrame()
        _ga = float(S.get("ui.glass_opacity", 0.55))
        _cr = int(S.get("ui.corner_radius", 18))
        card.setStyleSheet(f"""
            QFrame {{
                background: rgba(255,255,255,{_ga});
                border: 1px solid rgba(255,255,255,0.8);
                border-radius: {_cr}px;
            }}
        """)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(10)

        head = QHBoxLayout()
        tag = QLabel("完全相同 · MD5 一致")
        tag.setStyleSheet(
            "font-size:11px;color:#3f7bd5;background:rgba(110,160,255,0.14);"
            "border-radius:9px;padding:3px 10px;border:none;font-weight:700;")
        n = len(g["paths"])
        cnt = QLabel(f"共 {n} 个副本")
        cnt.setStyleSheet("font-size:12px;color:#6b7a90;background:transparent;border:none;")
        md5s = QLabel(f"MD5 {g['md5'][:10]}…")
        md5s.setStyleSheet("font-size:10.5px;color:#9aa6b8;background:transparent;border:none;")
        head.addWidget(tag)
        head.addWidget(cnt)
        head.addStretch(1)
        head.addWidget(md5s)
        lay.addLayout(head)

        for item in g["paths"]:
            lay.addWidget(self._build_item_row(g, item))
        return card

    def _build_item_row(self, g, item):
        row = QFrame()
        row.setStyleSheet("QFrame{background:rgba(255,255,255,0.42);border-radius:12px;border:none;}")
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(12)

        cb = QCheckBox()
        cb.setChecked(self._sel.get(item["path"], False))
        cb.toggled.connect(
            lambda checked, p=item["path"]: self._on_toggled(p, checked))
        h.addWidget(cb)

        img = QLabel()
        img.setFixedSize(52, 52)
        img.setAlignment(Qt.AlignCenter)
        img.setStyleSheet("background:rgba(240,244,250,0.6);border-radius:8px;border:none;")
        _load_thumb(img, item["path"], 52, 52)
        h.addWidget(img)

        info = QVBoxLayout()
        info.setSpacing(2)
        name = QLabel(item["name"])
        name.setStyleSheet(
            "font-size:12.5px;font-weight:600;color:#33445c;background:transparent;border:none;")
        meta = QLabel(f"{_fmt_size(item['size'])} · {os.path.dirname(item['path'])}")
        meta.setStyleSheet("font-size:10.5px;color:#8a97a8;background:transparent;border:none;")
        info.addWidget(name)
        info.addWidget(meta)
        h.addLayout(info, 1)

        keep = QPushButton("保留这个")
        keep.setCursor(Qt.PointingHandCursor)
        keep.setStyleSheet(
            "QPushButton{background:rgba(90,170,255,0.16);color:#3f7bd5;border:none;"
            "padding:5px 14px;border-radius:12px;font-size:11.5px;font-weight:700;}"
            "QPushButton:hover{background:rgba(90,170,255,0.28);}"
        )
        keep.clicked.connect(lambda _, p=item["path"], gd=g: self._on_keep(p, gd))
        h.addWidget(keep)

        row._path = item["path"]
        return row

    # --------------------------------------------------------
    # MD5 交互
    # --------------------------------------------------------
    def _on_toggled(self, path, checked):
        self._sel[path] = bool(checked)
        self._update_delete_btn()

    def _on_keep(self, path, group):
        self._sel[path] = False
        for item in group["paths"]:
            self._sel[item["path"]] = item["path"] != path
        self._rebuild()

    def _select_all(self):
        for g in self._groups:
            for item in g["paths"]:
                self._sel[item["path"]] = True
        self._ensure_keep_one()
        self._rebuild()

    def _invert(self):
        for p in self._sel:
            self._sel[p] = not self._sel[p]
        self._ensure_keep_one()
        self._rebuild()

    def _ensure_keep_one(self):
        for g in self._groups:
            paths = [item["path"] for item in g["paths"]]
            selected = [p for p in paths if self._sel.get(p)]
            if len(selected) == len(paths):
                biggest = max(g["paths"], key=lambda x: x["size"])
                self._sel[biggest["path"]] = False

    def _update_delete_btn(self):
        n = sum(1 for v in self._sel.values() if v)
        self._delete_btn.setEnabled(n > 0)
        self._delete_btn.setText(f"删除选中 ({n})")
        self._visual_stats.setText(
            f"候选 {len(self._visual_groups)} 组 · "
            f"待清理标记 {len(self._visual.pending_cleanup())} 张")

    def _delete_selected(self):
        selected = [p for p, v in self._sel.items() if v]
        if not selected:
            return
        self._ensure_keep_one()
        selected = [p for p in selected if self._sel.get(p)]
        if not selected:
            self._rebuild()
            return
        ret = QMessageBox.question(
            self, "确认删除",
            f"确定删除选中的 {len(selected)} 个重复副本？\n\n"
            "删除后将同步清理对应的照片记录，且无法恢复。\n"
            "不会影响角色分组、合照多角色归属与 AI 数据。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        keep_md5 = {g["md5"] for g in self._groups}
        result = self._cleaner.delete_paths(selected, keep_md5_set=keep_md5)
        msg = f"已删除 {len(result['deleted'])} 个副本"
        if result["failed"]:
            msg += f" · {len(result['failed'])} 个失败"
        QMessageBox.information(self, "删除完成", msg)
        self.refresh()
        if result["deleted"]:
            self.data_changed.emit()
