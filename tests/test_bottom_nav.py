"""底部 Liquid Glass 导航栏单元测试（离屏，temp 隔离配置不落生产）。"""

import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint, QVariantAnimation
from PySide6.QtTest import QTest

from config.settings_manager import settings as S
from ui.bottom_nav import BottomGlassNav


def settle(app, frames=40, dt=0.02):
    for _ in range(frames):
        t0 = time.time()
        app.processEvents()
        time.sleep(max(0.0, dt - (time.time() - t0)))


class BottomNavTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.orig_nav = S.get("nav")
        self.orig_mode = S.get("ui.mode")
        S.set_many({
            "nav.show_text": True,
            "nav.animation": True,
            "nav.animation_strength": 1.0,
            "nav.liquid_effect": "standard",
        })

    def tearDown(self):
        for k, v in self.orig_nav.items():
            S.set(f"nav.{k}", v)
        S.set("ui.mode", self.orig_mode)

    def _nav(self):
        nav = BottomGlassNav()
        nav.setFixedSize(1008, 70)
        nav.show()
        settle(self.app, 5)
        return nav

    # 1. 导航项与初始胶囊
    def test_entries_and_capsule(self):
        nav = self._nav()
        self.assertEqual(nav._n, 9)
        keys = [k for k, _ in nav._entries]
        self.assertEqual(keys[0], "overview")
        self.assertIn("ai_pick", keys)
        self.assertIn("settings", keys)
        r = nav._capsule_rect(0)
        self.assertAlmostEqual(nav._capsule[0], r.x(), delta=0.5)

    # 2. 液态切换：动画后胶囊精确到位
    def test_capsule_lands_on_target(self):
        nav = self._nav()
        nav.set_current(3)
        settle(self.app, 60)
        self.assertEqual(nav._current, 3)
        expect = nav._capsule_rect(3)
        self.assertLess(abs(nav._capsule[0] - expect.x()), 3)
        self.assertLess(abs(nav._capsule[1] - expect.width()), 3)

    # 3. 动画关闭：直接跳转
    def test_animation_off(self):
        S.set("nav.animation", False)
        nav = self._nav()
        nav.set_current(6)
        settle(self.app, 5)
        self.assertEqual(nav._current, 6)
        expect = nav._capsule_rect(6)
        self.assertLess(abs(nav._capsule[0] - expect.x()), 3)

    # 4. 液态拉伸：切换途中胶囊宽度 > 目标（standard 档）
    def test_liquid_stretch_midway(self):
        nav = self._nav()
        nav.set_current(1)
        settle(self.app, 4)
        mid_w = nav._capsule[1]
        target_w = nav._capsule_rect(1).width()
        self.assertGreaterEqual(mid_w, target_w - 0.5)
        settle(self.app, 60)

    # 5. 响应式：窄宽度自动隐藏文字
    def test_responsive_text(self):
        nav = BottomGlassNav()
        nav.setFixedSize(400, 70)   # 9 项 → 每项 44 < 78 → 隐藏文字
        nav.show()
        settle(self.app, 3)
        self.assertFalse(nav._show_text())
        nav.setFixedSize(1200, 70)
        settle(self.app, 3)
        self.assertTrue(nav._show_text())

    # 6. Aurora 关闭 → 极光层零 timer
    def test_aurora_off_zero_timer(self):
        nav = self._nav()
        S.set("aurora.enabled", False)
        settle(self.app, 8)
        active = [a for a in nav.findChildren(type(nav._aurora)) if a._timer.isActive()]
        self.assertEqual(active, [])
        S.set("aurora.enabled", True)

    # 7. 点击信号
    def test_click_signal(self):
        nav = self._nav()
        got = []
        nav.page_changed.connect(lambda i: got.append(i))
        QTest.mouseClick(nav, Qt.LeftButton, pos=QPoint(int(nav.width() / 9 * 4.5), 35))
        self.assertEqual(got, [4])


if __name__ == "__main__":
    unittest.main()
