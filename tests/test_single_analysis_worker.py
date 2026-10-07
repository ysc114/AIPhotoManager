"""Release checks for the single-photo AI analysis QThread."""

import os
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ui.analysis_workers import SingleImageAnalysisWorker


class SingleImageAnalysisWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _wait_until(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(predicate(), "Worker did not report its terminal state")

    def test_analysis_runs_off_ui_thread_while_events_continue(self):
        ui_thread = threading.get_ident()
        observed = {}

        def analyze(path):
            observed["thread"] = threading.get_ident()
            time.sleep(0.12)
            return {"caption": path}

        worker = SingleImageAnalysisWorker("sample.jpg")
        results = []
        ticks = []
        worker.done.connect(lambda path, result: results.append((path, result)))
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(True))
        timer.start()
        with mock.patch("core.ai_classifier.AIClassifier.analyze", side_effect=analyze):
            worker.start()
            self._wait_until(lambda: bool(results))
            worker.wait(1000)
        timer.stop()

        self.assertNotEqual(observed.get("thread"), ui_thread)
        self.assertGreater(len(ticks), 0)
        self.assertEqual(results, [("sample.jpg", {"caption": "sample.jpg"})])

    def test_model_failure_is_reported(self):
        worker = SingleImageAnalysisWorker("broken.jpg")
        failures = []
        worker.failed.connect(lambda path, error: failures.append((path, error)))
        with mock.patch("core.ai_classifier.AIClassifier.analyze",
                        side_effect=RuntimeError("model unavailable")):
            worker.start()
            self._wait_until(lambda: bool(failures))
            worker.wait(1000)

        self.assertEqual(failures, [("broken.jpg", "model unavailable")])

    def test_interruption_is_reported_as_cancelled_after_inference_returns(self):
        worker = SingleImageAnalysisWorker("sample.jpg")
        started_inference = threading.Event()
        cancelled = []

        def analyze(_path):
            started_inference.set()
            time.sleep(0.1)
            return {"caption": "done"}

        worker.cancelled.connect(cancelled.append)
        with mock.patch("core.ai_classifier.AIClassifier.analyze", side_effect=analyze):
            worker.start()
            self.assertTrue(started_inference.wait(1))
            worker.requestInterruption()
            self._wait_until(lambda: bool(cancelled))
            worker.wait(1000)

        self.assertEqual(cancelled, ["sample.jpg"])


if __name__ == "__main__":
    unittest.main()
