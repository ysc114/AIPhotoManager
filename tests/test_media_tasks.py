"""Background media task lifecycle and photo-pipeline handoff."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from core.image_loader import load_images_from_folder
from core.video_frames import extract_video_frames
from ui.main_window_v3 import MainWindow
from ui.media_tasks import _UpscaleWorker, _VideoFramesWorker


class MediaTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_workers_report_errors_without_crashing(self):
        video = _VideoFramesWorker([self.root / "missing.mp4"], 1)
        video_results = []
        video.done.connect(video_results.append)
        video.start()
        self.assertTrue(video.wait(3000))
        self.app.processEvents()
        self.assertEqual(len(video_results[0]["errors"]), 1)

        upscale = _UpscaleWorker([self.root / "missing.png"], 2)
        upscale_results = []
        upscale.done.connect(upscale_results.append)
        upscale.start()
        self.assertTrue(upscale.wait(3000))
        self.app.processEvents()
        self.assertEqual(len(upscale_results[0]["errors"]), 1)

    def test_video_output_is_scannable_and_entered_into_pending_analysis(self):
        source = self.root / "clip.avi"
        writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"MJPG"),
                                 10, (32, 24))
        if not writer.isOpened():
            self.skipTest("MJPG encoder unavailable")
        for _ in range(12):
            writer.write(np.full((24, 32, 3), 90, dtype=np.uint8))
        writer.release()
        photos = self.root / "photos"
        result = extract_video_frames(source, 1, photos, self.root / "manifest.json")
        self.assertEqual(set(load_images_from_folder(str(photos))), set(result["frames"]))

        window = MainWindow()
        try:
            window._video_auto_analyze = True
            with mock.patch.object(window, "_start_analyze_selected") as analyze, \
                    mock.patch.object(QMessageBox, "information"):
                window._on_video_done({**result, "errors": []})
            self.assertEqual({Path(p) for p in window._pending_files},
                             {Path(p) for p in result["frames"]})
            analyze.assert_called_once()
        finally:
            window.close()

    def test_close_interrupts_video_worker_and_retains_safe_lifetime(self):
        window = MainWindow()
        def slow_extract(*args, cancelled=None, **kwargs):
            while not cancelled():
                time.sleep(0.01)
            return {"frames": [], "new": 0, "reused": 0,
                    "skipped": False, "cancelled": True}

        try:
            with mock.patch("core.video_frames.extract_video_frames", side_effect=slow_extract), \
                    mock.patch.object(QMessageBox, "information"):
                self.assertTrue(window._start_video_frames(["example.mp4"], 1))
                worker = window._video_worker
                self.assertTrue(window._shutdown_background_workers(3000))
                self.assertFalse(worker.isRunning())
                self.assertIsNone(window._video_worker)
        finally:
            window.close()

    def test_close_interrupts_upscale_worker(self):
        window = MainWindow()
        def slow_upscale(*args, cancelled=None, **kwargs):
            while not cancelled():
                time.sleep(0.01)
            return {"outputs": [], "new": 0, "reused": 0,
                    "errors": [], "cancelled": True, "output_dir": str(self.root)}

        try:
            with mock.patch("core.super_resolution.upscale_images", side_effect=slow_upscale), \
                    mock.patch.object(QMessageBox, "information"):
                self.assertTrue(window._start_upscale(["example.png"], 2))
                worker = window._upscale_worker
                self.assertTrue(window._shutdown_background_workers(3000))
                self.assertFalse(worker.isRunning())
                self.assertIsNone(window._upscale_worker)
        finally:
            window.close()

    def test_upscale_uses_visible_photo_after_filtering(self):
        from PIL import Image
        first = self.root / "first.png"
        second = self.root / "second.png"
        for path in (first, second):
            Image.new("RGB", (8, 8), "red").save(path)
        window = MainWindow()
        try:
            window.image_list = [str(first), str(second)]
            window._populate_photo_list([str(second)])
            with mock.patch.object(QDialog, "exec", return_value=QDialog.Accepted), \
                    mock.patch.object(window, "_start_upscale") as start:
                window.super_resolution()
            self.assertEqual(start.call_args.args[0], [str(second)])
        finally:
            window.close()

    def test_filename_filter_preview_uses_selected_visible_photo(self):
        from PIL import Image
        first = self.root / "first.png"
        second = self.root / "second.png"
        Image.new("RGB", (8, 8), "red").save(first)
        Image.new("RGB", (8, 8), "blue").save(second)
        window = MainWindow()
        try:
            window.image_list = [str(first), str(second)]
            window._populate_photo_list(window.image_list)
            window.search_edit.setText("second")
            window.search_images()
            self.assertEqual(window.image_list_widget.count(), 1)
            self.assertEqual(Path(window._preview_path), second)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
