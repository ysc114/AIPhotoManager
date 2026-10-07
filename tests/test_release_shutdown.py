"""Release regressions for the duplicate page's real QThread ownership."""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from ui.duplicates_page import DuplicatesPage, _VisualScanWorker


class _BlockedWorker(QThread):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def run(self):
        self.entered.set()
        self.release.wait(5)


class DuplicateShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.page = DuplicatesPage(
            photos_dir=str(root), index_path=str(root / "visual.json"),
            auto_scan=False)

    def tearDown(self):
        worker = self.page._visual_worker
        if worker is not None:
            if hasattr(worker, "release"):
                worker.release.set()
            worker.requestInterruption()
            self.assertTrue(worker.wait(5000))
        self.page.shutdown_workers()
        self.page.close()
        self.temp.cleanup()

    def test_shutdown_timeout_keeps_live_thread_then_reaps_it(self):
        worker = _BlockedWorker()
        self.page._visual_worker = worker
        worker.start()
        self.assertTrue(worker.entered.wait(2))
        try:
            self.assertFalse(self.page.shutdown_workers(timeout_ms=1))
            self.assertIs(self.page._visual_worker, worker)
            self.assertTrue(worker.isRunning())
            self.assertTrue(worker.isInterruptionRequested())
        finally:
            worker.release.set()
            self.assertTrue(worker.wait(2000))
        self.assertTrue(self.page.shutdown_workers(timeout_ms=1))
        self.assertIsNone(self.page._visual_worker)

    def test_shutdown_without_worker_succeeds(self):
        self.assertTrue(self.page.shutdown_workers(timeout_ms=1))

    def test_real_visual_worker_cancels_at_progress_boundary(self):
        entered = threading.Event()
        release = threading.Event()
        index = mock.Mock()

        def compute_all(progress_cb):
            entered.set()
            release.wait(5)
            progress_cb(10, 100)
            self.fail("interrupted worker continued computing")

        index.compute_all.side_effect = compute_all
        worker = _VisualScanWorker(index)
        self.page._visual_worker = worker
        signals = []
        worker.cancelled.connect(lambda: signals.append("cancelled"))
        worker.done.connect(lambda _: signals.append("done"))
        worker.failed.connect(lambda _: signals.append("failed"))
        worker.start()
        self.assertTrue(entered.wait(2))
        try:
            worker.requestInterruption()
        finally:
            release.set()
            self.assertTrue(worker.wait(2000))
        self.app.processEvents()
        self.assertEqual(signals, ["cancelled"])
        index.groups.assert_not_called()
        self.assertTrue(self.page.shutdown_workers())

    def test_completion_handlers_keep_reference_while_cleanup_is_running(self):
        worker = _BlockedWorker()
        self.page._visual_worker = worker
        with mock.patch("ui.duplicates_page.reap_thread", return_value=False), \
                mock.patch("ui.duplicates_page.QTimer.singleShot") as retry:
            self.page._on_visual_done([])
            self.assertIs(self.page._visual_worker, worker)
            self.page._on_visual_failed("test error")
            self.assertIs(self.page._visual_worker, worker)
            self.page._on_visual_cancelled()
            self.assertIs(self.page._visual_worker, worker)
            self.assertEqual(retry.call_count, 3)


if __name__ == "__main__":
    unittest.main()
