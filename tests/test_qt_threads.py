# -*- coding: utf-8 -*-
"""QThread 回收：助手行为 + 各 worker 回调必须回收（防退出挂起回归）。"""
import os
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QMessageBox

from core.qt_threads import reap_thread


class _SleepWorker(QThread):
    def __init__(self, seconds=0.3):
        super().__init__()
        self._seconds = seconds

    def run(self):
        time.sleep(self._seconds)


class ReapThreadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_none_and_finished_are_safe(self):
        self.assertTrue(reap_thread(None))
        w = _SleepWorker(0.0)
        w.start()
        w.wait(2000)
        self.assertTrue(reap_thread(w))

    def test_waits_for_running_thread(self):
        w = _SleepWorker(0.35)
        w.start()
        t0 = time.perf_counter()
        ok = reap_thread(w, timeout_ms=3000)
        elapsed = time.perf_counter() - t0
        self.assertTrue(ok)
        self.assertFalse(w.isRunning())
        self.assertGreaterEqual(elapsed, 0.2, "回收应等待线程结束")

    def test_bad_object_is_safe(self):
        self.assertTrue(reap_thread(object()))


class HandlersReapTests(unittest.TestCase):
    """所有 done/failed 回调在清引用前必须调用回收（否则退出可能挂起）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.main_window_v3 import MainWindow
        self.win = MainWindow()
        self.win._ui_ready = True

    def tearDown(self):
        self.win.close()

    def test_main_window_handlers_reap(self):
        stub = object()
        with mock.patch("core.qt_threads.reap_thread") as reap, \
                mock.patch.object(QMessageBox, "critical"):
            self.win._similar_worker = stub
            self.win._on_similar_failed("boom")
            self.assertTrue(reap.called, "相似搜索失败回调应回收线程")
            self.assertEqual(reap.call_args[0][0], stub)
            self.assertIsNone(self.win._similar_worker)

            reap.reset_mock()
            self.win._pending_worker = stub
            self.win._on_analyze_failed("boom")
            self.assertTrue(reap.called, "分析失败回调应回收线程")

    def test_duplicates_page_visual_handlers_reap(self):
        from ui.duplicates_page import DuplicatesPage
        page = DuplicatesPage(auto_scan=False)
        try:
            stub = object()
            with mock.patch("ui.duplicates_page.reap_thread") as reap, \
                    mock.patch.object(QMessageBox, "information"):
                page._visual_worker = stub
                page._on_visual_failed("boom")
            self.assertTrue(reap.called)
            self.assertEqual(reap.call_args[0][0], stub)
            self.assertIsNone(page._visual_worker)
        finally:
            page.close()

    def test_role_center_handlers_reap(self):
        stub = object()
        with mock.patch("ui.role_center_mixin.reap_thread") as reap, \
                mock.patch.object(QMessageBox, "critical"):
            self.win._scan_worker = stub
            self.win._scan_worker_page = "fursuit"
            self.win._on_scan_failed("boom")
            self.assertTrue(reap.called, "扫描失败回调应回收线程")
            self.assertIsNone(self.win._scan_worker)

            reap.reset_mock()
            self.win._ai_pick_worker = stub
            self.win._on_ai_pick_all_done("role", None)
            self.assertTrue(reap.called, "AI 精选完成回调应回收线程")
            self.assertIsNone(self.win._ai_pick_worker)


if __name__ == "__main__":
    unittest.main()
