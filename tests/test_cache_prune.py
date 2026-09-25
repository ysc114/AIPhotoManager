# -*- coding: utf-8 -*-
"""失效缓存清理测试：只清缓存键，不删任何照片文件。"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox


class AnalysisCachePruneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prune_")
        self.cache_file = os.path.join(self.tmp, "analysis_cache.json")
        self.alive = os.path.join(self.tmp, "alive.jpg")
        Path(self.alive).write_bytes(b"x")
        self.gone = os.path.join(self.tmp, "gone.jpg")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cache(self):
        # 显式传路径：绝不触碰生产 analysis_cache.json
        from core.analysis_cache import AnalysisCache
        c = AnalysisCache(cache_file=self.cache_file)
        c.set(self.alive, {"category": "x"})
        c.set(self.gone, {"category": "y"})
        c._cache[""] = {}              # 空键（历史脏数据）
        return c

    def test_stale_count_and_prune(self):
        c = self._cache()
        self.assertEqual(c.stale_count(), 2, "1 个文件不存在 + 1 个空条目")
        r = c.prune_missing()
        self.assertEqual(r["stale"], 1)
        self.assertEqual(r["empty"], 1)
        self.assertEqual(r["kept"], 1)
        alive_key = self.alive.replace("\\", "/")     # 缓存键统一正斜杠
        gone_key = self.gone.replace("\\", "/")
        self.assertIn(alive_key, c._cache)
        self.assertNotIn(gone_key, c._cache)
        # 重新从磁盘加载：清理结果已落盘
        from core.analysis_cache import AnalysisCache
        again = AnalysisCache(cache_file=self.cache_file)
        self.assertEqual(list(again._cache), [alive_key])

    def test_prune_noop_when_clean(self):
        c = self._cache()
        c.prune_missing()
        again = c.prune_missing()
        self.assertEqual(again["removed"], 0)


class VisualIndexPruneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vprune_")
        self.index_path = os.path.join(self.tmp, "visual_similarity.json")
        self.a = os.path.join(self.tmp, "a.jpg")
        self.b = os.path.join(self.tmp, "b.jpg")
        for p in (self.a, self.b):
            Path(p).write_bytes(b"x")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _index(self):
        from core.visual_duplicates import VisualDuplicateIndex
        idx = VisualDuplicateIndex(photos_dir=self.tmp, index_path=self.index_path)
        idx._files[self.a] = {"mtime_ns": 1, "size": 1, "dhash": "aa"}
        idx._files[self.b] = {"mtime_ns": 1, "size": 1, "dhash": "bb"}
        idx._ignored = [[self.a, self.b]]
        idx.save()
        return idx

    def test_prune_removes_missing_keeps_decisions(self):
        idx = self._index()
        os.remove(self.b)
        self.assertEqual(idx.stale_count(), 1)
        r = idx.prune_missing()
        self.assertEqual(r["missing"], 1)
        self.assertEqual(r["kept"], 1)
        self.assertIn(self.a, idx._files)
        self.assertNotIn(self.b, idx._files)
        self.assertEqual(idx._ignored, [[self.a, self.b]],
                         "人工「不是同一角色/保留」判定不应被清理")
        from core.visual_duplicates import VisualDuplicateIndex
        again = VisualDuplicateIndex(photos_dir=self.tmp, index_path=self.index_path)
        self.assertEqual(list(again._files), [self.a])


class SettingsCleanCacheTests(unittest.TestCase):
    """设置页按钮：统计条数 → 二次确认 → 清理两个缓存并提示结果。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_clean_cache_prunes_both(self):
        from ui.settings_center import SettingsCenterPage
        page = SettingsCenterPage(win=None)
        try:
            fake_cache = mock.Mock()
            fake_cache.stale_count.return_value = 3
            fake_cache.prune_missing.return_value = {"removed": 3}
            fake_visual = mock.Mock()
            fake_visual.stale_count.return_value = 5
            fake_visual.prune_missing.return_value = {"missing": 5, "kept": 250}
            with mock.patch("core.analysis_cache.get_cache",
                            return_value=fake_cache), \
                    mock.patch("core.visual_duplicates.VisualDuplicateIndex",
                               return_value=fake_visual), \
                    mock.patch.object(page, "refresh_data_stats"), \
                    mock.patch.object(QMessageBox, "question",
                                      return_value=QMessageBox.Yes):
                page._clean_cache()
            self.assertTrue(fake_cache.prune_missing.called)
            self.assertTrue(fake_visual.prune_missing.called)
            self.assertIn("3", page._backup_status.text())
            self.assertIn("5", page._backup_status.text())
        finally:
            page.close()

    def test_clean_cache_cancel_does_nothing(self):
        from ui.settings_center import SettingsCenterPage
        page = SettingsCenterPage(win=None)
        try:
            fake_cache = mock.Mock()
            fake_cache.stale_count.return_value = 2
            fake_visual = mock.Mock()
            fake_visual.stale_count.return_value = 4
            with mock.patch("core.analysis_cache.get_cache",
                            return_value=fake_cache), \
                    mock.patch("core.visual_duplicates.VisualDuplicateIndex",
                               return_value=fake_visual), \
                    mock.patch.object(QMessageBox, "question",
                                      return_value=QMessageBox.No):
                page._clean_cache()
            self.assertFalse(fake_cache.prune_missing.called)
            self.assertFalse(fake_visual.prune_missing.called)
        finally:
            page.close()


if __name__ == "__main__":
    unittest.main()
