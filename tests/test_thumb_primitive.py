# -*- coding: utf-8 -*-
"""缩略图缓存优先原语：命中直接返回，未命中投递后台任务（不解码原图）。"""
import os
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

PHOTOS = Path(__file__).resolve().parents[1] / "photos"
PHOTO_A = str(PHOTOS / "20260604_091343.jpg")


class ThumbCachePixmapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.main_window_v3 import MainWindow
        self.win = MainWindow()
        self.win._ui_ready = True

    def tearDown(self):
        self.win.close()

    def test_cache_hit_returns_pixmap_without_request(self):
        with mock.patch("core.thumbnail_cache.thumbnail_cache.get_cached",
                        return_value=PHOTO_A), \
                mock.patch("core.thumbnail_cache.thumbnail_cache.request") as req:
            pix = self.win._thumb_cache_pixmap(PHOTO_A, 128)
        self.assertFalse(pix.isNull(), "缓存命中应直接返回 QPixmap")
        self.assertFalse(req.called, "命中时不应再投递后台任务")

    def test_cache_miss_queues_request_for_callback(self):
        called = []
        with mock.patch("core.thumbnail_cache.thumbnail_cache.get_cached",
                        return_value=None), \
                mock.patch("core.thumbnail_cache.thumbnail_cache.request") as req:
            pix = self.win._thumb_cache_pixmap(
                PHOTO_A, 128, on_ready=lambda p: called.append(p))
        self.assertTrue(pix.isNull(), "未命中返回空 QPixmap（调用方先占位）")
        self.assertTrue(req.called, "未命中应投递后台生成任务")
        args = req.call_args[0]
        self.assertEqual(args[1], 128, "尺寸应原样透传")

    def test_no_callback_on_miss_does_not_queue(self):
        with mock.patch("core.thumbnail_cache.thumbnail_cache.get_cached",
                        return_value=None), \
                mock.patch("core.thumbnail_cache.thumbnail_cache.request") as req:
            self.win._thumb_cache_pixmap(PHOTO_A, 128)
        self.assertFalse(req.called, "没有回调时不需要后台任务")


if __name__ == "__main__":
    unittest.main()
