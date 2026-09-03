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
        self.assertEqual(nav._n, 10)
        keys = [k for k, _ in nav._entries]
        self.assertEqual(keys[0], "overview")
        self.assertIn("ai_pick", keys)
        self.assertIn("duplicates", keys)
        self.assertIn("settings", keys)
        r = nav._capsule_rect(0)
        self.assertAlmostEqual(nav._capsule[0], r.x(), delta=0.5)

    # 1b. 入口顺序与页面栈一一对应（设置=8 / 重复照片=9，与 content_stack 一致）
    def test_entries_order_matches_content_stack(self):
        nav = self._nav()
        keys = [k for k, _ in nav._entries]
        self.assertEqual(keys[8:], ["settings", "duplicates"],
                         "底部导航末尾顺序必须与内容栈一致：设置(8)、重复照片(9)")

    # 1c. 每个导航入口都有对应自绘图形（无空图标）
    def test_all_entries_have_icons(self):
        from ui.components.icons import _PATH_BUILDERS, draw_icon
        from ui.bottom_nav import DEFAULT_ENTRIES
        for key, name in DEFAULT_ENTRIES:
            self.assertIn(key, _PATH_BUILDERS,
                          f"导航项「{name}」({key}) 缺少自绘图标")
        # 新图标可绘制（不抛异常）
        nav = self._nav()
        from PySide6.QtGui import QPixmap, QPainter, QColor
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        draw_icon(p, pm.rect(), "duplicates", QColor(80, 80, 80))
        p.end()
        self.assertFalse(pm.isNull())

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
        QTest.mouseClick(nav, Qt.LeftButton, pos=QPoint(int(nav.width() / 10 * 4.5), 35))
        self.assertEqual(got, [4])


if __name__ == "__main__":
    unittest.main()
