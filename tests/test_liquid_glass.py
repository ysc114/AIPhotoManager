"""Liquid Glass 折射层（pyglass vendor 集成）单元测试（离屏）。"""

import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QWidget, QGridLayout
from PySide6.QtCore import QPointF
from PySide6.QtGui import QEnterEvent

from config.settings_manager import settings as S


def settle(app, frames=40, dt=0.02):
    for _ in range(frames):
        t0 = time.time()
        app.processEvents()
        time.sleep(max(0.0, dt - (time.time() - t0)))


class VendorPyGlassTests(unittest.TestCase):
    """vendor pyglass：PySide6 适配后完整可用。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_import_and_render(self):
        import ui.vendor.pyglass as pg
        self.assertEqual(pg.__version__, "0.3.0")
        m = pg.GlassMaterial(thickness=0.5, frost=0.3)
        r = pg.GlassRenderer(m, 200, 140, 18)
        import numpy as np
        bd = np.zeros((300, 400, 4), dtype=np.uint8)
        bd[..., 3] = 255
        pm = r.refract(bd, QPointF(0, 0).toPoint(), 1.0, fast=True)
        self.assertIsNotNone(pm)
        self.assertEqual((pm.width(), pm.height()), (200, 140))

    def test_qimage_roundtrip(self):
        """PySide6 memoryview 兼容：QImage → ndarray → QImage。"""
        from ui.vendor.pyglass.refract import qimage_to_array, array_to_qimage
        from PySide6.QtGui import QImage
        import numpy as np
        img = QImage(80, 60, QImage.Format.Format_RGBA8888)
        img.fill(0x11223344)
        arr = qimage_to_array(img)
        self.assertEqual(arr.shape, (60, 80, 4))
        img2 = array_to_qimage(arr)
        self.assertEqual((img2.width(), img2.height()), (80, 60))


class AuroraGlassCardLiquidGlassTests(unittest.TestCase):
    """AuroraGlassCard 折射层：开关独立性 / hover / fallback。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.orig_a = S.get("aurora")
        self.orig_g = S.get("glass")
        S.set("glass.enabled", True)
        S.set("glass.mouse_follow", True)
        S.set("aurora.enabled", True)
        from ui.aurora_card import AuroraGlassCard
        self.Card = AuroraGlassCard
        self.host = QWidget()
        self.host.resize(600, 400)
        self.host.setStyleSheet("background:#eef1f7;")
        grid = QGridLayout(self.host)
        grid.setSpacing(10)
        self.cards = []
        for i in range(4):
            c = self.Card()
            c.setFixedSize(170, 150)
            grid.addWidget(c, i // 2, i % 2)
            self.cards.append(c)
        self.host.show()
        settle(self.app, 15)

    def tearDown(self):
        for k, v in self.orig_a.items():
            S.set(f"aurora.{k}", v)
        for k, v in self.orig_g.items():
            S.set(f"glass.{k}", v)
        self.host.close()

    def test_glass_active_by_default(self):
        c = self.cards[0]
        self.assertTrue(c._glass_active())

    def test_hover_generates_refraction(self):
        c = self.cards[0]
        c._glass_need_refresh = True
        c.enterEvent(QEnterEvent(QPointF(30, 30), QPointF(30, 30), QPointF(30, 30)))
        settle(self.app, 30)
        self.assertIsNotNone(c._refracted, "hover 应生成折射帧")

    def test_glass_off_fallback(self):
        S.set("glass.enabled", False)
        settle(self.app, 8)
        for c in self.cards:
            self.assertFalse(c._glass_active(), "glass 关闭后折射层不激活")

    def test_aurora_off_glass_on(self):
        """只关 Aurora：Liquid Glass 仍生效（折射可生成）。"""
        S.set("aurora.enabled", False)
        settle(self.app, 8)
        c = self.cards[0]
        self.assertTrue(c._glass_active())
        c._glass_need_refresh = True
        c.enterEvent(QEnterEvent(QPointF(10, 10), QPointF(10, 10), QPointF(10, 10)))
        settle(self.app, 30)
        self.assertIsNotNone(c._refracted, "Aurora 关闭后 Liquid Glass 仍应折射")

    def test_both_off_plain_card(self):
        S.set("aurora.enabled", False)
        S.set("glass.enabled", False)
        settle(self.app, 8)
        for c in self.cards:
            self.assertFalse(c._glass_active())
            self.assertFalse(S.get("aurora.enabled"))
        self.host.grab()  # 双关渲染不崩

    def test_material_param_change(self):
        c = self.cards[0]
        c._refract_frame(fast=True)
        self.assertIsNotNone(c._refracted)
        # S.set 同步触发 on_change → 渲染器立即作废
        S.set("glass.thickness", 0.8)
        self.assertIsNone(c._renderer, "厚度变化应重建渲染器")
        c._refract_frame(fast=True)
        self.assertIsNotNone(c._refracted)
        self.assertIsNotNone(c._renderer)

    def test_multiple_cards_render(self):
        """多卡片静态渲染不崩。"""
        for c in self.cards:
            c._refract_frame(fast=True)
            self.assertIsNotNone(c._refracted)
        self.host.grab()

    def test_aurora_off_glass_hover_keeps_timer(self):
        """修复：aurora 关闭 + glass 开启 + hover → timer 保持驱动折射（mouse_follow）。"""
        S.set("aurora.enabled", False)
        settle(self.app, 8)
        c = self.cards[0]
        c._glass_need_refresh = True
        c.enterEvent(QEnterEvent(QPointF(10, 10), QPointF(10, 10), QPointF(10, 10)))
        settle(self.app, 6)
        self.assertTrue(c._timer.isActive(),
                        "aurora 关闭时 hover 折射应保持 timer（mouse_follow）")
        self.assertIsNotNone(c._refracted)
        # 离开后静止 → 停表
        from PySide6.QtCore import QEvent
        c.leaveEvent(QEvent(QEvent.Leave))
        settle(self.app, 40)
        self.assertFalse(c._timer.isActive())

    def test_mouse_follow_off_single_refraction(self):
        """mouse_follow=False：hover 只折射一次，timer 不持续运行。"""
        S.set("glass.mouse_follow", False)
        S.set("aurora.enabled", False)
        settle(self.app, 8)
        c = self.cards[0]
        c._glass_need_refresh = True
        c.enterEvent(QEnterEvent(QPointF(10, 10), QPointF(10, 10), QPointF(10, 10)))
        settle(self.app, 6)
        self.assertIsNotNone(c._refracted)
        self.assertFalse(c._timer.isActive(), "mouse_follow=False 不应持续驱动 timer")

    def test_settings_persist(self):
        S.set("glass.thickness", 0.7)
        S.set("glass.frost", 0.5)
        S.set("glass.opacity", 0.4)
        S.set("glass.mouse_follow", False)
        self.assertEqual(S.get("glass.thickness"), 0.7)
        self.assertEqual(S.get("glass.frost"), 0.5)


if __name__ == "__main__":
    unittest.main()
