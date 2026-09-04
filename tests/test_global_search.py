"""Spotlight 全局搜索面板测试（④ 第一阶段：UI 框架，offscreen）。

覆盖：防抖请求 / 分区渲染 / 键盘上下与 Enter / Esc 关闭 / 最近搜索 /
MainWindow 集成（入口按钮、Ctrl+K 与 Ctrl+Shift+F 快捷键、结果分发）。
"""
import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtTest import QTest

from ui.components.global_search import GlobalSearchPanel


def settle(app, frames=20, dt=0.02):
    for _ in range(frames):
        t0 = time.time()
        app.processEvents()
        time.sleep(max(0.0, dt - (time.time() - t0)))


def _item(i):
    return {
        "icon": "🐺", "title": f"角色{i}", "subtitle": "兽装角色 · Fursee",
        "badge": "角色", "payload": {"kind": "character", "id": i},
    }


class GlobalSearchPanelTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = GlobalSearchPanel()
        self.panel.show_panel()
        settle(self.app, 6)

    def tearDown(self):
        self.panel.hide_panel()
        settle(self.app, 8)
        self.panel.close()

    def test_debounce_search_requested(self):
        got = []
        self.panel.search_requested.connect(got.append)
        self.panel.input.setText("白狼")
        settle(self.app, 6)      # < 120ms：未触发
        self.assertEqual(got, [])
        settle(self.app, 8)      # 超过防抖
        self.assertEqual(got, ["白狼"])
        self.assertEqual(self.panel.query(), "白狼")

    def test_results_render_and_keyboard_navigation(self):
        self.panel.set_results(
            [{"title": "角色", "items": [_item(1), _item(2)]}])
        self.assertEqual(len(self.panel._items), 2)
        # 键盘下/上
        QTest.keyClick(self.panel.input, Qt.Key_Down)
        self.assertEqual(self.panel._sel, 0)
        QTest.keyClick(self.panel.input, Qt.Key_Down)
        self.assertEqual(self.panel._sel, 1)
        QTest.keyClick(self.panel.input, Qt.Key_Up)
        self.assertEqual(self.panel._sel, 0)
        # Enter 选中并发出信号
        got = []
        self.panel.result_selected.connect(got.append)
        QTest.keyClick(self.panel.input, Qt.Key_Return)
        settle(self.app, 10)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["payload"]["id"], 1)
        self.assertFalse(self.panel.isVisible(), "选中后面板应关闭")

    def test_escape_closes(self):
        self.panel.input.setText("x")
        QTest.keyClick(self.panel.input, Qt.Key_Escape)
        settle(self.app, 12)
        self.assertFalse(self.panel.isVisible())

    def test_recent_chips(self):
        self.panel.set_results([], recent=["白狼", "展会"])
        chips = [w for w in self.panel.findChildren(QLabel)
                 if "白狼" in w.text()]
        self.assertGreaterEqual(len(chips), 1)
        self.assertTrue(self.panel._recent_host.isVisible())
        self.panel.set_results([], recent=[])
        self.assertFalse(self.panel._recent_host.isVisible())


class GlobalSearchWindowTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.main_window_v3 import MainWindow
        self.win = MainWindow()
        self.win._ui_ready = True
        self.win.show()
        settle(self.app, 10)

    def tearDown(self):
        self.win.close()

    def test_entry_and_shortcuts(self):
        self.assertIsNotNone(getattr(self.win, "gs_btn", None))
        self.win._open_global_search()
        settle(self.app, 8)
        self.assertTrue(self.win._global_search.isVisible())
        seqs = {sc.key().toString() for sc in self.win.findChildren(QShortcut)}
        self.assertIn("Ctrl+K", seqs)
        self.assertIn("Ctrl+Shift+F", seqs)
        self.win._toggle_global_search()
        settle(self.app, 12)
        self.assertFalse(self.win._global_search.isVisible())

    def test_query_and_result_dispatch(self):
        self.win._open_global_search()
        settle(self.app, 6)
        # 空查询 → 最近搜索（空列表也安全）
        self.win._on_global_search_query("")
        # 查询"角色"（现有只读数据；结果可为空，不崩溃即可）
        self.win._on_global_search_query("角色")
        settle(self.app, 4)
        # 无 payload 的结果：仅记录最近搜索，不跳转不崩溃
        self.win._on_global_result_selected(
            {"title": "测试条目", "payload": {}})
        self.assertIn("测试条目", self.win._gs_recents)
        # 面板可再次打开/关闭
        self.win._open_global_search()
        settle(self.app, 4)
        self.assertTrue(self.win._global_search.isVisible())
        self.win._global_search.hide_panel()
        settle(self.app, 12)


if __name__ == "__main__":
    unittest.main()
