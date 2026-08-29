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
        # 设置中心：关键功能按钮在位（备份/刷新统计/打开目录）
        btns = [b.text() for b in self.window.settings_page.findChildren(QPushButton)]
        for expected in ("💾 立即备份", "📂 打开备份目录", "🔄 刷新统计", "📡 重新扫描新照片"):
            self.assertIn(expected, btns, f"设置中心缺少按钮: {expected}")
        # 不提供危险的全量重聚按钮
        self.assertNotIn("重新聚类全部照片", btns)

    def test_nav_rows_map_to_real_pages(self):
        # 新版导航（AI精选提升为一级）：AI精选=1, 收藏=6, 待处理=7, 设置=8
        self.window._switch_page(1)
        self.assertIs(
            self.window.content_stack.currentWidget(),
            self.window.ai_pick_page,
        )
        self.window._switch_page(6)
        self.assertIs(
            self.window.content_stack.currentWidget(),
            self.window.favorites_page,
        )
        self.window._switch_page(7)
        self.assertIs(
            self.window.content_stack.currentWidget(),
            self.window.pending_page,
        )
        self.window._switch_page(8)
        self.assertIs(
            self.window.content_stack.currentWidget(),
            self.window.settings_page,
        )
        # 导航项数量与顺序
        items = [self.window.nav_list.item(i).text() for i in range(self.window.nav_list.count())]
        self.assertEqual(len(items), 9)
        self.assertIn("AI精选", items[1])

    def test_photo_page_favorite_button(self):
        btns = [b.text() for b in self.window.photo_page.findChildren(QPushButton)]
        self.assertIn("⭐ 收藏当前", btns)


if __name__ == "__main__":
    unittest.main()
