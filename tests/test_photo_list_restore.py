# -*- coding: utf-8 -*-
"""照片列表「查找相似照片 → 返回全部」快照与恢复测试（offscreen）。"""
import os
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

PHOTOS = Path(__file__).resolve().parents[1] / "photos"
PHOTO_A = str(PHOTOS / "20260604_091343.jpg")
PHOTO_B = str(PHOTOS / "20260623_100924.jpg")


class _FakeSimilarWorker(QObject):
    """替代 _SimilarSearchWorker：只记录启动，不跑真实索索。"""

    progress_updated = Signal(int, int)
    done_sig = Signal(object)
    failed = Signal(str)

    instances = []

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self._running = False
        _FakeSimilarWorker.instances.append(self)

    def start(self):
        self._running = True

    def isRunning(self):
        return self._running


class PhotoListRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.main_window_v3 import MainWindow
        self.win = MainWindow()
        self.win._ui_ready = True
        self.win.show()
        self.app.processEvents()
        _FakeSimilarWorker.instances = []
        self._patch = mock.patch("ui.main_window_v3._SimilarSearchWorker",
                                 _FakeSimilarWorker)
        self._patch.start()
        self.win.image_list = [PHOTO_A, PHOTO_B]
        self.win.current_image_path = PHOTO_A
        self.win._populate_photo_list(self.win.image_list)

    def tearDown(self):
        self._patch.stop()
        self.win.close()

    def _start_similar(self):
        self.win._find_similar_photos()
        worker = _FakeSimilarWorker.instances[-1]
        worker._running = False          # 模拟搜索结束
        return worker

    def test_snapshot_replace_and_restore(self):
        # 进入相似搜索前快照原列表
        self.win._find_similar_photos()
        self.assertEqual(self.win._photo_list_backup, [PHOTO_A, PHOTO_B])
        self.assertTrue(self.win.btn_restore_list.isHidden(),
                        "搜索中不显示返回按钮")

        self.win._on_similar_done([{"path": PHOTO_A, "similarity": 0.91}])
        self.assertEqual(self.win.image_list, [PHOTO_A], "结果替换列表")
        self.assertEqual(self.win._photo_list_mode, "similar")
        self.assertFalse(self.win.btn_restore_list.isHidden(),
                         "相似搜索后应显示「返回全部」")

        self.win._restore_photo_list()
        self.assertEqual(self.win.image_list, [PHOTO_A, PHOTO_B], "恢复原列表")
        self.assertEqual(self.win._photo_list_mode, "")
        self.assertIsNone(self.win._photo_list_backup)
        self.assertTrue(self.win.btn_restore_list.isHidden())
        self.assertIn("返回全部照片", self.win.statusBar().currentMessage())

    def test_second_search_keeps_first_snapshot(self):
        self.win._find_similar_photos()
        self.win._on_similar_done([{"path": PHOTO_A, "similarity": 0.9}])
        self.win._find_similar_photos()          # 连续搜索
        self.assertEqual(self.win._photo_list_backup, [PHOTO_A, PHOTO_B],
                         "连续搜索不应把相似结果当成原始列表")

    def test_restore_without_backup_falls_back_to_library(self):
        """无快照（如角色墙跳转前照片页为空）→ 回落到自动载入 photos/。"""
        self.win._photo_list_backup = None
        self.win.image_list = []
        self.win._photos_autoload_done = False
        self.win._restore_photo_list()
        self.app.processEvents()
        self.assertTrue(self.win.image_list, "应自动载入 photos/")
        self.assertEqual(self.win._photo_list_mode, "")
        self.assertTrue(self.win.btn_restore_list.isHidden())

    def test_open_folder_resets_similar_mode(self):
        self.win._find_similar_photos()
        self.win._on_similar_done([{"path": PHOTO_A, "similarity": 0.9}])
        tmp = os.path.dirname(PHOTO_A)
        with mock.patch("ui.main_window_v3.QFileDialog.getExistingDirectory",
                        return_value=tmp), \
                mock.patch("ui.main_window_v3.load_images_from_folder",
                           return_value=[PHOTO_B]):
            self.win.open_folder()
        self.assertEqual(self.win.image_list, [PHOTO_B])
        self.assertEqual(self.win._photo_list_mode, "")
        self.assertIsNone(self.win._photo_list_backup)
        self.assertTrue(self.win.btn_restore_list.isHidden())


class PhotoAutoLoadTests(unittest.TestCase):
    """照片页首次进入自动载入 photos/（列表非空/已载入过则不动）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.main_window_v3 import MainWindow
        self.win = MainWindow()
        self.win._ui_ready = True
        self.win.show()
        self.app.processEvents()

    def tearDown(self):
        self.win.close()

    def _photo_page_row(self):
        return self.win.content_stack.indexOf(self.win.photo_page)

    def test_first_visit_autoloads_photos(self):
        self.assertEqual(self.win.image_list, [])
        self.win._on_bottom_nav_changed(self._photo_page_row())
        self.app.processEvents()
        self.assertTrue(self.win.image_list,
                        "首次进入照片页应自动载入 photos/")
        self.assertEqual(self.win.image_list_widget.count(),
                         len(self.win.image_list))
        self.assertTrue(getattr(self.win, "_photos_autoload_done", False))
        self.assertIn("photos/", self.win.statusBar().currentMessage())

    def test_existing_list_not_overwritten(self):
        self.win.image_list = [PHOTO_A]
        self.win._populate_photo_list(self.win.image_list)
        self.win._on_bottom_nav_changed(self._photo_page_row())
        self.assertEqual(self.win.image_list, [PHOTO_A],
                         "已有列表不应被自动载入覆盖")

    def test_autoload_only_once(self):
        self.win._on_bottom_nav_changed(self._photo_page_row())
        self.app.processEvents()
        n = len(self.win.image_list)
        self.assertGreater(n, 0)
        self.win.image_list = []          # 模拟用户清空
        self.win._on_bottom_nav_changed(0)
        self.win._on_bottom_nav_changed(self._photo_page_row())
        self.assertEqual(self.win.image_list, [],
                         "只自动载入一次，避免反复覆盖用户上下文")


class GroupJumpTests(unittest.TestCase):
    """角色墙点照片 → 落到照片页 + 可返回全库（回归：曾切到 AI精选页）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.main_window_v3 import MainWindow
        self.win = MainWindow()
        self.win._ui_ready = True
        self.win.show()
        self.app.processEvents()
        from core.identity import get_reader
        groups = [g for g in get_reader().get_groups("fursuit_character")
                  if g.get("images")]
        self.assertTrue(groups, "库中应有带照片的兽装角色")
        self.group = groups[0]

    def tearDown(self):
        self.win.close()

    def _jump(self):
        self.win._open_group("fursuit", self.group, "测试角色")
        self.app.processEvents()
        members = self.win._group_pages["fursuit"].get("current_members") or []
        self.assertTrue(members)
        p, det = members[0]
        self.win._open_photo_in_photo_page(self.group, p, det)
        self.app.processEvents()
        return p

    def test_jump_lands_on_photo_page_with_restore(self):
        self.win._on_bottom_nav_changed(
            self.win.content_stack.indexOf(self.win.photo_page))
        self.app.processEvents()
        full = list(self.win.image_list)
        self.assertTrue(full)

        self._jump()
        self.assertIs(self.win.content_stack.currentWidget(),
                      self.win.photo_page, "应切换到照片页（曾误切 AI精选页）")
        self.assertEqual(self.win._photo_list_mode, "group")
        self.assertFalse(self.win.btn_restore_list.isHidden(),
                         "跳转后应显示「返回全部」")

        self.win._restore_photo_list()
        self.app.processEvents()
        self.assertEqual(self.win.image_list, full, "应恢复到跳转前的全库列表")
        self.assertEqual(self.win._photo_list_mode, "")
        self.assertTrue(self.win.btn_restore_list.isHidden())

    def test_restore_after_jump_from_empty_page_loads_library(self):
        self.assertEqual(self.win.image_list, [], "进入前照片页应为空")
        self._jump()
        self.assertTrue(self.win.image_list, "跳转后应显示该角色照片")
        self.win._restore_photo_list()
        self.app.processEvents()
        self.assertTrue(self.win.image_list,
                        "原本为空 → 返回全部应自动载入 photos/")
        self.assertEqual(self.win._photo_list_mode, "")


if __name__ == "__main__":
    unittest.main()
