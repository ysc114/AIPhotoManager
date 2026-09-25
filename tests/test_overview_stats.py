# -*- coding: utf-8 -*-
"""总览统计口径：AI 已分析 = 库内照片数（缓存仅用于类别与置信度）。"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication


class _FakeCache:
    def __init__(self, entries):
        self._cache = entries


class _FakeConn:
    def __init__(self, count):
        self._count = count

    def execute(self, *args, **kwargs):
        class _Cur:
            def __init__(self, n):
                self._n = n

            def fetchone(self):
                return (self._n,)

        return _Cur(self._count)


class _FakeReader:
    def __init__(self, count):
        class _Db:
            conn = _FakeConn(count)

        self.db = _Db()

    def close(self):
        pass


def _entry(label_cn="兽装形象", quality=0.8):
    return {"category": "fursuit", "quality": quality,
            "layer1": {"label_cn": label_cn}}


class OverviewStatsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.main_window_v3 import MainWindow
        self.win = MainWindow()
        self.win._ui_ready = True

    def tearDown(self):
        self.win.close()

    def test_analyzed_uses_database_not_cache_entries(self):
        entries = {f"C:/x/{i}.jpg": _entry() for i in range(5)}
        with mock.patch("core.analysis_cache.get_cache",
                        return_value=_FakeCache(entries)), \
                mock.patch("core.identity.get_reader",
                           return_value=_FakeReader(2)):
            stats = self.win._compute_overview_stats()
        self.assertEqual(stats["analyzed"], 2, "应与设置页/体检一致：以库为准")
        self.assertEqual(stats["fursuit_photos"], 5, "类别统计仍来自缓存")
        self.assertAlmostEqual(stats["avg_quality"], 0.8, places=3)

    def test_falls_back_to_cache_when_db_unavailable(self):
        entries = {f"C:/x/{i}.jpg": _entry() for i in range(3)}
        with mock.patch("core.analysis_cache.get_cache",
                        return_value=_FakeCache(entries)), \
                mock.patch("core.identity.get_reader",
                           side_effect=RuntimeError("db down")):
            stats = self.win._compute_overview_stats()
        self.assertEqual(stats["analyzed"], 3, "库不可读时回退缓存计数")

    def test_person_and_fursuit_split(self):
        entries = {
            "C:/x/a.jpg": _entry("兽装形象"),
            "C:/x/b.jpg": _entry("普通人物"),
            "C:/x/c.jpg": _entry("其他"),
        }
        with mock.patch("core.analysis_cache.get_cache",
                        return_value=_FakeCache(entries)), \
                mock.patch("core.identity.get_reader",
                           return_value=_FakeReader(1)):
            stats = self.win._compute_overview_stats()
        self.assertEqual(stats["fursuit_photos"], 1)
        self.assertEqual(stats["person_photos"], 1)


if __name__ == "__main__":
    unittest.main()
