"""
Aurora Glass Card（可复用极光玻璃组件）

小米 HyperOS「极光」+ Apple Liquid Glass 视觉，做成独立可配置系统：

- 半透明玻璃卡片底 + 常驻柔和彩色极光（进页面即见）
- hover 时极光跟随鼠标流动并增强（带惯性延迟，快速移动不瞬移）
- 鼠标停留时极光在鼠标附近缓慢内部流动（液体感）
- 离开后极光平滑回落至基础亮度（余韵淡出）
- **Liquid Glass 折射层（pyglass vendor 引擎）**：
  玻璃底升级为物理折射（Snell 透镜弯曲 / 色散 / Fresnel rim / frost 霜化）；
  hover 时实时折射跟随鼠标；静止时使用缓存帧；
  pyglass 不可用时自动 fallback 到普通玻璃绘制（不影响任何功能）
- 独立开关（互相不依赖）：
  aurora.enabled（彩色极光）/ glass.enabled（Liquid Glass 折射）
  两者全关 → 普通玻璃卡；关 Aurora → 纯 Liquid Glass；关 Glass → 现有极光玻璃
- 全部参数通过 SettingsManager 实时读取，修改即时生效（aurora.* / glass.* / ui.*）
- 关闭后不再绘制对应层、不启动任何定时器，零动态开销

复用方式与 QFrame 完全一致：
    card = AuroraGlassCard()
    card.setFixedSize(w, h)
    # 之后正常 addWidget 子控件 / 挂事件
"""

import math

from PySide6.QtCore import Qt, QPointF, QRectF, QSize, QTimer, QPoint
from PySide6.QtGui import (
    QPainter,
    QPainterPath,
    QPixmap,
    QRadialGradient,
    QLinearGradient,
    QColor,
    QPen,
)
from PySide6.QtWidgets import QFrame

from config.settings_manager import settings as S

# ── 颜色池（蓝 / 紫 / 粉 / 青），按颜色模式取用 ─────────────
_COLOR_POOLS = {
    "soft":  [(128, 188, 255), (180, 155, 255), (255, 170, 210), (135, 208, 238)],
    "auto":  [(70, 160, 255), (142, 105, 255), (255, 122, 178), (0, 196, 226)],
    "vivid": [(28, 108, 255), (176, 38, 255), (255, 38, 148), (0, 176, 255)],
}

# 光源散布偏移（最多 5 个；不足时按需取用）
_LIGHT_OFFSETS = [
    (-0.18, -0.12, 0.62),
    (0.16, -0.04, 0.56),
    (0.02, 0.16, 0.66),
    (0.22, 0.15, 0.50),
    (-0.17, 0.20, 0.52),
]


class AuroraGlassCard(QFrame):
    """极光玻璃卡片：pyglass 折射玻璃 + 常驻极光 + hover 跟随增强。

    所有视觉参数每次绘制时从 settings 实时读取：
    - 修改参数 → 触发卡片 update() 即见新效果（无需重建）
    - aurora.enabled=False → 零极光绘制 / 零定时器
    - glass.enabled=False 或 pyglass 不可用 → 玻璃底自动回退普通绘制
    """

    _SMOOTH_IN = 0.22      # 光晕淡入基准速度
    _SMOOTH_OUT = 0.12     # 光晕回落基准速度
    _REST_POS = (0.50, 0.42)  # 静止极光默认位置（宽高比例，中心偏上）

    def __init__(self, parent=None, refract=True, shadow=True):
        super().__init__(parent)
        self._refract_enabled = bool(refract)   # False=纯装饰层（Dock/面板背景），不启用物理折射
        self._paint_shadow = bool(shadow)       # True=自绘轻量投影（零离屏图层）
        self.setAttribute(Qt.WA_Hover, True)
        self.setMouseTracking(True)

        self._hovering = False
        self._target = QPointF(0.0, 0.0)    # 鼠标目标位置（本地坐标）
        self._glow_pos = QPointF(0.0, 0.0)  # 平滑后的光晕位置
        self._drift_t = 0.0                 # 内部流动相位
        self._glow_alpha = 0.0              # 当前光晕强度 0~1

        # 静态帧缓存（非 hover 时整卡渲染结果直接 blit）
        self._cache = None
        self._cache_key = None

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

        # ── Liquid Glass 折射层（pyglass vendor，懒初始化）──
        self._pg = None            # vendor pyglass 模块（None=不可用）
        self._pg_available = None  # 探测结果缓存
        self._renderer = None      # GlassRenderer
        self._backdrop = None      # WidgetBackdrop（host=window, exclude=self）
        self._refracted = None     # 缓存折射 QPixmap
        self._glass_need_refresh = False  # 背景需重新捕获

        # 参数变更自监听：修改极光/玻璃参数 → 立即刷新；销毁时注销（防泄漏）
        self._settings_cb = S.on_change("aurora", self._on_aurora_cfg)
        self._glass_cb = S.on_change("glass", self._on_glass_cfg)
        self._ui_cb = S.on_change("ui", self._on_ui_changed)
        self.destroyed.connect(self._on_destroyed)

        if S.get("aurora.enabled", True):
            self._glow_alpha = self._base_glow(self._cfg())

    # --------------------------------------------------------
    # 配置
    # --------------------------------------------------------
    def _cfg(self):
        """一次性读取全部极光参数（paint/tick 时调用，开销可忽略）。"""
        return dict(
            enabled=bool(S.get("aurora.enabled", True)) and not self._perf_mode(),
            intensity=float(S.get("aurora.intensity", 0.55)),
            speed=float(S.get("aurora.speed", 1.0)),
            blur=float(S.get("aurora.blur", 0.6)),
            radius=float(S.get("aurora.radius", 0.6)),
            follow=float(S.get("aurora.follow", 0.8)),
            smoothing=float(S.get("aurora.smoothing", 0.6)),
            opacity=float(S.get("aurora.opacity", 0.85)),
            color_mode=str(S.get("aurora.color_mode", "auto")),
            light_count=int(S.get("aurora.light_count", 3)),
            glass_opacity=float(S.get("ui.glass_opacity", 0.55)),
            corner_radius=int(S.get("ui.corner_radius", 18)),
        )

    def _glass_cfg(self):
        """Liquid Glass 折射层参数。"""
        return dict(
            enabled=bool(S.get("glass.enabled", True)),
            thickness=float(S.get("glass.thickness", 0.35)),
            frost=float(S.get("glass.frost", 0.25)),
            opacity=float(S.get("glass.opacity", 0.35)),
            mouse_follow=bool(S.get("glass.mouse_follow", True)),
        )

    @staticmethod
    def _base_glow(cfg):
        """基础（非 hover）极光强度：随 intensity 单调变化，0 时仍留极淡。"""
        return 0.10 + 0.35 * cfg["intensity"]

    @staticmethod
    def _max_glow(cfg):
        """hover 峰值强度。"""
        return 0.45 + 0.45 * cfg["intensity"]

    @staticmethod
    def _lerp_k(cfg):
        """位置平滑系数：smoothing 越大惯性越大（越滑）。"""
        return 0.32 / (1.0 + 4.0 * cfg["smoothing"])

    def _light_colors(self, cfg):
        """按颜色模式 + 光源数量取色。"""
        pool = _COLOR_POOLS.get(cfg["color_mode"], _COLOR_POOLS["auto"])
        n = max(2, min(5, cfg["light_count"]))
        return [pool[i % len(pool)] for i in range(n)]

    # --------------------------------------------------------
    # Liquid Glass（pyglass 折射层）
    # --------------------------------------------------------
    def _init_pg(self):
        """探测 vendor pyglass（只做一次）；不可用返回 False（自动 fallback）。"""
        if self._pg_available is None:
            try:
                import ui.vendor.pyglass as pg
                self._pg = pg
                self._pg_available = True
            except Exception as e:
                print(f"[LiquidGlass] pyglass 不可用，回退普通玻璃: {e}")
                self._pg = None
                self._pg_available = False
        return self._pg_available

    def _glass_active(self):
        """折射层是否应启用（装饰层 refract=False 时不启用；性能模式禁用）。"""
        if self._perf_mode():
            return False
        if not self._refract_enabled:
            return False
        if not self._init_pg():
            return False
        if not self._glass_cfg()["enabled"]:
            return False
        return self.width() >= 24 and self.height() >= 24

    def _ensure_renderer(self):
        if self._renderer is None:
            self._renderer = self._pg.GlassRenderer(
                self._pg.GlassMaterial(
                    thickness=self._glass_cfg()["thickness"],
                    frost=self._glass_cfg()["frost"],
                ),
                self.width(), self.height(),
                max(4, int(S.get("ui.corner_radius", 18))),
            )
        else:
            cfg = self._glass_cfg()
            self._renderer.set_material(self._pg.GlassMaterial(
                thickness=cfg["thickness"], frost=cfg["frost"]))
            self._renderer.set_geometry(
                self.width(), self.height(),
                max(4, int(S.get("ui.corner_radius", 18))))
        return True

    def _ensure_backdrop(self):
        """共享捕获：host=window（refract 时隐藏自身避免自我折射）。"""
        if self._backdrop is None:
            host = self.window() if (self.window() and self.window() is not self) else self
            self._backdrop = self._pg.WidgetBackdrop(host, exclude=self)
            self._backdrop.changed.connect(self._on_backdrop_changed)
        return self._backdrop

    def _on_backdrop_changed(self):
        self.update()

    def _refract_frame(self, fast=False):
        """捕获/切片/折射当前背景 → 缓存 pixmap。失败返回 False（fallback）。"""
        try:
            if not self._ensure_renderer():
                return False
            self._ensure_backdrop()
            bd = self._backdrop.array()
            if bd is None or self._glass_need_refresh:
                self._backdrop.refresh()   # 内部隐藏 self 再 grab（避免自我折射）
                self._glass_need_refresh = False
                bd = self._backdrop.array()
                if bd is None:
                    return False
            host = self._backdrop._host
            origin = self.mapTo(host, QPoint(0, 0))
            self._refracted = self._renderer.refract(
                bd, origin, self._backdrop.dpr(), fast=fast)
            return self._refracted is not None
        except Exception:
            self._refracted = None
            return False

    # --------------------------------------------------------
    # 事件
    # --------------------------------------------------------
    def _maybe_start(self):
        if S.get("aurora.enabled", True):
            self._timer.start()
            return True
        return False

    def enterEvent(self, event):
        a_on = S.get("aurora.enabled", True)
        if a_on:
            self._hovering = True
            self._timer.start()
        # Liquid Glass：hover 进入刷新背景并折射（mouse_follow 时持续驱动）
        if self._glass_active():
            self._hovering = True
            self._glass_need_refresh = True
            if self._glass_cfg()["mouse_follow"]:
                if not self._timer.isActive():
                    self._timer.start()
            else:
                self._refract_frame(fast=True)   # 一次性折射即可
                self.update()
        super().enterEvent(event)

    def hoverMoveEvent(self, event):
        # 注：PySide6 未暴露 QWidget.hoverMoveEvent，基类实现为空，无需 super() 调用
        a_on = S.get("aurora.enabled", True)
        g_on = self._glass_active()
        if not a_on and not g_on:
            self._hovering = False
            return
        self._hovering = True
        # 事件本地坐标优先；异常（0,0 或越界，离屏/个别环境下会出现）
        # 时退回全局光标映射兜底
        p = QPointF(event.pos())
        if (p.x() <= 0 and p.y() <= 0) or p.x() >= self.width() or p.y() >= self.height():
            from PySide6.QtGui import QCursor
            g = self.mapFromGlobal(QCursor.pos())
            if 0 <= g.x() < self.width() and 0 <= g.y() < self.height():
                p = QPointF(g)
        self._target = p
        if a_on or (g_on and self._glass_cfg()["mouse_follow"]):
            self._timer.start()

    def mouseMoveEvent(self, event):
        # 兜底：个别环境下 hover 事件未送达时，mouse move 也能驱动极光
        a_on = S.get("aurora.enabled", True)
        g_on = self._glass_active()
        if not a_on and not g_on:
            self._hovering = False
            return super().mouseMoveEvent(event)
        self._hovering = True
        self._target = QPointF(event.pos())
        if a_on or (g_on and self._glass_cfg()["mouse_follow"]):
            self._timer.start()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hovering = False
        super().leaveEvent(event)

    def resizeEvent(self, event):
        # 尺寸变化 → 渲染器几何失效 + 折射缓存作废
        self._renderer = None
        self._refracted = None
        super().resizeEvent(event)

    # --------------------------------------------------------
    # 设置变更联动
    # --------------------------------------------------------
    def _on_aurora_cfg(self, key, value):
        """极光参数变化：即时刷新；关闭时停表归零（零动态开销）。"""
        if key == "aurora.enabled":
            if value:
                self._hovering = False
                self._glow_pos = QPointF(0.0, 0.0)
                self._glow_alpha = self._base_glow(self._cfg())
            else:
                self._hovering = False
                self._timer.stop()
                self._glow_alpha = 0.0
        elif key in ("aurora.intensity", "aurora.opacity", "aurora.color_mode",
                     "aurora.blur", "aurora.radius", "aurora.light_count",
                     "aurora.speed", "aurora.follow", "aurora.smoothing"):
            if not self._hovering and not self._timer.isActive():
                # 静止态：直接对齐新的基础强度
                self._glow_alpha = self._base_glow(self._cfg())
        self.update()

    def _on_glass_cfg(self, key, value):
        """Liquid Glass 参数变化：重建材质 / 作废折射缓存 / 即时刷新。"""
        if key == "glass.enabled":
            if value:
                self._glass_need_refresh = True
            else:
                self._refracted = None
                if not S.get("aurora.enabled", True):
                    self._timer.stop()
        elif key in ("glass.thickness", "glass.frost", "glass.opacity"):
            self._renderer = None
            self._refracted = None
        elif key == "glass.mouse_follow":
            pass
        self.update()

    def _on_destroyed(self):
        S.off_change("aurora", self._settings_cb)
        S.off_change("glass", self._glass_cb)
        S.off_change("ui", self._ui_cb)

    def _on_ui_changed(self, key, value):
        """ui.* 变化（如 perf_mode 一键开关）→ 作废静态缓存并重绘。"""
        if key == "ui.perf_mode":
            self._cache_key = None
            self.update()

    # --------------------------------------------------------
    # 动画驱动
    # --------------------------------------------------------
    def _tick(self):
        """每帧：位置 lerp + 强度 lerp + 内部流动；回落完成且静止后停表。

        aurora 关闭时：若 Liquid Glass 开启且 hover 需要实时折射
        （mouse_follow），timer 继续驱动折射帧；否则停表零开销。
        """
        cfg = self._cfg()
        if not cfg["enabled"]:
            # 极光关闭：仅当 Liquid Glass 需要实时折射时保持 timer
            if self._hovering and self._glass_active() and self._glass_cfg()["mouse_follow"]:
                self._refract_frame(fast=True)
                self.update()
                return
            self._timer.stop()
            self._glow_alpha = 0.0
            self.update()
            return

        k = self._lerp_k(cfg)
        cx, cy = self.width() * self._REST_POS[0], self.height() * self._REST_POS[1]

        if self._hovering:
            # 目标 = 鼠标位置 × follow + 中心 × (1-follow)
            f = max(0.0, min(1.0, cfg["follow"]))
            target_pos = QPointF(
                self._target.x() * f + cx * (1 - f),
                self._target.y() * f + cy * (1 - f),
            )
            # 停留时缓慢内部流动（液体感），速度受 speed 控制
            self._drift_t += cfg["speed"] * 0.025
            target_pos += QPointF(
                math.sin(self._drift_t) * 7.0 * cfg["speed"],
                math.cos(self._drift_t * 1.3) * 6.0 * cfg["speed"],
            )
            target_alpha = self._max_glow(cfg)
            alpha_speed = self._SMOOTH_IN * max(0.2, cfg["speed"])
        else:
            target_pos = QPointF(cx, cy)
            target_alpha = self._base_glow(cfg)
            alpha_speed = self._SMOOTH_OUT * max(0.2, cfg["speed"])

        self._glow_pos += (target_pos - self._glow_pos) * k
        self._glow_alpha += (target_alpha - self._glow_alpha) * alpha_speed

        # Liquid Glass：hover 且鼠标跟随 → 每帧实时折射（fast 切片）
        if self._hovering and self._glass_active():
            gcfg = self._glass_cfg()
            if gcfg["mouse_follow"] or self._refracted is None:
                self._refract_frame(fast=True)

        moving = (
            abs(self._glow_pos.x() - target_pos.x()) > 0.5
            or abs(self._glow_pos.y() - target_pos.y()) > 0.5
        )
        rested = abs(self._glow_alpha - target_alpha) < 0.008
        if not self._hovering and not moving and rested:
            self._glow_alpha = target_alpha
            self._timer.stop()
            self.update()
            return
        self.update()

    # --------------------------------------------------------
    # 性能模式：一键忽略极光/折射/阴影等动态渲染（默认不开启）
    # --------------------------------------------------------
    def _perf_mode(self):
        """ui.perf_mode=True → 跳过极光/折射/自绘阴影（普通玻璃静态卡）。"""
        return bool(S.get("ui.perf_mode", False))

    # --------------------------------------------------------
    # 绘制
    # --------------------------------------------------------
    def _static_key(self):
        """静态帧缓存键：尺寸 + 全部视觉参数（任一变化自动重建）。"""
        cfg = self._cfg()
        return (
            self.width(), self.height(), cfg["corner_radius"],
            bool(cfg["enabled"]), cfg["intensity"], cfg["opacity"],
            cfg["blur"], cfg["radius"], cfg["color_mode"], cfg["light_count"],
            cfg["glass_opacity"],
            float(S.get("ui.shadow_strength", 40)),
            float(S.get("ui.glass_blur", 30)) / 30.0,
            self._perf_mode(),
        )

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect()
        w, h = rect.width(), rect.height()
        if w <= 0 or h <= 0:
            p.end()
            return

        # ── 静态帧缓存：非 hover/非动画时整卡渲染结果缓存为 pixmap，
        # 滚动/重绘/筛选重建直接 blit（226 张卡滚动几乎零绘制成本）──
        if not self._hovering and not self._timer.isActive():
            key = self._static_key()
            if (self._cache_key == key and self._cache is not None
                    and self._cache.size() == QSize(w, h)):
                p.drawPixmap(0, 0, self._cache)
                p.end()
                return
            pm = QPixmap(w, h)
            pm.fill(Qt.transparent)
            pp = QPainter(pm)
            pp.setRenderHint(QPainter.Antialiasing, True)
            self._paint_content(pp, QRectF(rect), w, h)
            pp.end()
            self._cache = pm
            self._cache_key = key
            p.drawPixmap(0, 0, pm)
            p.end()
            return

        self._paint_content(p, QRectF(rect), w, h)
        p.end()

    def _paint_content(self, p, rect, w, h):
        """卡片完整绘制（玻璃底 + 自绘投影 + 极光 + 高光 + 描边）。"""
        cfg = self._cfg()
        radius = max(4, cfg["corner_radius"])
        perf = self._perf_mode()

        # ── 自绘柔和投影（静态；无 QGraphicsDropShadowEffect 离屏图层）──
        # 只随重绘绘制 3 层描边近似柔影，226 张卡也不产生离屏合成开销；
        # ui.shadow_strength=0 或性能模式 → 零投影。
        s_strength = max(0.0, float(S.get("ui.shadow_strength", 40)) / 40.0)
        if self._paint_shadow and not perf and s_strength > 0.02:
            s_blur = max(0.5, float(S.get("ui.glass_blur", 30)) / 30.0)
            dy = 4.0
            for width, alpha, grow in (
                (max(2.5, 9.0 * s_blur), int(9 * s_strength), 7.0),
                (max(1.8, 5.0 * s_blur), int(17 * s_strength), 4.0),
                (1.1, int(30 * s_strength), 1.6),
            ):
                rr = QRectF(rect).adjusted(
                    -grow * 0.5, -grow * 0.5 + dy, grow * 0.5, grow * 0.5 + dy
                )
                p.setPen(QPen(QColor(30, 60, 110, alpha), width))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rr, radius + grow * 0.5, radius + grow * 0.5)

        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), radius, radius)
        p.setClipPath(path)

        # ── Liquid Glass 折射层（pyglass；失败/关闭自动回退玻璃底）──
        # 仅 hover 时同步折射一次（避免列表页批量卡片同时 grab 主窗口导致
        # 原生崩溃/卡顿）；静止卡片显示纯玻璃+Aurora，hover 后实时折射。
        glass_drawn = False
        if self._glass_active() and not perf:
            if self._refracted is None and self._hovering:
                self._refract_frame(fast=True)
            if self._refracted is not None:
                gcfg = self._glass_cfg()
                style = self._pg.GlassStyle(
                    tint_top=int(18 * gcfg["opacity"] * 255),
                    tint_bottom=int(6 * gcfg["opacity"] * 255),
                )
                self._pg.paint_glass(
                    p, QRectF(rect), radius, self._refracted, style=style)
                glass_drawn = True

        if not glass_drawn:
            # 普通玻璃底（性能模式：稳定不透明底，纯静态、零渐变叠加）
            ga = cfg["glass_opacity"]
            if perf:
                p.fillPath(path, QColor(255, 255, 255, 225))
            else:
                base_alpha = ga + (0.10 if self._hovering else (0.05 if cfg["enabled"] else 0.0))
                p.fillPath(path, QColor(255, 255, 255, int(min(base_alpha, 0.94) * 255)))

        # ── 极光光晕（N 个径向渐变，SourceOver 彩色叠加）──
        # 浅色玻璃底上用 Screen 合成会趋近纯白导致不可见，故用普通半透明叠加。
        glow = 0.0
        glow_pos = QPointF(w * self._REST_POS[0], h * self._REST_POS[1])
        if cfg["enabled"] and not perf:
            if self._timer.isActive() or self._hovering:
                glow = self._glow_alpha
                glow_pos = QPointF(self._glow_pos)
            else:
                glow = self._base_glow(cfg)

        if glow > 0.01:
            op = max(0.0, min(1.0, cfg["opacity"]))
            b = max(0.0, min(1.0, cfg["blur"]))
            rscale = max(0.3, min(1.2, cfg["radius"]))
            peak_a = int(glow * 185 * op)
            mid_a = int(glow * 82 * op)
            colors = self._light_colors(cfg)
            offsets = _LIGHT_OFFSETS[: len(colors)]
            for (dx, dy, rr), col in zip(offsets, colors):
                center = QPointF(
                    glow_pos.x() + dx * w * rscale,
                    glow_pos.y() + dy * h * rscale,
                )
                r = max(1.0, rr * w * rscale)
                grad = QRadialGradient(center, r)
                grad.setColorAt(0.0, QColor(col[0], col[1], col[2], peak_a))
                # 模糊度：越大中段越外扩（柔和弥散），越小越收敛
                grad.setColorAt(0.30 + 0.30 * b, QColor(col[0], col[1], col[2], mid_a))
                grad.setColorAt(0.62 + 0.26 * b, QColor(col[0], col[1], col[2], int(mid_a * 0.42)))
                grad.setColorAt(1.0, QColor(col[0], col[1], col[2], 0))
                p.fillPath(path, grad)

        # ── 顶部玻璃高光 ──
        if not perf:
            hi = QLinearGradient(0, 0, 0, h * 0.55)
            hi.setColorAt(0.0, QColor(255, 255, 255, int(64 + 18 * glow)))
            hi.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.fillPath(path, hi)

        # ── 边缘高光描边（折射层已自带 rim 时跳过）──
        if not glass_drawn:
            border = QColor(255, 255, 255, 210 if self._hovering else (110 if not perf else 60))
            p.setPen(QPen(border, 1.0))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
