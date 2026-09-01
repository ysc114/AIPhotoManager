"""
duplicates_page.py —— ♻️ 重复照片管理中心

Liquid Glass / Aurora 风格独立页面：
- 自动扫描照片库，按 MD5 分组（完全相同才归组，不误判"看起来相似"）
- 每组显示缩略图 / 文件名 / 大小 / 路径 + 「共 X 个副本」
- 支持选择要保留的文件、批量选择副本、全部选择 / 反选 / 删除选中
- 删除前二次确认；安全规则：每组至少保留 1 个（自动保留最大文件）
- 删除后刷新本页并发出 data_changed 信号（主窗口刷新图库/角色/统计）

与角色身份彻底分离：core.duplicates.DuplicateCleaner 只删该文件自身
记录（identity_image / favorite / analysis_cache），不碰角色组与 embedding。
"""

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QScrollArea,
    QCheckBox, QFrame, QPushButton, QMessageBox,
)

from core.duplicates import DuplicateScanner, DuplicateCleaner
from core.thumbnail_cache import thumbnail_cache
from config.settings_manager import settings as S


def _fmt_size(b):
    if b >= 1 << 20:
        return f"{b / (1 << 20):.1f} MB"
    if b >= 1 << 10:
        return f"{b // (1 << 10)} KB"
    return f"{b} B"


class DuplicatesPage(QWidget):
    """重复照片管理页（扫描 → 选择 → 确认删除 → 刷新）。"""

    data_changed = Signal()          # 删除完成后通知主窗口（图库/角色/统计刷新）

    def __init__(self, photos_dir=None, parent=None):
        super().__init__(parent)
        self._photos_dir = photos_dir
        self._scanner = DuplicateScanner(photos_dir)
        self._cleaner = DuplicateCleaner(photos_dir)
        self._groups = []            # 扫描结果 [{md5, paths:[...]}]
        self._sel = {}               # 绝对路径 -> 是否选中（删除）

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
        outer.addLayout(head)

        # 操作行
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
        self._cards_layout.addStretch(1)
        self._scroll.setWidget(self._cards_host)
        outer.addWidget(self._scroll, 1)

    # --------------------------------------------------------
    # 刷新
    # --------------------------------------------------------
    def refresh(self):
        """重新扫描 + 重建列表。"""
        self._groups = self._scanner.scan()
        self._sel = {}
        for g in self._groups:
            for item in g["paths"]:
                self._sel[item["path"]] = False
        self._rebuild()

    def _rebuild(self):
        # 清空旧卡片
        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        n_groups = len(self._groups)
        n_files = sum(len(g["paths"]) for g in self._groups)
        self._stats.setText(f"发现 {n_groups} 组重复照片 · {n_files} 个文件")
        if n_groups == 0:
            empty = QLabel("✅ 未发现完全相同的重复照片。")
            empty.setStyleSheet(
                "font-size:15px;color:#7c8ba0;padding:60px 0;background:transparent;border:none;")
            empty.setAlignment(Qt.AlignCenter)
            self._cards_layout.insertWidget(0, empty)
            return
        for gi, g in enumerate(self._groups):
            self._cards_layout.insertWidget(gi, self._build_group_card(g))
        self._update_delete_btn()

    def _build_group_card(self, g):
        """单个重复组卡片。"""
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

        # 组头：完全相同 · MD5 一致 · 共 X 个副本
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

        # 副本行
        for item in g["paths"]:
            lay.addWidget(self._build_item_row(g, item))
        return card

    def _build_item_row(self, g, item):
        row = QFrame()
        row.setStyleSheet("QFrame{background:rgba(255,255,255,0.42);border-radius:12px;border:none;}")
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(12)

        # 选择框
        cb = QCheckBox()
        cb.setChecked(self._sel.get(item["path"], False))
        cb.toggled.connect(
            lambda checked, p=item["path"]: self._on_toggled(p, checked))
        h.addWidget(cb)

        # 缩略图
        img = QLabel()
        img.setFixedSize(52, 52)
        img.setAlignment(Qt.AlignCenter)
        img.setStyleSheet("background:rgba(240,244,250,0.6);border-radius:8px;border:none;")
        try:
            cp = thumbnail_cache.get_cached(item["path"], 128)
        except Exception:
            cp = None
        from PySide6.QtGui import QPixmap
        if cp:
            px = QPixmap(cp)
            if not px.isNull():
                img.setPixmap(px.scaled(52, 52, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                img.setText("🖼")
        else:
            img.setText("🖼")
        h.addWidget(img)

        # 文件名 + 大小 + 路径
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

        # 保留按钮
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
    # 交互
    # --------------------------------------------------------
    def _on_toggled(self, path, checked):
        self._sel[path] = bool(checked)
        self._update_delete_btn()

    def _on_keep(self, path, group):
        """保留这个：取消选中本副本，同组其余全部选中（保一删多）。"""
        self._sel[path] = False
        for item in group["paths"]:
            self._sel[item["path"]] = item["path"] != path
        self._rebuild()

    def _select_all(self):
        for g in self._groups:
            for item in g["paths"]:
                self._sel[item["path"]] = True
        # 安全：每组自动保留最大文件
        self._ensure_keep_one()
        self._rebuild()

    def _invert(self):
        for p in self._sel:
            self._sel[p] = not self._sel[p]
        self._ensure_keep_one()
        self._rebuild()

    def _ensure_keep_one(self):
        """安全规则：每组至少保留 1 个（自动取消选中组内最大文件）。"""
        for g in self._groups:
            paths = [item["path"] for item in g["paths"]]
            selected = [p for p in paths if self._sel.get(p)]
            if len(selected) == len(paths):
                # 保留组内最大文件
                biggest = max(g["paths"], key=lambda x: x["size"])
                self._sel[biggest["path"]] = False

    def _update_delete_btn(self):
        n = sum(1 for v in self._sel.values() if v)
        self._delete_btn.setEnabled(n > 0)
        self._delete_btn.setText(f"删除选中 ({n})")

    def _delete_selected(self):
        selected = [p for p, v in self._sel.items() if v]
        if not selected:
            return
        self._ensure_keep_one()   # 双保险
        selected = [p for p in selected if self._sel.get(p)]
        if not selected:
            self._rebuild()
            return
        # 二次确认
        ret = QMessageBox.question(
            self, "确认删除",
            f"确定删除选中的 {len(selected)} 个重复副本？\n\n"
            "删除后将同步清理对应的照片记录，且无法恢复。\n"
            "不会影响角色分组、合照多角色归属与 AI 数据。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        # 只删属于重复组的文件（MD5 安全集合）
        keep_md5 = {g["md5"] for g in self._groups}
        result = self._cleaner.delete_paths(selected, keep_md5_set=keep_md5)
        msg = f"已删除 {len(result['deleted'])} 个副本"
        if result["failed"]:
            msg += f" · {len(result['failed'])} 个失败"
        QMessageBox.information(self, "删除完成", msg)
        self.refresh()
        if result["deleted"]:
            self.data_changed.emit()   # 通知主窗口刷新图库/角色/统计
