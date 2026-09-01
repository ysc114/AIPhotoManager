"""Phase 4-1 全局搜索增强：类型/日期/收藏筛选 + 照片所属角色 + 分区结果。

只读查询测试（不触发 AI、不写库）。
"""

import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QDate
from config.settings_manager import settings as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def settle(app, frames=40, dt=0.02):
    for _ in range(frames):
        t0 = time.time()
        app.processEvents()
        time.sleep(max(0.0, dt - (time.time() - t0)))


class SearchFilterIndexTests(unittest.TestCase):
    """search_index：类型/日期/收藏筛选 + 角色映射 + 日期解析。"""

    @classmethod
    def setUpClass(cls):
        from core.search_index import SearchIndex
        cls.idx = SearchIndex(project_root=ROOT)
        cls.idx.refresh()

    def test_type_filter_roles(self):
        r = self.idx.search("", type_filter="fursuit_character")
        self.assertGreaterEqual(len(r["roles"]), 10)
        for g in r["roles"]:
            self.assertEqual(g.get("type"), "fursuit_character")
        r2 = self.idx.search("", type_filter="real_person")
        for g in r2["roles"]:
            self.assertEqual(g.get("type"), "real_person")

    def test_date_filter_photos(self):
        r = self.idx.search("", date_from="2020-01-01", date_to="2026-12-31")
        self.assertGreaterEqual(len(r["photos"]), 5)
        # 空日期范围 → 无结果
        r2 = self.idx.search("", date_from="1999-01-01", date_to="1999-12-31")
        self.assertEqual(r2["photos"], [])

    def test_favorite_only(self):
        r = self.idx.search("", favorite_only=True)
        for p in r["photos"]:
            self.assertTrue(p["favorite"])

    def test_photo_role_mapping(self):
        """照片结果带所属角色（若存在）。"""
        r = self.idx.search("")
        # 至少部分照片应映射到角色（库里绝大多数照片属于角色组）
        with_role = [p for p in r["photos"] if p.get("role")]
        self.assertGreater(len(with_role), 0)
        for p in with_role:
            self.assertIn("name", p["role"])

    def test_photo_date_parsing(self):
        """文件名时间戳 / 日期名 / mtime fallback。"""
        from core.search_index import SearchIndex
        idx = SearchIndex(project_root=ROOT)
        # 13 位毫秒时间戳 → 有效日期
        ts = idx._photo_date("C:/x/photos/1787539644969.png")
        self.assertRegex(ts, r"^\d{4}-\d{2}-\d{2}$")
        # YYYYMMDD 文件名
        d = idx._photo_date("C:/x/photos/20260601_125816.jpg")
        self.assertEqual(d, "2026-06-01")
        # mtime fallback
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        tmp.close()
        try:
            d2 = idx._photo_date(tmp.name)
            self.assertRegex(d2, r"^\d{4}-\d{2}-\d{2}$")
        finally:
            os.unlink(tmp.name)

    def test_empty_query_filters_work(self):
        """关键词为空时筛选器独立生效（返回过滤后全量）。"""
        r = self.idx.search("", type_filter="fursuit_character",
                            date_from="2020-01-01", date_to="2026-12-31")
        self.assertGreaterEqual(len(r["roles"]), 10)


class SearchBarFilterUITests(unittest.TestCase):
    """搜索框过滤 UI：控件在位 / 筛选驱动面板 / 分区 / 清除。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.main_window_v3 import MainWindow
        self.win = MainWindow()
        self.win._ui_ready = True
        self.win.show()
        settle(self.app, 8)

    def tearDown(self):
        self.win.close()

    def test_filter_widgets_present(self):
        sb = self.win.search_bar
        self.assertEqual(sb._type_combo.count(), 3)
        self.assertTrue(sb._date_from.isVisible())
        self.assertTrue(sb._date_to.isVisible())
        self.assertFalse(sb._fav_only.isChecked())

    def test_type_filter_drives_panel(self):
        sb = self.win.search_bar
        sb._type_combo.setCurrentIndex(1)   # 兽装角色（空关键词）
        settle(self.app, 30)
        self.assertTrue(sb._panel_visible)
        # 分区标题
        first = sb._panel.item(0).text()
        self.assertIn("角色结果", first)

    def test_clear_filters_hides_panel(self):
        sb = self.win.search_bar
        sb._type_combo.setCurrentIndex(2)
        settle(self.app, 30)
        self.assertTrue(sb._panel_visible)
        sb._clear_filters()
        settle(self.app, 30)
        self.assertFalse(sb._panel_visible)
        self.assertEqual(sb._type_combo.currentIndex(), 0)

    def test_date_filter_panel(self):
        sb = self.win.search_bar
        sb._date_from.setDate(QDate(2026, 1, 1))
        sb._date_to.setDate(QDate(2026, 12, 31))
        settle(self.app, 30)
        self.assertTrue(sb._panel_visible)
        # 面板里应有照片结果区（2026 有照片）
        texts = [sb._panel.item(i).text() for i in range(sb._panel.count())
                 if not (sb._panel.item(i).flags() & Qt.ItemIsEnabled)]
        self.assertTrue(any("照片结果" in t for t in texts),
                        f"应有照片结果分区, got {texts[:5]}")


if __name__ == "__main__":
    unittest.main()
