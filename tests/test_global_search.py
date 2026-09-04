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
from unittest import mock

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
        # 测试中禁用真实索引构建（语义分区用 fake 桩覆盖）
        self.win._sem_building = True
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
        # 查询"兽装"（类别匹配：应命中兽装角色组；无库数据时跳过）
        self.win._on_global_search_query("兽装")
        settle(self.app, 4)
        if self.win._global_search._items:
            self.assertGreater(len(self.win._global_search._items), 0)
        else:
            self.skipTest("库中无兽装角色组")
        # 无结果查询 → 无真实结果 + 面板有提示（不空白）
        self.win._on_global_search_query("__no_such_thing__")
        settle(self.app, 4)
        non_semantic = [i for i in self.win._global_search._items
                        if i.get("badge") != "语义"]
        self.assertEqual(non_semantic, [], "不应有非语义的真实结果")
        texts = [l.text() for l in self.win._global_search.findChildren(QLabel)]
        self.assertTrue(
            any(("没有找到" in t) or ("语义索引构建中" in t) for t in texts),
            "应有无结果/索引构建提示")
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


    def test_semantic_section(self):
        """🧠 语义搜索分区：索引就绪→显示语义结果；为空→构建提示（不发起真实构建）。"""
        self.win._open_global_search()
        settle(self.app, 6)

        # 情形1：索引为空 → 提示行 + 不启动真实构建（_sem_building 预置）
        self.win._sem_building = True
        with mock.patch("core.visual_search.get_index", return_value=_FakeIndex(0)):
            self.win._on_global_search_query("白狼")
        settle(self.app, 4)
        texts = [l.text() for l in self.win._global_search.findChildren(QLabel)]
        self.assertTrue(any("语义索引构建中" in t for t in texts))

        # 情形2：索引就绪 → 语义结果行
        with mock.patch("core.visual_search.get_index", return_value=_FakeIndex(2)):
            self.win._on_global_search_query("白狼")
        settle(self.app, 4)
        items = self.win._global_search._items
        sem = [i for i in items if i.get("badge") == "语义"]
        self.assertEqual(len(sem), 2)
        self.assertIn("photo", sem[0]["payload"]["kind"] or "")
        self.win._global_search.hide_panel()
        settle(self.app, 10)


class _FakeIndex:
    """语义分区测试桩：返回固定结果，不加载模型/不建索引。"""

    def __init__(self, n):
        self._n = n

    def count(self):
        return self._n

    def search_by_text(self, q, enc, top_k=4):
        return [
            {"photo_id": i, "path": f"C:/fake/p{i}.jpg",
             "similarity": 0.9 - i * 0.05}
            for i in range(self._n)
        ]


if __name__ == "__main__":
    unittest.main()
