"""UI 组件库单元测试（离屏，不碰生产数据）。"""

import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QWidget, QHBoxLayout
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from config.settings_manager import settings as S


def settle(app, frames=40, dt=0.02):
    for _ in range(frames):
        t0 = time.time()
        app.processEvents()
        time.sleep(max(0.0, dt - (time.time() - t0)))


class ComponentsTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        S.set("aurora.enabled", True)

    # ── AnimatedToggle ──
    def test_toggle_animates(self):
        from ui.components.animated_toggle import AnimatedToggle
        t = AnimatedToggle(checked=False)
        t.show()
        settle(self.app, 5)
        self.assertAlmostEqual(t._pos, 0.0, delta=0.05)
        t.setChecked(True)
        self.assertTrue(t.isChecked())
        settle(self.app, 30)
        self.assertAlmostEqual(t._pos, 1.0, delta=0.05)
        # 点击切换
        t.click()
        self.assertFalse(t.isChecked())
        settle(self.app, 30)
        self.assertAlmostEqual(t._pos, 0.0, delta=0.05)

    def test_toggle_signal(self):
        from ui.components.animated_toggle import AnimatedToggle
        t = AnimatedToggle()
        got = []
        t.toggled.connect(lambda v: got.append(v))
        t.setChecked(True)
        self.assertTrue(got and got[-1] is True)

    # ── GlassButton ──
    def test_button_variants_and_click(self):
        from ui.components.glass_button import GlassButton
        for variant in ("normal", "accent", "danger"):
            b = GlassButton(f"B-{variant}", variant=variant)
            b.show()
            settle(self.app, 3)
            self.assertEqual(b.text(), f"B-{variant}")
        b = GlassButton("click")
        got = []
        b.clicked.connect(lambda: got.append(1))
        b.show()
        settle(self.app, 3)
        QTest.mouseClick(b, Qt.LeftButton)
        settle(self.app, 10)
        self.assertEqual(got, [1])

    def test_button_press_animation(self):
        from ui.components.glass_button import GlassButton
        b = GlassButton("press")
        b.show()
        settle(self.app, 3)
        QTest.mousePress(b, Qt.LeftButton)
        settle(self.app, 5)
        self.assertGreater(b._pressed_anim, 0.2)
        QTest.mouseRelease(b, Qt.LeftButton)
        settle(self.app, 30)
        self.assertAlmostEqual(b._pressed_anim, 0.0, delta=0.05)

    # ── AnimatedIcon ──
    def test_icon_states(self):
        from ui.components.animated_icon import AnimatedIcon
        ic = AnimatedIcon("overview", size=28)
        ic.show()
        settle(self.app, 3)
        self.assertAlmostEqual(ic._scale, 1.0, delta=0.05)
        ic.set_disabled(True)
        self.assertTrue(ic._disabled)
        ic.set_disabled(False)
        ic.set_icon("photo")
        self.assertEqual(ic._key, "photo")

    def test_icon_hover_scale(self):
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QEnterEvent
        from ui.components.animated_icon import AnimatedIcon
        ic = AnimatedIcon("person", size=28)
        ic.show()
        settle(self.app, 3)
        ic.enterEvent(QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
        settle(self.app, 30)
        self.assertGreater(ic._scale, 1.03)
        ic.leaveEvent(QEvent(QEvent.Leave))
        settle(self.app, 30)
        self.assertAlmostEqual(ic._scale, 1.0, delta=0.06)

    # ── GlassCard ──
    def test_glass_card_aurora_layer(self):
        from ui.components.glass_card import GlassCard
        from ui.aurora_card import AuroraGlassCard
        c1 = GlassCard(aurora=True)
        self.assertIsNotNone(c1._aurora_layer)
        self.assertIsInstance(c1._aurora_layer, AuroraGlassCard)
        c2 = GlassCard(aurora=False)
        self.assertIsNone(c2._aurora_layer)
        # Aurora 关闭 → 极光层零 timer
        c1.resize(200, 120)
        S.set("aurora.enabled", False)
        settle(self.app, 8)
        self.assertFalse(c1._aurora_layer._timer.isActive())
        c1.refresh_glass()  # 不应抛异常

    # ── GlassSlider ──
    def test_slider_value(self):
        from ui.components.glass_slider import GlassSlider
        s = GlassSlider()
        s.setRange(0, 100)
        s.setValue(62)
        self.assertEqual(s.value(), 62)
        got = []
        s.valueChanged.connect(lambda v: got.append(v))
        s.setValue(80)
        self.assertIn(80, got)

    # ── Toast ──
    def test_toast_stack(self):
        from ui.components.toast import toast
        host = QWidget()
        host.show()
        settle(self.app, 3)
        before = len(toast._stack)
        toast.show(host, "测试成功", kind="success")
        toast.show(host, "测试警告", kind="warning")
        settle(self.app, 10)
        self.assertEqual(len(toast._stack), before + 2)
        # 清理（避免残留）
        for w in list(toast._stack):
            w._cleanup()
        settle(self.app, 5)

    # ── 集成：设置中心使用新组件 ──
    def test_settings_center_uses_components(self):
        from ui.components.animated_toggle import AnimatedToggle
        from ui.components.glass_slider import GlassSlider
        from ui.components.glass_button import GlassButton
        from ui.main_window_v3 import MainWindow
        win = MainWindow()
        try:
            win._ui_ready = True
            win.show()
            settle(self.app, 8)
            win.content_stack.setCurrentIndex(8)
            win._refresh_settings_page()
            settle(self.app, 10)
            page = win.content_stack.currentWidget()
            toggles = page.findChildren(AnimatedToggle)
            sliders = page.findChildren(GlassSlider)
            buttons = page.findChildren(GlassButton)
            print(f"[集成] 设置中心: AnimatedToggle={len(toggles)} GlassSlider={len(sliders)} GlassButton={len(buttons)}")
            self.assertGreaterEqual(len(toggles), 4, "设置中心应有多个 AnimatedToggle")
            self.assertGreaterEqual(len(sliders), 6, "设置中心应有多个 GlassSlider")
            self.assertGreaterEqual(len(buttons), 2, "设置中心应有 GlassButton")
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main()
