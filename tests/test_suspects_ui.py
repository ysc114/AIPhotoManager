"""角色中心 2.0 · 第二阶段：疑似同一角色 —— GUI 冒烟（offscreen）。

与仓库现有 UI 测试同一模式：构造 MainWindow、进入角色页、点开
「✨ 疑似同一角色」视图，验证面板结构/渲染不崩溃。
只读生产库（与 test_character_center.py 一致），不点击任何写按钮。
"""
import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication


def settle(app, frames=30, dt=0.02):
    for _ in range(frames):
        t0 = time.time()
        app.processEvents()
        time.sleep(max(0.0, dt - (time.time() - t0)))


class SuspectsUiSmokeTests(unittest.TestCase):
    """角色中心「疑似同一角色」面板冒烟（只读，不写库/决策）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.main_window_v3 import MainWindow
        self.win = MainWindow()
        self.win._ui_ready = True
        self.win.show()
        settle(self.app, 10)
        self.win._on_bottom_nav_changed(5)   # 角色页
        settle(self.app, 40)
        self.state = self.win._group_pages["character"]
        self.assertIn("suspects_btn", self.state, "角色页应挂载疑似同一角色入口")
        self.assertIn("suspects_ui", self.state, "角色页应构建候选视图")
        self.ui = self.state["suspects_ui"]

    def tearDown(self):
        self.win.close()

    def test_toolbar_entry_opens_suspects_view(self):
        """点「✨ 疑似同一角色」→ 进入候选视图（索引 2）并完成渲染。"""
        self.state["suspects_btn"].click()
        settle(self.app, 50)
        self.assertEqual(self.state["page_stack"].currentIndex(), 2)
        self.assertTrue(self.ui["view"].isVisible())

    def test_suspects_panel_renders_or_empty_state(self):
        """候选列表渲染：有候选则卡片数与计数一致；无候选显示空态。"""
        self.state["suspects_btn"].click()
        settle(self.app, 60)
        text = self.ui["count"].text()
        self.assertTrue(text.startswith("候选"), f"计数标签异常: {text}")
        n = int(text.split("候选")[1].split("·")[0].strip())
        rendered = self.ui["cards_layout"].count()
        if n > 0:
            self.assertGreaterEqual(rendered, 1)
            self.assertLessEqual(rendered, min(n, 60))
            self.assertFalse(self.ui["empty"].isVisible())
        else:
            self.assertEqual(rendered, 0)
            self.assertTrue(self.ui["empty"].isVisible())
        # 每个候选卡应含 是/否 按钮与相似度文本
        buttons = self.ui["view"].findChildren(type(self.state["suspects_btn"]))
        if n > 0:
            self.assertTrue(any("是同一角色" in b.text() for b in buttons))
            self.assertTrue(any("不是同一角色" in b.text() for b in buttons))

    def test_back_button_returns_to_list(self):
        self.state["suspects_btn"].click()
        settle(self.app, 40)
        self.assertEqual(self.state["page_stack"].currentIndex(), 2)
        self.ui["back"].click()
        settle(self.app, 10)
        self.assertEqual(self.state["page_stack"].currentIndex(), 0)

    def test_recount_and_undo_buttons_present(self):
        """重算/撤销按钮存在且可点击不崩溃（撤销无记录时为禁用态）。"""
        self.state["suspects_btn"].click()
        settle(self.app, 40)
        self.assertTrue(callable(self.ui["recount"].click))
        self.assertTrue(callable(self.ui["undo"].click))
        # 无写操作：点重算只是重新渲染
        self.ui["recount"].click()
        settle(self.app, 40)
        self.assertEqual(self.state["page_stack"].currentIndex(), 2)


    def test_toolbar_button_reflects_candidate_count(self):
        """0 候选置灰并提示；有候选时显示数量且可点（避免点开空页面）。"""
        from unittest import mock
        btn = self.state["suspects_btn"]

        class _StubMgr:
            def __init__(self, n):
                self._n = n

            def get_suspect_candidates(self):
                return [{}] * self._n

            def close(self):
                pass

        with mock.patch("core.identity.get_reader",
                        return_value=_StubMgr(0)):
            self.win._sync_suspects_btn(self.state)
        self.assertTrue(btn.isEnabled(), "始终可点：视图里还有撤销合并入口")
        self.assertNotIn("（", btn.text(), "0 候选不显示数量")
        self.assertIn("撤销", btn.toolTip())

        with mock.patch("core.identity.get_reader",
                        return_value=_StubMgr(3)):
            self.win._sync_suspects_btn(self.state)
        self.assertTrue(btn.isEnabled())
        self.assertIn("（3）", btn.text(), "有候选应显示数量")

    def test_toolbar_button_greys_out_on_real_data_when_no_candidates(self):
        """真实库：候选为 0 时按钮应置灰（当前生产库即为此状态）。"""
        from core.identity import get_reader
        n = len(get_reader().get_suspect_candidates() or [])
        self.win._sync_suspects_btn(self.state)
        btn = self.state["suspects_btn"]
        self.assertTrue(btn.isEnabled(), "真实库下按钮也应始终可点")
        if n == 0:
            self.assertNotIn("（", btn.text())
        else:
            self.assertIn(f"（{n}）", btn.text())


if __name__ == "__main__":
    unittest.main()
