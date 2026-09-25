# -*- coding: utf-8 -*-
"""整理命名（逐个给未命名角色命名）测试：只走注入回调，不碰生产库。"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.naming_walkthrough import UnnamedRolesDialog


def _group(cid, name="", serial=1, gtype="fursuit_character", photos=1):
    return {"character_id": cid, "name": name, "type": gtype,
            "serial": serial, "count": photos,
            "images": [f"C:/fake/{cid}.jpg"],
            "detections": [{"image_path": f"C:/fake/{cid}.jpg",
                            "detection_index": 0, "bbox": "[0,0,10,10]",
                            "confidence": 0.9,
                            "embedding_type": "fursuit_fursee"}]}


class UnnamedRolesDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, groups, saver=None):
        self.saved = []
        saver = saver or (lambda cid, name: self.saved.append((cid, name)) or True)
        return UnnamedRolesDialog(groups, saver)

    def test_only_unnamed_groups_and_stable_label(self):
        dlg = self._dialog([_group("a", name="小白", serial=1),
                            _group("b", serial=7),
                            _group("c", serial=8)])
        try:
            self.assertEqual(len(dlg._groups), 2, "只遍历未命名角色")
            self.assertEqual(dlg.remaining(), 2)
            self.assertIn("#007", dlg._title.text())
            self.assertIn("1 / 2", dlg._progress.text())
            self.assertIn("出现 1 张照片", dlg._sub.text())
        finally:
            dlg.close()

    def test_enter_saves_and_advances(self):
        dlg = self._dialog([_group("b", serial=7), _group("c", serial=8)])
        try:
            dlg._edit.setText("  小花  ")
            dlg._on_save()
            self.assertEqual(self.saved, [("b", "小花")])
            self.assertEqual(dlg.renamed_count, 1)
            self.assertEqual(dlg.remaining(), 1)
            self.assertEqual(dlg.current_group()["character_id"], "c")
            self.assertEqual(dlg._edit.text(), "", "进入下一个应清空输入框")
        finally:
            dlg.close()

    def test_skip_does_not_save(self):
        dlg = self._dialog([_group("b", serial=7), _group("c", serial=8)])
        try:
            dlg._on_skip()
            self.assertEqual(self.saved, [])
            self.assertEqual(dlg.renamed_count, 0)
            self.assertEqual(dlg.current_group()["character_id"], "c")
        finally:
            dlg.close()

    def test_empty_name_acts_as_skip(self):
        dlg = self._dialog([_group("b", serial=7), _group("c", serial=8)])
        try:
            dlg._edit.setText("   ")
            dlg._on_save()
            self.assertEqual(self.saved, [], "空名字不应写库")
            self.assertEqual(dlg.current_group()["character_id"], "c")
        finally:
            dlg.close()

    def test_failed_save_keeps_current_role(self):
        dlg = self._dialog([_group("b", serial=7)], saver=lambda cid, name: False)
        try:
            dlg._edit.setText("小花")
            dlg._on_save()
            self.assertEqual(dlg.renamed_count, 0)
            self.assertEqual(dlg.current_group()["character_id"], "b",
                             "写入失败应停留当前角色便于重试")
        finally:
            dlg.close()

    def test_finish_after_last_item(self):
        dlg = self._dialog([_group("b", serial=7)])
        try:
            dlg._edit.setText("小花")
            dlg._on_save()
            self.assertEqual(dlg.renamed_count, 1)
            self.assertIsNone(dlg.current_group())
            self.assertEqual(dlg.remaining(), 0)
        finally:
            dlg.close()


class NamingWalkthroughWiringTests(unittest.TestCase):
    """主窗口接线：只传未命名角色；保存走新建 IdentityManager（写路径）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.main_window_v3 import MainWindow
        self.win = MainWindow()
        self.win._ui_ready = True

    def tearDown(self):
        self.win.close()

    def test_open_passes_only_unnamed_groups(self):
        state = self.win._group_pages["character"]
        state["groups"] = [_group("a", name="小白", serial=1),
                           _group("b", serial=2),
                           _group("c", serial=3)]
        captured = {}

        class _StubDialog:
            renamed_count = 2

            def __init__(self, groups, saver, cover_provider=None, parent=None):
                captured["groups"] = groups
                captured["saver"] = saver
                captured["cover"] = cover_provider

            def exec(self):
                return 1

        with mock.patch("ui.naming_walkthrough.UnnamedRolesDialog", _StubDialog), \
                mock.patch.object(self.win, "_load_groups_into_page") as reload_:
            self.win._open_naming_walkthrough("character")
        self.assertEqual([g["character_id"] for g in captured["groups"]],
                         ["b", "c"])
        self.assertTrue(callable(captured["saver"]))
        self.assertTrue(callable(captured["cover"]))
        self.assertTrue(reload_.called, "命名后应刷新角色页")

    def test_open_without_unnamed_shows_hint(self):
        state = self.win._group_pages["character"]
        state["groups"] = [_group("a", name="小白", serial=1)]
        with mock.patch("ui.naming_walkthrough.UnnamedRolesDialog") as dlg:
            self.win._open_naming_walkthrough("character")
        self.assertFalse(dlg.called, "没有未命名角色时不应弹窗")
        self.assertIn("没有未命名角色", self.win.statusBar().currentMessage())

    def test_save_role_name_uses_fresh_manager(self):
        calls = []

        class _StubMgr:
            def update_name(self, cid, name):
                calls.append((cid, name))

            def close(self):
                calls.append("closed")

        with mock.patch("core.identity.IdentityManager", _StubMgr):
            ok = self.win._save_role_name("cid-1", "小花")
        self.assertTrue(ok)
        self.assertEqual(calls, [("cid-1", "小花"), "closed"])

    def test_sync_naming_btn_reflects_count(self):
        state = self.win._group_pages["character"]
        # 工具栏控件在首次加载角色页时才挂到 state；这里直接挂上按钮
        state["naming_btn"] = self.win._char_toolbar_controls[-1]
        state["groups"] = [_group("a", name="小白", serial=1),
                           _group("b", serial=2)]
        self.win._sync_naming_btn(state)
        btn = state["naming_btn"]
        self.assertTrue(btn.isEnabled())
        self.assertIn("（1）", btn.text())
        state["groups"] = [_group("a", name="小白", serial=1)]
        self.win._sync_naming_btn(state)
        self.assertFalse(btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
