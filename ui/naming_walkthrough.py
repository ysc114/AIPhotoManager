# ui/naming_walkthrough.py
"""整理命名：逐个给「未命名角色」起名（人工确认，不自动改数据）。

交互：
- 只遍历未命名角色，显示该角色的 detection 裁剪封面 + 稳定序号 + 照片数
- 回车 = 保存并下一个；「跳过」= 不写库直接下一个；随时可关闭
- 写库通过注入的 name_saver 回调完成（UI 不承担业务逻辑，铁律 7）
- 只调用既有 update_name；不合并、不改 detection / 聚类参数
"""

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout,
)

from core.identity.naming import display_name


class UnnamedRolesDialog(QDialog):
    """未命名角色逐个命名对话框（模态）。"""

    COVER = QSize(220, 200)

    def __init__(self, groups, name_saver, cover_provider=None, parent=None):
        super().__init__(parent)
        self._groups = [g for g in (groups or [])
                        if not str(g.get("name") or "").strip()]
        self._saver = name_saver
        self._cover_provider = cover_provider
        self._idx = 0
        self.renamed_count = 0

        self.setWindowTitle("整理命名 · 未命名角色")
        self.setModal(True)
        self.setMinimumWidth(460)
        self._build_ui()
        self._show_current()

    # ---------------- UI ----------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(12)

        self._progress = QLabel("")
        self._progress.setStyleSheet(
            "font-size:12px;color:#6b7a90;background:transparent;border:none;")
        root.addWidget(self._progress)

        frame = QFrame()
        frame.setStyleSheet(
            "QFrame{background:rgba(255,255,255,0.72);"
            "border:1px solid rgba(255,255,255,0.9);border-radius:14px;}")
        box = QVBoxLayout(frame)
        box.setContentsMargins(14, 14, 14, 14)
        box.setSpacing(10)

        self._cover = QLabel("🖼")
        self._cover.setFixedSize(self.COVER)
        self._cover.setAlignment(Qt.AlignCenter)
        self._cover.setStyleSheet(
            "background:rgba(240,244,250,0.65);border-radius:10px;"
            "color:#b9c4d2;font-size:18px;border:none;")
        box.addWidget(self._cover, 0, Qt.AlignHCenter)

        self._title = QLabel("")
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setStyleSheet(
            "font-size:14px;font-weight:700;color:#2a3a52;"
            "background:transparent;border:none;")
        box.addWidget(self._title)

        self._sub = QLabel("")
        self._sub.setAlignment(Qt.AlignCenter)
        self._sub.setStyleSheet(
            "font-size:11.5px;color:#8a97a8;background:transparent;border:none;")
        box.addWidget(self._sub)
        root.addWidget(frame)

        self._edit = QLineEdit()
        self._edit.setPlaceholderText("输入角色名（回车保存并下一个）")
        self._edit.setStyleSheet(
            "QLineEdit{background:rgba(255,255,255,0.85);"
            "border:1px solid rgba(140,170,220,0.55);border-radius:12px;"
            "padding:8px 14px;font-size:13px;color:#2a3a4e;}"
            "QLineEdit:focus{border:1px solid rgba(110,160,255,0.9);}")
        self._edit.returnPressed.connect(self._on_save)
        root.addWidget(self._edit)

        row = QHBoxLayout()
        row.setSpacing(10)
        self._save_btn = QPushButton("保存并下一个")
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #6fb7f5,stop:1 #9b8cf0);color:white;border:none;"
            "padding:8px 18px;border-radius:14px;font-size:12.5px;font-weight:700;}"
            "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #5aa6ea,stop:1 #8a7ce6);}")
        self._save_btn.clicked.connect(self._on_save)
        row.addWidget(self._save_btn)

        self._skip_btn = QPushButton("跳过")
        self._skip_btn.setCursor(Qt.PointingHandCursor)
        self._skip_btn.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.8);color:#3a5a7a;"
            "border:1px solid rgba(255,255,255,0.95);padding:8px 18px;"
            "border-radius:14px;font-size:12.5px;font-weight:600;}"
            "QPushButton:hover{background:rgba(255,255,255,0.96);}")
        self._skip_btn.clicked.connect(self._on_skip)
        row.addWidget(self._skip_btn)

        row.addStretch(1)
        close_btn = QPushButton("结束整理")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#8a97a8;border:none;"
            "padding:8px 10px;font-size:12px;}"
            "QPushButton:hover{color:#3a5a7a;}")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        root.addLayout(row)

    # ---------------- 行为 ----------------
    def current_group(self):
        if 0 <= self._idx < len(self._groups):
            return self._groups[self._idx]
        return None

    def remaining(self):
        return max(0, len(self._groups) - self._idx)

    def _show_current(self):
        group = self.current_group()
        if group is None:
            self.accept()
            return
        total = len(self._groups)
        self._progress.setText(f"未命名角色 {self._idx + 1} / {total}")
        self._title.setText(display_name(None, group.get("type"),
                                         group.get("serial")))
        photos = group.get("count")
        if photos is None:
            photos = len(group.get("images") or [])
        self._sub.setText(f"出现 {int(photos or 0)} 张照片"
                          " · 命名只写名称，不动 detection/聚类")
        self._edit.clear()
        self._edit.setFocus()
        self._set_cover(group)

    def _set_cover(self, group):
        pix = None
        if self._cover_provider is not None:
            try:
                pix = self._cover_provider(group, self.COVER)
            except Exception:
                pix = None
        if pix is not None and not pix.isNull():
            self._cover.setText("")
            self._cover.setPixmap(pix.scaled(
                self.COVER, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self._cover.setPixmap(QPixmap())
            self._cover.setText("🖼")

    def _on_save(self):
        group = self.current_group()
        if group is None:
            return
        name = self._edit.text().strip()
        if name and self._saver is not None:
            ok = self._saver(group.get("character_id"), name)
            if ok is False:
                return          # 写入失败：停留当前角色，便于重试
            self.renamed_count += 1
        self._advance()

    def _on_skip(self):
        self._advance()

    def _advance(self):
        self._idx += 1
        if self._idx >= len(self._groups):
            self.accept()
            return
        self._show_current()
