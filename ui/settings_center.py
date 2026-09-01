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
from ui.components.glass_card import GlassCard
from ui.components.glass_button import GlassButton
from ui.components.animated_toggle import AnimatedToggle
from ui.components.glass_slider import GlassSlider
from ui.components.toast import toast

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
        self._section_panels = []  # 玻璃面板注册表（参数修改后刷新）
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

        # ── 各分区（macOS 风格 6 大组）──
        self._section_ai()           # 🧠 AI 识别
        self._section_roles()        # 👥 角色分类
        self._section_scan()         # 📡 照片扫描
        self._section_display()      # 🖥️ 显示
        self._section_data_manage()  # 🗂 数据管理（存储/备份/AI数据/通知/高级）
        self._section_version()      # 🛠 版本与兼容模式

        self._body.addStretch()

    # --------------------------------------------------------
    # 分区工具
    # --------------------------------------------------------
    def _section(self, emoji_title):
        """创建分区面板（GlassCard 玻璃容器），返回 (panel, body_layout)。"""
        panel = GlassCard(aurora=False)
        self._section_panels.append(panel)
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

    @staticmethod
    def _subtitle(body, text):
        """组内子标题（macOS 设置风格的小节分隔）。"""
        lab = QLabel(text)
        lab.setStyleSheet(
            "font-size:12px;font-weight:700;color:#8a97a8;"
            "background:transparent;border:none;padding:6px 2px 0 2px;"
            "letter-spacing:0.5px;"
        )
        body.addWidget(lab)

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
        cb = AnimatedToggle()
        cb.setChecked(bool(S.get(key)))

        def _changed():
            S.set(key, cb.isChecked())
            if on_change:
                on_change()
        cb.toggled.connect(_changed)
        return cb

    @staticmethod
    def _glass_shadow(widget, blur=22, dy=4, alpha=40):
        """设置面板投影（强度受 ui.shadow_strength 参数缩放）。"""
        from PySide6.QtWidgets import QGraphicsDropShadowEffect
        from PySide6.QtGui import QColor
        s = max(0.0, float(S.get("ui.shadow_strength", 40)) / 40.0)
        b = max(0.2, float(S.get("ui.glass_blur", 30)) / 30.0)
        effect = QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(max(1, int(blur * b)))
        effect.setOffset(0, dy)
        effect.setColor(QColor(30, 60, 110, max(0, int(alpha * s))))
        widget.setGraphicsEffect(effect)

    def apply_glass(self):
        """参数修改后刷新全部 GlassCard 面板（透明度 / 圆角 / 阴影）。"""
        for panel in self._section_panels:
            if hasattr(panel, "refresh_glass"):
                panel.refresh_glass()

    def _slider(self, key, lo, hi, step=1, scale=1.0, suffix="", on_change=None):
        """横向滑块（写入 SettingsManager，可选缩放显示值）。"""
        sl = GlassSlider(Qt.Horizontal)
        sl.setRange(lo, hi)
        sl.setSingleStep(step)
        sl.setFixedWidth(150)
        sl.setValue(int(float(S.get(key)) * scale))

        def _changed(v):
            S.set(key, round(v / scale, 2))
            if on_change:
                on_change()
        sl.valueChanged.connect(_changed)
        wrap = QWidget()
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(8)
        wl.addWidget(sl)
        val = QLabel(f"{S.get(key)}{suffix}")
        val.setStyleSheet("font-size:12px;color:#5b7bd5;font-weight:600;"
                          "background:rgba(255,255,255,0.5);border-radius:8px;"
                          "padding:2px 8px;border:1px solid rgba(255,255,255,0.6);")
        wl.addWidget(val)
        sl.valueChanged.connect(lambda v, lb=val: lb.setText(f"{round(v / scale, 2)}{suffix}"))
        return wrap

    def _glass_btn(self, text, gradient, slot, variant=None):
        b = GlassButton(text, variant=variant or "accent")
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
    # ② 👥 角色分类
    # --------------------------------------------------------
    def _section_roles(self):
        _, body = self._section("👥 角色分类")
        cards = [
            ("🐾 兽装角色", "Fursee（YOLO 检测 + 512D embedding）",
             "兽装照片经 Fursee 识别后按 0.79 阈值增量归组。"),
            ("👤 人物角色", "Face（InsightFace 人脸识别）",
             "人物照片经人脸识别后按 0.92 阈值增量归组。"),
        ]
        for title, engine, desc in cards:
            card = QFrame()
            card.setStyleSheet(
                "QFrame{background:rgba(255,255,255,0.5);border-radius:12px;"
                "border:1px solid rgba(255,255,255,0.6);}"
            )
            cl = QVBoxLayout(card)
            cl.setContentsMargins(12, 10, 12, 10)
            t = QLabel(f"{title} · {engine}")
            t.setStyleSheet("font-size:13px;font-weight:700;color:#1f2d3d;background:transparent;border:none;")
            cl.addWidget(t)
            d = QLabel(desc)
            d.setStyleSheet("font-size:11.5px;color:#8a97a8;background:transparent;border:none;")
            d.setWordWrap(True)
            cl.addWidget(d)
            body.addWidget(card)
        note = QLabel("角色页同时显示两类角色；「合并角色」仅影响所选组，不改变其他角色。")
        note.setStyleSheet("font-size:11px;color:#a0aab8;background:transparent;border:none;")
        note.setWordWrap(True)
        body.addWidget(note)

    # --------------------------------------------------------
    # ③ 📡 照片扫描
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
    # ④ 🖥️ 显示
    # --------------------------------------------------------
    def _section_display(self):
        _, body = self._section("🖥️ 显示")
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

        self._subtitle(body, "✨ Liquid Glass 微调（即时预览）")
        self._row(body, "玻璃透明度", self._slider(
            "ui.glass_opacity", 30, 90, scale=100, suffix="",
            on_change=self._glass_changed), note="越小越透明")
        self._row(body, "模糊强度", self._slider(
            "ui.glass_blur", 1, 60, on_change=self._glass_changed), note="阴影弥散")
        self._row(body, "卡片圆角", self._slider(
            "ui.corner_radius", 8, 28, suffix="px", on_change=self._glass_changed))
        self._row(body, "缩略图圆角", self._slider(
            "ui.thumb_radius", 4, 24, suffix="px", on_change=self._glass_changed))
        self._row(body, "阴影强度", self._slider(
            "ui.shadow_strength", 0, 80, on_change=self._glass_changed), note="0=无阴影")
        self._row(body, "动画速度", self._slider(
            "ui.animation_speed", 50, 200, scale=100, suffix="×", on_change=self._glass_changed))
        tip = QLabel("以上参数仅影响界面外观；关闭动画可提升低配机器流畅度。")
        tip.setStyleSheet("font-size:11px;color:#a0aab8;background:transparent;border:none;")
        tip.setWordWrap(True)
        body.addWidget(tip)

        # ── 🧭 底部导航（液态玻璃导航栏，修改即时生效）──
        self._subtitle(body, "🧭 底部导航")
        self._row(
            body, "导航样式",
            self._combo(
                [("Liquid Glass（底部悬浮）", "new"), ("经典（左侧栏）", "classic")],
                "ui.mode",
                on_change=lambda: self._theme_changed(),
            ),
            note="切换 UI 模式",
        )
        self._row(
            body, "显示文字",
            self._check("nav.show_text"),
            note="窄窗口自动只显示图标",
        )
        self._row(
            body, "液态动画",
            self._check("nav.animation"),
            note="关闭后选中无滑动动画",
        )
        self._row(
            body, "动画强度",
            self._slider("nav.animation_strength", 50, 200, scale=100, suffix="×"),
            note="弱 → 强",
        )
        self._row(
            body, "液态效果",
            self._combo(
                [("标准", "standard"), ("柔和", "soft"), ("明显", "vivid")],
                "nav.liquid_effect",
            ),
            note="胶囊拉伸幅度",
        )

        # ── ✨ Aurora 极光（可配置极光系统，修改即时生效）──
        self._subtitle(body, "✨ Aurora 极光")
        self._row(
            body, "启用 Aurora 极光",
            self._check("aurora.enabled"),
            note="关闭后角色卡片恢复普通玻璃",
        )
        self._row(
            body, "极光强度",
            self._slider("aurora.intensity", 0, 100, scale=100),
            note="弱 → 强",
        )
        self._row(
            body, "流动速度",
            self._slider("aurora.speed", 20, 300, scale=100),
            note="慢 → 快",
        )
        self._row(
            body, "颜色模式",
            self._combo(
                [("自动", "auto"), ("柔和", "soft"), ("彩色", "vivid")],
                "aurora.color_mode",
            ),
            note="自动=跟随主题",
        )

        # 高级 Aurora 设置（折叠）
        a_fold = QToolButton()
        a_fold.setText("▶ 高级 Aurora 设置")
        a_fold.setCheckable(True)
        a_fold.setStyleSheet(
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
        self._row(adv_layout, "光晕范围", self._slider("aurora.radius", 30, 120, scale=100),
                  note="扩散半径")
        self._row(adv_layout, "光晕模糊度", self._slider("aurora.blur", 0, 100, scale=100),
                  note="柔和程度")
        self._row(adv_layout, "鼠标跟随", self._slider("aurora.follow", 0, 100, scale=100),
                  note="0=固定不跟随")
        self._row(adv_layout, "跟随平滑度", self._slider("aurora.smoothing", 0, 100, scale=100),
                  note="越大惯性越强")
        self._row(adv_layout, "极光透明度", self._slider("aurora.opacity", 0, 100, scale=100),
                  note="整体透明")
        self._row(adv_layout, "Hover 浮起", self._slider("aurora.hover_lift", 0, 100, scale=100),
                  note="0=不浮起")
        self._row(adv_layout, "光源数量", self._combo(
            [("2 个", 2), ("3 个", 3), ("4 个", 4), ("5 个", 5)],
            "aurora.light_count",
        ))
        a_tip = QLabel("高级参数修改后立即生效；关闭 Aurora 时全部动画与绘制停止。")
        a_tip.setStyleSheet("font-size:11px;color:#a0aab8;background:transparent;border:none;")
        a_tip.setWordWrap(True)
        adv_layout.addWidget(a_tip)
        adv.hide()

        def _a_toggle():
            adv.setVisible(a_fold.isChecked())
            a_fold.setText("▼ 高级 Aurora 设置" if a_fold.isChecked() else "▶ 高级 Aurora 设置")
        a_fold.toggled.connect(_a_toggle)
        body.addWidget(a_fold)
        body.addWidget(adv)

        # ── 🧊 Liquid Glass 折射层（pyglass 物理折射，独立于 Aurora）──
        self._subtitle(body, "🧊 Liquid Glass")
        self._row(
            body, "启用 Liquid Glass",
            self._check("glass.enabled"),
            note="物理折射玻璃（Snell/色散/Frost）；关闭后恢复普通玻璃",
        )
        self._row(
            body, "玻璃厚度 / 折射强度",
            self._slider("glass.thickness", 0, 100, scale=100),
            note="越大边缘弯曲与色散越明显",
        )
        self._row(
            body, "玻璃模糊 / Frost",
            self._slider("glass.frost", 0, 100, scale=100),
            note="越大越磨砂（霜化）",
        )
        self._row(
            body, "玻璃透明度",
            self._slider("glass.opacity", 0, 100, scale=100),
            note="影响玻璃内部色调",
        )
        self._row(
            body, "鼠标跟随",
            self._check("glass.mouse_follow"),
            note="Hover 时实时折射跟随鼠标",
        )
        g_tip = QLabel("Liquid Glass 与 Aurora 独立开关：可只开玻璃、只开极光、或全开；全关时恢复普通卡片。")
        g_tip.setStyleSheet("font-size:11px;color:#a0aab8;background:transparent;border:none;")
        g_tip.setWordWrap(True)
        body.addWidget(g_tip)

    # --------------------------------------------------------
    # ⑤ 🗂 数据管理（子块）
    # --------------------------------------------------------
    def _section_storage(self, body):
        self._subtitle(body, "📂 存储")
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
    def _section_backup(self, body):
        self._subtitle(body, "💾 数据与备份")
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
    def _section_data(self, body):
        self._subtitle(body, "🔍 AI 数据")
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
    def _section_notify(self, body):
        self._subtitle(body, "🔔 通知")
        self._row(body, "分析完成通知", self._check("notifications.analysis_done"))
        self._row(body, "扫描完成通知", self._check("notifications.scan_done"))
        self._row(body, "新角色发现通知", self._check("notifications.new_character"))
        self._row(body, "错误通知", self._check("notifications.error"))

    # --------------------------------------------------------
    # ⑧ ⌨️ 快捷键
    # --------------------------------------------------------
    def _section_shortcuts(self, body):
        self._subtitle(body, "⌨️ 快捷键")
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
    def _section_advanced(self, body):
        self._subtitle(body, "🛠️ 高级参数（默认折叠）")
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
    # --------------------------------------------------------
    # ⑥ 🛠 版本与兼容模式
    # --------------------------------------------------------
    def _section_version(self):
        _, body = self._section("🛠 版本与兼容模式")
        self._row(
            body, "界面模式",
            self._combo(
                [("新版界面", "new"), ("经典版界面", "classic")],
                "ui.mode",
                on_change=lambda: self._theme_changed(
                    "界面模式已切换（视觉即时预览；完全生效建议重启）。"
                ),
            ),
            note="仅切换外观，不改变数据库 / AI / 角色数据",
        )
        safe = QLabel("⚠️ 兼容模式仅影响界面布局与视觉。任何涉及数据迁移、"
                      "重建角色或改库的切换都会先二次确认并保持安全保护。")
        safe.setStyleSheet("font-size:11px;color:#a0aab8;background:transparent;border:none;")
        safe.setWordWrap(True)
        body.addWidget(safe)
        self._subtitle(body, "ℹ️ 关于")
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

    # --------------------------------------------------------
    # ⑤ 🗂 数据管理（组合组）
    # --------------------------------------------------------
    def _section_data_manage(self):
        _, body = self._section("🗂 数据管理")
        self._section_storage(body)
        self._section_backup(body)
        self._section_data(body)
        self._section_notify(body)
        self._section_shortcuts(body)
        self._section_advanced(body)

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

    def _glass_changed(self):
        """玻璃微调参数修改 → 即时预览（设置面板 + 总览卡 + 已加载分组页）。"""
        if self.win:
            self.apply_glass()
            if hasattr(self.win, "_refresh_glass_panels"):
                self.win._refresh_glass_panels()

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
            toast.show(self, "数据库备份完成", kind="success")
        except Exception as e:
            self._backup_status.setText(f"❌ 备份失败：{e}")
            toast.show(self, f"备份失败：{e}", kind="warning")

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
