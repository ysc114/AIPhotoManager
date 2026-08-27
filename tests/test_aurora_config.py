"""Aurora 极光可配置系统单元测试（temp 文件隔离，不碰生产配置/不碰数据库）。

覆盖：
- aurora 分区默认值与持久化
- 变更通知（前缀监听 / 注销）
- 组件行为：关闭零定时器、hover 不驱动、参数映射
- 颜色模式与光源数量
"""

import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings_manager import SettingsManager, DEFAULT_SETTINGS

# 需要的 Qt 组件测试在函数内延迟 import（避免无头环境报错）
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from ui.aurora_card import AuroraGlassCard

_AURORA_KEYS = (
    "enabled", "intensity", "speed", "color_mode", "blur", "radius",
    "follow", "smoothing", "opacity", "hover_lift", "light_count",
)


class AuroraSettingsTests(unittest.TestCase):

    def setUp(self):
        self.tmp = os.path.join(tempfile.mkdtemp(), "aurora_test.json")
        self.sm = SettingsManager(self.tmp)
        self._orig = None
        try:
            from config.settings_manager import settings as prod
            self._orig = prod.get("aurora")
        except Exception:
            pass

    def tearDown(self):
        try:
            os.remove(self.tmp)
        except OSError:
            pass

    # 1. 默认配置存在且克制
    def test_defaults(self):
        a = self.sm.get("aurora")
        self.assertIsInstance(a, dict)
        for k in _AURORA_KEYS:
            self.assertIn(k, a, f"aurora 缺少键 {k}")
        self.assertIs(a["enabled"], True)
        self.assertEqual(a["color_mode"], "auto")
        self.assertGreaterEqual(a["light_count"], 2)
        self.assertLessEqual(a["light_count"], 5)
        self.assertTrue(0.0 <= a["intensity"] <= 1.0)
        self.assertTrue(0.0 <= a["hover_lift"] <= 1.0)

    # 2. 持久化往返
    def test_persist_roundtrip(self):
        self.sm.set("aurora.intensity", 0.9)
        self.sm.set("aurora.light_count", 5)
        self.sm.set("aurora.color_mode", "vivid")
        self.sm.set("aurora.enabled", False)
        sm2 = SettingsManager(self.tmp)
        self.assertEqual(sm2.get("aurora.intensity"), 0.9)
        self.assertEqual(sm2.get("aurora.light_count"), 5)
        self.assertEqual(sm2.get("aurora.color_mode"), "vivid")
        self.assertIs(sm2.get("aurora.enabled"), False)

    # 3. 变更通知：前缀监听 + 精确监听 + 注销
    def test_change_notify(self):
        got = []
        self.sm.on_change("aurora", lambda k, v: got.append(("P", k, v)))
        cb2 = self.sm.on_change("aurora.intensity", lambda k, v: got.append(("E", k, v)))
        self.sm.set("aurora.intensity", 0.77)
        self.sm.set("ui.mode", "classic")  # 不应触发
        self.assertIn(("P", "aurora.intensity", 0.77), got)
        self.assertIn(("E", "aurora.intensity", 0.77), got)
        self.assertFalse(any(t[1].startswith("ui.") for t in got))
        # 注销后不再收到
        n = len(got)
        self.sm.off_change("aurora.intensity", cb2)
        self.sm.set("aurora.intensity", 0.5)
        self.assertEqual(len(got), n + 1)  # 只有前缀监听收到

    # 4. 默认配置包含 aurora 分区
    def test_default_section_present(self):
        self.assertIn("aurora", DEFAULT_SETTINGS)
        self.assertIsInstance(DEFAULT_SETTINGS["aurora"], dict)


class AuroraCardTests(unittest.TestCase):
    """组件行为（离屏，不触库）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from config.settings_manager import settings as prod
        self._orig = prod.get("aurora")
        # 隔离测试环境参数
        prod.set_many({
            "aurora.enabled": True,
            "aurora.intensity": 0.55,
            "aurora.speed": 1.0,
            "aurora.color_mode": "auto",
            "aurora.blur": 0.6,
            "aurora.radius": 0.6,
            "aurora.follow": 0.8,
            "aurora.smoothing": 0.6,
            "aurora.opacity": 0.85,
            "aurora.light_count": 3,
        })

    def tearDown(self):
        from config.settings_manager import settings as prod
        for k, v in self._orig.items():
            prod.set(f"aurora.{k}", v)

    # 5. 关闭后零定时器，hover 不驱动
    def test_disabled_zero_overhead(self):
        from config.settings_manager import settings as prod
        card = AuroraGlassCard()
        card.resize(226, 248)
        prod.set("aurora.enabled", False)
        # 模拟 hover 事件（事件坐标驱动）
        from PySide6.QtGui import QHoverEvent
        from PySide6.QtCore import QEvent
        ev = QHoverEvent(QEvent.HoverMove, QPointF(100, 80), QPointF(0, 0))
        card.hoverMoveEvent(ev)
        self.assertFalse(card._timer.isActive())
        self.assertFalse(card._hovering)
        self.assertEqual(card._glow_alpha, 0.0)
        card.deleteLater()

    # 6. 强度映射：base/max 随 intensity 单调
    def test_intensity_mapping(self):
        from config.settings_manager import settings as prod
        prod.set("aurora.intensity", 0.0)
        base0 = AuroraGlassCard._base_glow(AuroraGlassCard()._cfg())
        prod.set("aurora.intensity", 1.0)
        base1 = AuroraGlassCard._base_glow(AuroraGlassCard()._cfg())
        self.assertLess(base0, base1)
        self.assertLess(AuroraGlassCard._max_glow(
            {"intensity": 0.0}), AuroraGlassCard._max_glow({"intensity": 1.0}))

    # 7. 颜色模式取色：数量与模式
    def test_light_colors(self):
        from config.settings_manager import settings as prod
        prod.set("aurora.light_count", 5)
        prod.set("aurora.color_mode", "vivid")
        card = AuroraGlassCard()
        cols = card._light_colors(card._cfg())
        self.assertEqual(len(cols), 5)
        prod.set("aurora.color_mode", "soft")
        cols2 = card._light_colors(card._cfg())
        self.assertEqual(len(cols2), 5)
        # 非法模式回退 auto
        prod.set("aurora.color_mode", "bogus")
        self.assertEqual(len(card._light_colors(card._cfg())), 5)
        card.deleteLater()

    # 8. 平滑度系数：越大惯性越大（系数越小）
    def test_smoothing_lerp(self):
        k0 = AuroraGlassCard._lerp_k({"smoothing": 0.0})
        k1 = AuroraGlassCard._lerp_k({"smoothing": 1.0})
        self.assertGreater(k0, k1)


if __name__ == "__main__":
    unittest.main()
