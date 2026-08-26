# -*- coding: utf-8 -*-
"""UI Phase 3 测试（收藏/待处理/设置页结构）。"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QPushButton

from ui.main_window_v3 import MainWindow


class Phase3UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()

    def test_favorites_page_built(self):
        self.assertTrue(hasattr(self.window, "favorites_page"))
        self.assertTrue(hasattr(self.window, "_load_favorites_page"))
        self.assertTrue(hasattr(self.window, "_toggle_favorite_current"))

    def test_pending_page_stats_label(self):
        self.assertTrue(hasattr(self.window, "_pending_stats_label"))
        self.assertTrue(hasattr(self.window, "_refresh_pending_stats"))

    def test_settings_page_built(self):
        self.assertTrue(hasattr(self.window, "settings_page"))
        self.assertTrue(hasattr(self.window, "_refresh_settings_page"))
        # 只读：无修改按钮（无保存/应用）
        btns = [b.text() for b in self.window.settings_page.findChildren(QPushButton)]
        self.assertEqual(btns, ["🔄 刷新状态"])

    def test_nav_rows_map_to_real_pages(self):
        # 收藏=5, 待处理=6, 设置=7
        self.window._switch_page(5)
        self.assertIs(
            self.window.content_stack.currentWidget(),
            self.window.favorites_page,
        )
        self.window._switch_page(6)
        self.assertIs(
            self.window.content_stack.currentWidget(),
            self.window.pending_page,
        )
        self.window._switch_page(7)
        self.assertIs(
            self.window.content_stack.currentWidget(),
            self.window.settings_page,
        )

    def test_photo_page_favorite_button(self):
        btns = [b.text() for b in self.window.photo_page.findChildren(QPushButton)]
        self.assertIn("⭐ 收藏当前", btns)


if __name__ == "__main__":
    unittest.main()
