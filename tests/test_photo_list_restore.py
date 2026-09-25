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

    def test_restore_without_backup_is_safe(self):
        self.win._photo_list_backup = None
        self.win._restore_photo_list()
        self.assertIn("没有可恢复", self.win.statusBar().currentMessage())
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


if __name__ == "__main__":
    unittest.main()
