"""
AIPhotoManager 设置中心（Settings Center）

独立于 main_window_v3.py 的设置页面：
- 所有状态读写走 config.settings_manager.SettingsManager
- UI 只负责「读取设置 → 显示控件 → 用户修改 → SettingsManager 保存」
- 界面模式 / 主题 / Liquid Glass 修改后立即回调主窗口预览
- 提供：立即备份 / 打开目录 / AI 数据统计 / 关于（git commit）

注意：设置页构建时不连数据库（避免 WAL 副作用），
AI 数据统计在 refresh() 时惰性读取。
"""

import json
import os
import shutil
import sqlite3
import subprocess
import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QComboBox,
    QCheckBox,
    QSpinBox,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QScrollArea,
    QFrame,
    QToolButton,
    QMessageBox,
)

from config.settings_manager import settings as S

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _project_path(sub=""):
    """解析相对项目根的路径。"""
    p = _PROJECT_ROOT / sub if sub else _PROJECT_ROOT
    return str(p)


# ============================================================
# 设置中心页面
# ============================================================
class SettingsCenterPage(QWidget):
    """完整设置中心（滚动 + 分区）。win 为主窗口引用（主题预览/动作回调）。"""

    def __init__(self, win=None, parent=None):
        super().__init__(parent)
        self.win = win
        self._stats_cache = None   # (timestamp, stats) 统计结果缓存
        self._build()

    # --------------------------------------------------------
    # 构建
    # --------------------------------------------------------
    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 头部
        head = QVBoxLayout()
        head.setSpacing(2)
        title = QLabel("⚙️ 设置中心")
        title.setStyleSheet(
            "font-size:24px;font-weight:800;color:#1f2d3d;"
            "background:transparent;border:none;"
        )
        head.addWidget(title)
        note = QLabel(
            "管理界面、AI 识别、扫描、备份与数据。修改 AI 参数不会自动重新整理已有角色，"
            "仅影响后续分析。"
        )
        note.setStyleSheet("font-size:12px;color:#8a97a8;background:transparent;border:none;")
        note.setWordWrap(True)
        head.addWidget(note)
        layout.addLayout(head)
        layout.addSpacing(6)

        # 滚动区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        container = QWidget()
        self._body = QVBoxLayout(container)
        self._body.setContentsMargins(0, 0, 8, 0)
        self._body.setSpacing(14)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        # ── 各分区 ──
        self._section_ai()
        self._section_scan()
        self._section_ui()
        self._section_storage()
        self._section_backup()
        self._section_data()
        self._section_notify()
        self._section_shortcuts()
        self._section_advanced()
        self._section_about()

        self._body.addStretch()

    # --------------------------------------------------------
    # 分区工具
    # --------------------------------------------------------
    def _section(self, emoji_title):
        """创建分区面板（玻璃卡容器），返回 (panel, body_layout)。"""
        panel = QFrame()
        panel.setStyleSheet("""
            QFrame {
                background: rgba(255,255,255,0.55);
                border: 1px solid rgba(255,255,255,0.75);
                border-radius: 16px;
            }
        """)
        p_layout = QVBoxLayout(panel)
        p_layout.setContentsMargins(16, 12, 16, 14)
        p_layout.setSpacing(10)
        head = QLabel(emoji_title)
        head.setStyleSheet(
            "font-size:14.5px;font-weight:700;color:#1f2d3d;"
            "background:transparent;border:none;"
        )
        p_layout.addWidget(head)
        body = QVBoxLayout()
        body.setSpacing(8)
        p_layout.addLayout(body)
        self._body.addWidget(panel)
        return panel, body

    def _row(self, body, label_text, widget, note=None):
        """一行：标签 + 控件 + 可选说明。"""
        row = QHBoxLayout()
        row.setSpacing(10)
        lab = QLabel(label_text)
        lab.setStyleSheet(
            "font-size:13px;color:#4a5a6a;background:transparent;border:none;"
        )
        lab.setMinimumWidth(140)
        row.addWidget(lab)
        row.addWidget(widget)
        if note:
            n = QLabel(note)
            n.setStyleSheet("font-size:11px;color:#a0aab8;background:transparent;border:none;")
            n.setWordWrap(True)
            row.addWidget(n, 1)
        else:
            row.addStretch(1)
        body.addLayout(row)

    def _combo(self, items, key, on_change=None):
        c = QComboBox()
        for text, val in items:
            c.addItem(text, val)
        cur = S.get(key)
        idx = c.findData(cur)
        c.setCurrentIndex(idx if idx >= 0 else 0)

        def _changed():
            S.set(key, c.currentData())
            if on_change:
                on_change()
        c.currentIndexChanged.connect(_changed)
        return c

    def _check(self, key, on_change=None):
        cb = QCheckBox()
        cb.setChecked(bool(S.get(key)))

        def _changed():
            S.set(key, cb.isChecked())
            if on_change:
                on_change()
        cb.toggled.connect(_changed)
        return cb

    def _glass_btn(self, text, gradient, slot):
        b = QPushButton(text)
        b.setStyleSheet(
            f"QPushButton{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {gradient[0]},stop:1 {gradient[1]});color:white;border:none;"
            f"padding:8px 20px;border-radius:16px;font-size:12.5px;font-weight:600;}}"
            f"QPushButton:hover{{opacity:0.9;}}"
        )
        b.clicked.connect(slot)
        return b

    # --------------------------------------------------------
    # ① 🧠 AI 识别
    # --------------------------------------------------------
    def _section_ai(self):
        _, body = self._section("🧠 AI 识别")
        rows = [
            ("L1 分类", "CLIP（AIClassifier）"),
            ("兽装识别", "Fursee（YOLO + 512D）"),
            ("人物识别", "Face（InsightFace）"),
            ("兽装匹配阈值", f"{S.get('ai.fursuit_threshold')}"),
            ("人物匹配阈值", f"{S.get('ai.face_threshold')}"),
            ("MD5 去重", "开启" if S.get('ai.md5_dedup') else "关闭"),
        ]
        for k, v in rows:
            lab = QLabel(f"{k}：{v}")
            lab.setStyleSheet(
                "font-size:13px;color:#4a5a6a;background:rgba(255,255,255,0.5);"
                "border:1px solid rgba(255,255,255,0.6);border-radius:10px;padding:8px 12px;"
            )
            body.addWidget(lab)

        # 高级 AI 折叠区
        fold = QToolButton()
        fold.setText("▶ 高级 AI 设置")
        fold.setCheckable(True)
        fold.setStyleSheet(
            "QToolButton{font-size:12.5px;color:#5b7bd5;font-weight:600;"
            "background:transparent;border:none;padding:4px 0;text-align:left;}"
        )
        adv = QFrame()
        adv.setStyleSheet(
            "QFrame{background:rgba(255,255,255,0.35);border-radius:12px;"
            "border:1px solid rgba(255,255,255,0.5);}"
        )
        adv_layout = QVBoxLayout(adv)
        adv_layout.setContentsMargins(12, 8, 12, 8)
        for k, v in [
            ("Fursee eps", S.get("ai.fursee_eps")),
            ("min_samples", S.get("ai.min_samples")),
            ("metric", S.get("ai.metric")),
            ("embedding 类型", S.get("ai.embedding_type")),
        ]:
            lab = QLabel(f"{k}：{v}")
            lab.setStyleSheet(
                "font-size:12px;color:#7c8ba0;background:transparent;border:none;"
            )
            adv_layout.addWidget(lab)
        tip = QLabel("修改这些参数不会自动重新整理已有角色，仅影响后续分析。")
        tip.setStyleSheet("font-size:11px;color:#a0aab8;background:transparent;border:none;")
        tip.setWordWrap(True)
        adv_layout.addWidget(tip)
        adv.hide()

        def _toggle():
            adv.setVisible(fold.isChecked())
            fold.setText("▼ 高级 AI 设置" if fold.isChecked() else "▶ 高级 AI 设置")
        fold.toggled.connect(_toggle)
        body.addWidget(fold)
        body.addWidget(adv)

    # --------------------------------------------------------
    # ② 📡 照片扫描
    # --------------------------------------------------------
    def _section_scan(self):
        _, body = self._section("📡 照片扫描设置")
        self._row(body, "扫描新照片", self._check("scan.auto_scan_photos"))
        self._row(body, "自动识别兽装", self._check("scan.auto_detect_fursuit"))
        self._row(body, "自动识别人物", self._check("scan.auto_detect_person"))
        self._row(body, "跳过重复照片", self._check("scan.skip_duplicates"))
        self._row(body, "MD5 内容去重", self._check("scan.md5_dedup"))
        note = QLabel("扫描行为：新照片会通过增量识别加入已有角色（incremental_assign），"
                      "不会重新打散已有角色。")
        note.setStyleSheet("font-size:11px;color:#a0aab8;background:transparent;border:none;")
        note.setWordWrap(True)
        body.addWidget(note)

    # --------------------------------------------------------
    # ③ 🖥️ 界面设置
    # --------------------------------------------------------
    def _section_ui(self):
        _, body = self._section("🖥️ 界面设置")
        self._row(
            body, "界面模式",
            self._combo(
                [("新版界面", "new"), ("经典版界面", "classic")],
                "ui.mode",
                on_change=lambda: self._theme_changed("界面模式将在下次启动时完全生效，视觉已即时预览。"),
            ),
            note="仅切换外观，不改变数据库 / AI / 角色数据",
        )
        self._row(
            body, "主题",
            self._combo(
                [("跟随系统", "system"), ("浅色", "light"), ("深色", "dark")],
                "ui.theme",
                on_change=lambda: self._theme_changed(),
            ),
        )
        self._row(
            body, "Liquid Glass",
            self._check("ui.liquid_glass", on_change=lambda: self._theme_changed()),
            note="半透明玻璃效果",
        )
        self._row(
            body, "动画",
            self._check("ui.animation", on_change=lambda: self._theme_changed()),
        )
        self._row(
            body, "缩略图大小",
            self._combo(
                [(f"{s}px", s) for s in (110, 138, 160, 200)],
                "ui.thumbnail_size",
                on_change=lambda: self._note_restart(),
            ),
            note="重启后生效",
        )
        self._row(
            body, "每行照片数量",
            self._combo(
                [(f"{n} 张", n) for n in (4, 5, 6, 7, 8)],
                "ui.grid_columns",
                on_change=lambda: self._note_restart(),
            ),
            note="重启后生效",
        )
        self._row(body, "圆角照片", self._check("ui.rounded_photos", on_change=lambda: self._theme_changed()))
        self._row(body, "显示照片信息", self._check("ui.show_photo_info"))

    # --------------------------------------------------------
    # ④ 📂 存储设置
    # --------------------------------------------------------
    def _section_storage(self):
        _, body = self._section("📂 存储设置")
        self._storage_rows = []
        for key, label, sub in [
            ("storage.photos_dir", "照片目录", "photos"),
            ("storage.data_dir", "数据目录", ""),
            ("storage.backup_dir", "备份目录", "backups"),
        ]:
            val = S.get(key) or _project_path(sub)
            row = QHBoxLayout()
            lab = QLabel(f"{label}：")
            lab.setStyleSheet("font-size:13px;color:#4a5a6a;background:transparent;border:none;")
            lab.setMinimumWidth(100)
            path_lab = QLabel(val)
            path_lab.setStyleSheet(
                "font-size:12px;color:#7c8ba0;background:rgba(255,255,255,0.5);"
                "border:1px solid rgba(255,255,255,0.6);border-radius:8px;padding:5px 10px;"
            )
            path_lab.setWordWrap(True)
            btn = QPushButton("打开")
            btn.setStyleSheet(
                "QPushButton{background:rgba(255,255,255,0.65);color:#3a5a7a;"
                "border:1px solid rgba(255,255,255,0.8);padding:5px 14px;"
                "border-radius:12px;font-size:11.5px;font-weight:600;}"
                "QPushButton:hover{background:rgba(255,255,255,0.9);}"
            )
            btn.clicked.connect(lambda _, p=val: self._open_dir(p))
            row.addWidget(lab)
            row.addWidget(path_lab, 1)
            row.addWidget(btn)
            body.addLayout(row)
            self._storage_rows.append((key, path_lab))

        tip = QLabel("注意：不允许直接修改数据库文件。数据操作请通过「数据与备份」区进行。")
        tip.setStyleSheet("font-size:11px;color:#a0aab8;background:transparent;border:none;")
        tip.setWordWrap(True)
        body.addWidget(tip)

    # --------------------------------------------------------
    # ⑤ 💾 数据与备份
    # --------------------------------------------------------
    def _section_backup(self):
        _, body = self._section("💾 数据与备份")
        self._row(body, "自动备份", self._check("backup.auto_backup"))
        self._row(
            body, "备份频率",
            self._combo(
                [("每天", "daily"), ("每周", "weekly"), ("手动", "manual")],
                "backup.frequency",
            ),
        )
        sp = QSpinBox()
        sp.setRange(1, 60)
        sp.setValue(int(S.get("backup.keep_count", 7)))
        sp.setStyleSheet(
            "QSpinBox{background:rgba(255,255,255,0.65);border:1px solid rgba(255,255,255,0.8);"
            "border-radius:10px;padding:4px 8px;font-size:13px;}"
        )

        def _keep_changed():
            S.set("backup.keep_count", sp.value())
        sp.valueChanged.connect(_keep_changed)
        self._row(body, "保留备份数量", sp)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addWidget(
            self._glass_btn("💾 立即备份", ("#6fb7f5", "#9b8cf0"), self._do_backup_now)
        )
        btn_row.addWidget(
            self._glass_btn("📂 打开备份目录", ("#57c78a", "#6aaee8"), lambda: self._open_dir(
                S.get("storage.backup_dir") or _project_path("backups")
            ))
        )
        btn_row.addStretch(1)
        body.addLayout(btn_row)

        self._backup_status = QLabel("")
        self._backup_status.setStyleSheet(
            "font-size:12px;color:#7c8ba0;background:transparent;border:none;"
        )
        body.addWidget(self._backup_status)

        restore = QPushButton("⚠️ 恢复备份（暂未开放）")
        restore.setEnabled(False)
        restore.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.4);color:#b0b8c4;"
            "border:1px solid rgba(255,255,255,0.5);padding:6px 14px;"
            "border-radius:12px;font-size:12px;}"
        )
        body.addWidget(restore, alignment=Qt.AlignLeft)

    # --------------------------------------------------------
    # ⑥ 🔍 AI 数据管理
    # --------------------------------------------------------
    def _section_data(self):
        _, body = self._section("🔍 AI 数据管理")
        self._ai_stats_label = QLabel("点击「刷新」查看数据统计…")
        self._ai_stats_label.setStyleSheet(
            "font-size:12.5px;color:#4a5a6a;background:rgba(255,255,255,0.5);"
            "border:1px solid rgba(255,255,255,0.6);border-radius:10px;padding:10px 12px;"
        )
        self._ai_stats_label.setWordWrap(True)
        body.addWidget(self._ai_stats_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addWidget(self._glass_btn("🔄 刷新统计", ("#6fb7f5", "#9b8cf0"), self.refresh_data_stats))
        btn_row.addWidget(self._glass_btn("📡 重新扫描新照片", ("#57c78a", "#6aaee8"), self._rescan))
        btn_row.addWidget(self._glass_btn("🧹 清理无效缓存", ("#c29ae8", "#9b8cf0"), self._clean_cache))
        btn_row.addStretch(1)
        body.addLayout(btn_row)

        tip = QLabel("不提供「重新聚类全部照片」。全量重聚会拆散人工合并结果，请使用「AI 找候选 → 人工确认 → 合并」流程。")
        tip.setStyleSheet("font-size:11px;color:#a0aab8;background:transparent;border:none;")
        tip.setWordWrap(True)
        body.addWidget(tip)

    # --------------------------------------------------------
    # ⑦ 🔔 通知设置
    # --------------------------------------------------------
    def _section_notify(self):
        _, body = self._section("🔔 通知设置")
        self._row(body, "分析完成通知", self._check("notifications.analysis_done"))
        self._row(body, "扫描完成通知", self._check("notifications.scan_done"))
        self._row(body, "新角色发现通知", self._check("notifications.new_character"))
        self._row(body, "错误通知", self._check("notifications.error"))

    # --------------------------------------------------------
    # ⑧ ⌨️ 快捷键
    # --------------------------------------------------------
    def _section_shortcuts(self):
        _, body = self._section("⌨️ 快捷键")
        shortcuts = [
            ("打开照片", "Ctrl+O"),
            ("扫描新照片", "Ctrl+S"),
            ("收藏照片", "Ctrl+F"),
            ("下一张", "→"),
            ("上一张", "←"),
            ("返回", "Esc"),
            ("搜索", "Ctrl+Shift+F"),
        ]
        grid = QGridLayout()
        grid.setSpacing(8)
        for i, (k, v) in enumerate(shortcuts):
            kl = QLabel(k)
            kl.setStyleSheet("font-size:13px;color:#4a5a6a;background:transparent;border:none;")
            vl = QLabel(v)
            vl.setStyleSheet(
                "font-size:12px;color:#5b7bd5;font-weight:600;"
                "background:rgba(255,255,255,0.5);border-radius:8px;padding:3px 10px;"
                "border:1px solid rgba(255,255,255,0.6);"
            )
            vl.setAlignment(Qt.AlignCenter)
            grid.addWidget(kl, i, 0)
            grid.addWidget(vl, i, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 0)
        body.addLayout(grid)
        tip = QLabel("快捷键为默认值（第一版只读展示）。")
        tip.setStyleSheet("font-size:11px;color:#a0aab8;background:transparent;border:none;")
        body.addWidget(tip)

    # --------------------------------------------------------
    # ⑨ 🛠️ 高级设置
    # --------------------------------------------------------
    def _section_advanced(self):
        _, body = self._section("🛠️ 高级设置")
        fold = QToolButton()
        fold.setText("▶ 展开高级参数")
        fold.setCheckable(True)
        fold.setStyleSheet(
            "QToolButton{font-size:12.5px;color:#5b7bd5;font-weight:600;"
            "background:transparent;border:none;padding:4px 0;text-align:left;}"
        )
        adv = QFrame()
        adv.setStyleSheet(
            "QFrame{background:rgba(255,255,255,0.35);border-radius:12px;"
            "border:1px solid rgba(255,255,255,0.5);}"
        )
        adv_layout = QVBoxLayout(adv)
        adv_layout.setContentsMargins(12, 8, 12, 8)
        self._row(adv_layout, "Debug 模式", self._check("advanced.debug_mode"))
        self._row(
            adv_layout, "日志等级",
            self._combo(
                [("DEBUG", "DEBUG"), ("INFO", "INFO"), ("WARNING", "WARNING"), ("ERROR", "ERROR")],
                "advanced.log_level",
            ),
        )
        self._row(
            adv_layout, "缓存策略",
            self._combo(
                [("激进", "aggressive"), ("均衡", "balanced"), ("保守", "conservative")],
                "advanced.cache_strategy",
            ),
        )
        adv.hide()

        def _toggle():
            adv.setVisible(fold.isChecked())
            fold.setText("▼ 收起高级参数" if fold.isChecked() else "▶ 展开高级参数")
        fold.toggled.connect(_toggle)
        body.addWidget(fold)
        body.addWidget(adv)

    # --------------------------------------------------------
    # ⑩ ℹ️ 关于
    # --------------------------------------------------------
    def _section_about(self):
        _, body = self._section("ℹ️ 关于 AIPhotoManager")
        info = [
            ("产品", "AIPhotoManager V4"),
            ("UI 版本", "Liquid Glass 设置中心"),
            ("兽装识别", "Fursee（YOLO + 512D embedding）"),
            ("人物识别", "Face（InsightFace）"),
            ("数据库", f"Schema v{self._schema_version()}"),
            ("Git commit", self._git_commit()),
            ("GitHub", "github.com/ysc114/AIPhotoManager"),
        ]
        for k, v in info:
            lab = QLabel(f"{k}：{v}")
            lab.setStyleSheet(
                "font-size:13px;color:#4a5a6a;background:rgba(255,255,255,0.5);"
                "border:1px solid rgba(255,255,255,0.6);border-radius:10px;padding:8px 12px;"
            )
            body.addWidget(lab)
        self._db_status_label = QLabel("")
        self._db_status_label.setStyleSheet(
            "font-size:12px;color:#7c8ba0;background:transparent;border:none;"
        )
        body.addWidget(self._db_status_label)

    # ========================================================
    # 行为
    # ========================================================
    def refresh(self):
        """切到设置页时刷新动态数据（惰性，避免构造副作用）。"""
        self.refresh_data_stats()
        self._refresh_db_status()

    def refresh_data_stats(self):
        """读取数据库统计（只读，带 30s 结果缓存）。"""
        stats = self._collect_stats()
        self._ai_stats_label.setText(
            f"Fursee detection：{stats['fursee_det']} ｜ Fursee 角色：{stats['fursee_groups']}\n"
            f"Face 人物：{stats['face']} ｜ 已分析照片：{stats['analyzed']}\n"
            f"未分析照片：{stats['unanalyzed']} ｜ photos/ 图片：{stats['photos_total']}\n"
            f"重复副本（磁盘，未入库）：{stats['dup_disk']}"
        )

    def _collect_stats(self):
        """统计采集（30 秒内复用结果，避免频繁切页全量 MD5 读盘）。"""
        now = datetime.datetime.now().timestamp()
        if self._stats_cache and now - self._stats_cache[0] < 30:
            return self._stats_cache[1]
        out = {
            "fursee_det": 0, "fursee_groups": 0, "face": 0,
            "analyzed": 0, "unanalyzed": 0, "photos_total": 0, "dup_disk": 0,
        }
        db_path = _project_path("identity_db.sqlite")
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            c = con.cursor()
            out["fursee_det"] = c.execute(
                "SELECT COUNT(*) FROM identity_image WHERE embedding_type='fursuit_fursee'"
            ).fetchone()[0]
            out["fursee_groups"] = c.execute(
                "SELECT COUNT(DISTINCT group_id) FROM identity_image "
                "WHERE embedding_type='fursuit_fursee'"
            ).fetchone()[0]
            out["face"] = c.execute(
                "SELECT COUNT(*) FROM identity_image WHERE embedding_type='face'"
            ).fetchone()[0]
            out["analyzed"] = c.execute(
                "SELECT COUNT(DISTINCT image_path) FROM identity_image"
            ).fetchone()[0]
            con.close()
        except Exception:
            pass
        # photos/ 目录
        photos = S.get("storage.photos_dir") or _project_path("photos")
        exts = (".jpg", ".jpeg", ".png", ".webp")
        if os.path.isdir(photos):
            files = [f for f in os.listdir(photos) if os.path.splitext(f)[1].lower() in exts]
            out["photos_total"] = len(files)
            out["unanalyzed"] = max(0, len(files) - out["analyzed"])
            # 磁盘重复副本（按 MD5 分组，多余副本数；仅统计未入库，避免全量 MD5 开销）
            try:
                con = sqlite3.connect(f"file:{_project_path('identity_db.sqlite')}?mode=ro", uri=True)
                db_paths = {os.path.basename(r[0]) for r in con.execute(
                    "SELECT DISTINCT image_path FROM identity_image"
                )}
                con.close()
            except Exception:
                db_paths = set()
            seen = {}
            for f in files:
                if f in db_paths:
                    continue
                try:
                    import hashlib
                    m = hashlib.md5(open(os.path.join(photos, f), "rb").read()).hexdigest()[:12]
                    seen.setdefault(m, []).append(f)
                except OSError:
                    pass
            out["dup_disk"] = sum(len(v) - 1 for v in seen.values() if len(v) > 1)
        self._stats_cache = (now, out)
        return out

    def _schema_version(self):
        try:
            con = sqlite3.connect(f"file:{_project_path('identity_db.sqlite')}?mode=ro", uri=True)
            v = con.execute("PRAGMA user_version").fetchone()[0]
            con.close()
            return v
        except Exception:
            return "—"

    def _refresh_db_status(self):
        try:
            con = sqlite3.connect(f"file:{_project_path('identity_db.sqlite')}?mode=ro", uri=True)
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            n_img = con.execute("SELECT COUNT(*) FROM identity_image").fetchone()[0]
            n_grp = con.execute("SELECT COUNT(*) FROM identity_group").fetchone()[0]
            con.close()
            self._db_status_label.setText(f"数据库状态：integrity={integrity} ｜ identity_image {n_img} 行 ｜ identity_group {n_grp} 组")
        except Exception:
            self._db_status_label.setText("数据库状态：读取失败")

    @staticmethod
    def _git_commit():
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=_project_path(), capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                return r.stdout.strip()
        except Exception:
            pass
        return "—"

    # --------------------------------------------------------
    # 动作
    # --------------------------------------------------------
    def _theme_changed(self, extra=None):
        """主题类设置修改 → 立即预览。"""
        if self.win and hasattr(self.win, "_apply_theme"):
            self.win._apply_theme()
        if extra:
            self._backup_status.setText(extra)

    def _note_restart(self):
        self._backup_status.setText("部分界面设置将在重启 AIPhotoManager 后生效。")

    def _open_dir(self, path):
        if not os.path.isdir(path):
            path = _project_path()
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _do_backup_now(self):
        """立即备份 identity_db.sqlite 到备份目录（sqlite backup API）。"""
        try:
            src = _project_path("identity_db.sqlite")
            if not os.path.exists(src):
                self._backup_status.setText("未找到数据库文件。")
                return
            bk_root = S.get("storage.backup_dir") or "backups"
            if not os.path.isabs(bk_root):
                bk_root = os.path.join(_project_path(), bk_root)
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            target_dir = os.path.join(bk_root, f"autobackup_{stamp}")
            os.makedirs(target_dir, exist_ok=True)
            target = os.path.join(target_dir, "identity_db.sqlite")
            src_con = sqlite3.connect(src)
            dst_con = sqlite3.connect(target)
            src_con.backup(dst_con)
            dst_con.close()
            src_con.close()
            # 清理超过保留数量的自动备份
            self._prune_backups(bk_root)
            self._backup_status.setText(
                f"✅ 备份完成：{target_dir}\\identity_db.sqlite"
            )
        except Exception as e:
            self._backup_status.setText(f"❌ 备份失败：{e}")

    def _prune_backups(self, bk_root):
        keep = int(S.get("backup.keep_count", 7) or 7)
        try:
            dirs = sorted(
                d for d in os.listdir(bk_root)
                if d.startswith("autobackup_") and os.path.isdir(os.path.join(bk_root, d))
            )
            for old in dirs[:-keep]:
                shutil.rmtree(os.path.join(bk_root, old), ignore_errors=True)
        except Exception:
            pass

    def _rescan(self):
        """触发主窗口「扫描新照片」（复用既有增量链路）。"""
        if self.win and hasattr(self.win, "_scan_photos_dir"):
            self.win._scan_photos_dir()
            if hasattr(self.win, "_switch_page"):
                self.win._switch_page(6)
        else:
            self._backup_status.setText("无法触发扫描（主窗口未就绪）。")

    def _clean_cache(self):
        """清理 analysis_cache.json 中的无效空条目（{} / null）。"""
        cache_path = _project_path("analysis_cache.json")
        if not os.path.exists(cache_path):
            self._backup_status.setText("analysis_cache.json 不存在。")
            return
        ret = QMessageBox.question(
            self, "清理无效缓存",
            "将删除 analysis_cache.json 中的空缓存条目（{} 等），\n"
            "这些照片下次分析时会重新分类。继续？",
        )
        if ret != QMessageBox.Yes:
            return
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            bad = [k for k, v in cache.items() if not v]
            for k in bad:
                cache.pop(k, None)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
            self._backup_status.setText(f"✅ 已清理 {len(bad)} 条无效缓存。")
        except Exception as e:
            self._backup_status.setText(f"❌ 清理失败：{e}")
