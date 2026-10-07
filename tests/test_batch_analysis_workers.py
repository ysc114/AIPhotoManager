"""Focused regressions for moving the existing batch-analysis actions off UI."""

import os
import threading
import unittest
from unittest import mock
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from ui.batch_analysis import BatchAnalysisWorker
from ui.main_window_v3 import MainWindow
from core.ai_organizer import AIOrganizer


class BatchAnalysisWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _run(self, worker):
        worker.start()
        self.assertTrue(worker.wait(5000))
        self.app.processEvents()

    def test_auto_classification_runs_in_worker_and_preserves_copy_options(self):
        caller_thread = threading.get_ident()
        observed = {}

        def fake_auto_organize(paths, target, **kwargs):
            observed.update(paths=paths, target=target, kwargs=kwargs,
                            thread=threading.get_ident())
            kwargs["progress_callback"](1, 2, "working")
            return {"success": 1, "failed": 0, "errors": [], "categories": {}}

        worker = BatchAnalysisWorker("classify", ["a.jpg", "b.jpg"], "out")
        progress = []
        worker.progress.connect(lambda value, text: progress.append((value, text)))
        with mock.patch("core.auto_organizer.auto_organize", fake_auto_organize):
            self._run(worker)

        self.assertNotEqual(observed["thread"], caller_thread)
        self.assertEqual(observed["kwargs"]["mode"], "copy")
        self.assertTrue(observed["kwargs"]["remove_duplicates"])
        self.assertEqual(progress, [(50, "working")])
        self.assertEqual(worker.result["success"], 1)
        self.assertIsNone(worker.error)

    def test_event_loop_remains_responsive_during_batch_action(self):
        release = threading.Event()
        ticks = []

        def fake_auto_organize(*_args, **_kwargs):
            self.assertTrue(release.wait(3))
            return {"success": 0, "failed": 0, "errors": [], "categories": {}}

        worker = BatchAnalysisWorker("classify", ["a.jpg"], "out")
        loop = QEventLoop()
        timer = QTimer()
        timer.setInterval(5)
        timer.timeout.connect(lambda: ticks.append(True))
        worker.finished.connect(loop.quit)
        with mock.patch("core.auto_organizer.auto_organize", fake_auto_organize):
            timer.start()
            worker.start()
            QTimer.singleShot(60, release.set)
            loop.exec()
            self.assertTrue(worker.wait(1000))
        timer.stop()
        self.assertGreater(len(ticks), 0)

    def test_organizer_receives_progress_and_cancellation_callbacks(self):
        organizer = mock.Mock()
        organizer.organize_folder.return_value = {
            "total": 1, "success": 1, "failed": 0,
            "categories": {}, "characters": [],
        }
        with mock.patch("core.ai_organizer.AIOrganizer", return_value=organizer):
            worker = BatchAnalysisWorker("organize", ["a.jpg"])
            self._run(worker)

        args, kwargs = organizer.organize_folder.call_args
        self.assertEqual(args, (["a.jpg"],))
        self.assertTrue(callable(kwargs["progress_callback"]))
        self.assertTrue(callable(kwargs["cancelled"]))
        organizer.close.assert_called_once_with()
        self.assertEqual(worker.result["success"], 1)
        self.assertIsNone(worker.error)

    def test_worker_surfaces_failure_and_closes_organizer(self):
        organizer = mock.Mock()
        organizer.organize_folder.side_effect = RuntimeError("model unavailable")
        with mock.patch("core.ai_organizer.AIOrganizer", return_value=organizer):
            worker = BatchAnalysisWorker("organize", ["a.jpg"])
            self._run(worker)

        self.assertEqual(worker.error, "model unavailable")
        organizer.close.assert_called_once_with()

    def test_cancel_requested_during_action_is_forwarded(self):
        worker = BatchAnalysisWorker("classify", ["a.jpg"], "out")
        entered = threading.Event()
        release = threading.Event()

        def fake_auto_organize(paths, target, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            return {"cancelled": kwargs["cancelled"](), "success": 0,
                    "failed": 0, "errors": [], "categories": {}}

        with mock.patch("core.auto_organizer.auto_organize", fake_auto_organize):
            worker.start()
            self.assertTrue(entered.wait(2))
            worker.requestInterruption()
            release.set()
            self.assertTrue(worker.wait(3000))
        self.app.processEvents()
        self.assertTrue(worker.result["cancelled"])

    def test_analysis_busy_guard_checks_other_workers_and_supports_exclusion(self):
        batch = mock.Mock()
        batch.isRunning.return_value = True
        scan = mock.Mock()
        scan.isRunning.return_value = False
        window = SimpleNamespace(
            _batch_analysis_worker=batch,
            _ai_analysis_worker=None,
            _scan_worker=scan,
            _ai_pick_worker=None,
            _pending_worker=None,
            _group_pages={},
        )
        self.assertTrue(MainWindow._analysis_task_busy(window))
        self.assertFalse(MainWindow._analysis_task_busy(
            window, exclude_attr="_batch_analysis_worker"))
        batch.isRunning.return_value = False
        scan.isRunning.return_value = True
        self.assertTrue(MainWindow._analysis_task_busy(
            window, exclude_attr="_batch_analysis_worker"))

    def test_ai_organizer_forwards_identity_progress_for_cancellation(self):
        state = {"cancel": False}
        identity = mock.Mock()

        def analyze_folder(paths, progress_callback=None):
            progress_callback(1, 2, "first image")
            state["cancel"] = True
            progress_callback(2, 2, "second image")

        identity.analyze_folder.side_effect = analyze_folder
        organizer = AIOrganizer()
        organizer._identity_manager = identity
        organizer._cancelled = lambda: state["cancel"]
        result = organizer._step_characters(
            [{"path": "photo.jpg", "category": "人物"}], None)
        self.assertEqual(result, {"cancelled": True})
        identity.analyze_folder.assert_called_once()

    def test_ai_organizer_keeps_single_image_failure_details(self):
        organizer = AIOrganizer()
        organizer.cache = mock.Mock()
        organizer.cache.get.return_value = None
        organizer._classifier = mock.Mock()
        organizer._classifier.analyze.side_effect = RuntimeError("decode failed")
        result = organizer._step_classify(["broken.jpg"], None)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["errors"], ["broken.jpg: decode failed"])


if __name__ == "__main__":
    unittest.main()
