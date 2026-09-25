# -*- coding: utf-8 -*-
"""数据体检测试：全部临时目录，绝不触碰生产库/缓存。"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from core.health_check import STATUS_OK, STATUS_WARN, format_report, run_health_check


def _item(result, key):
    for it in result["items"]:
        if it["key"] == key:
            return it
    raise AssertionError(f"缺少体检项 {key}")


class HealthCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="health_")
        self.photos = os.path.join(self.tmp, "photos")
        os.makedirs(self.photos, exist_ok=True)
        self.db_path = os.path.join(self.tmp, "id.sqlite")
        self.cache_file = os.path.join(self.tmp, "analysis_cache.json")
        self.index_path = os.path.join(self.tmp, "visual_similarity.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _photo(self, name):
        p = os.path.join(self.photos, name)
        Path(p).write_bytes(b"x")
        return p

    def _db(self, paths):
        from core.identity.database import IdentityDatabase
        db = IdentityDatabase(self.db_path)
        gid = db.create_group("", "fursuit_character")
        for i, p in enumerate(paths):
            db.conn.execute(
                "INSERT INTO identity_image (group_id, image_path,"
                " detection_index, embedding_type) VALUES (?,?,?,?)",
                (gid, p.replace("\\", "/"), i, "fursuit_fursee"))
        db.conn.commit()
        db.close()
        return gid

    def _run(self, **kw):
        kw.setdefault("photos_dir", self.photos)
        kw.setdefault("db_path", self.db_path)
        kw.setdefault("analysis_cache_file", self.cache_file)
        kw.setdefault("visual_index_path", self.index_path)
        kw.setdefault("visual_search_cache_dir",
                      os.path.join(self.tmp, "vs_cache"))
        return run_health_check(**kw)

    def test_all_healthy(self):
        p = self._photo("a.jpg")
        self._db([p])
        r = self._run()
        self.assertEqual(r["errors"], 0)
        self.assertEqual(r["warnings"], 0, format_report(r))
        self.assertEqual(_item(r, "db_missing_files")["status"], STATUS_OK)
        self.assertEqual(_item(r, "pending_photos")["status"], STATUS_OK)
        self.assertEqual(_item(r, "visual_search_index")["status"], STATUS_OK)

    def test_missing_file_and_orphan_group(self):
        p = os.path.join(self.photos, "gone.jpg")      # 库里引用但文件不存在
        self._db([p])
        r = self._run()
        mf = _item(r, "db_missing_files")
        self.assertEqual(mf["status"], STATUS_WARN)
        self.assertEqual(mf["count"], 1)
        orphan = _item(r, "orphan_groups")
        self.assertEqual(orphan["status"], STATUS_WARN)
        self.assertEqual(orphan["count"], 1, "该组唯一照片缺失 → 孤儿组")

    def test_stale_caches_detected(self):
        p = self._photo("a.jpg")
        self._db([p])
        from core.analysis_cache import AnalysisCache
        c = AnalysisCache(cache_file=self.cache_file)
        c.set(os.path.join(self.photos, "deleted.jpg"), {"category": "x"})
        from core.visual_duplicates import VisualDuplicateIndex
        vi = VisualDuplicateIndex(photos_dir=self.photos,
                                  index_path=self.index_path)
        vi._files[os.path.join(self.photos, "deleted.jpg").replace("\\", "/")] = {
            "mtime_ns": 1, "size": 1, "dhash": "aa"}
        vi.save()
        r = self._run(include_duplicates=False)
        self.assertEqual(_item(r, "stale_analysis_cache")["count"], 1)
        self.assertEqual(_item(r, "stale_analysis_cache")["status"], STATUS_WARN)
        self.assertEqual(_item(r, "stale_visual_fingerprints")["count"], 1)

    def test_pending_photos_detected(self):
        p = self._photo("a.jpg")
        self._db([p])
        self._photo("new_one.jpg")                     # 未入库
        r = self._run(include_duplicates=False)
        pending = _item(r, "pending_photos")
        self.assertEqual(pending["status"], STATUS_WARN)
        self.assertEqual(pending["count"], 1)
        self.assertIn("待处理", pending["fix"])

    def test_duplicate_photos_detected(self):
        a = self._photo("a.jpg")
        b = os.path.join(self.photos, "a (1).jpg")
        Path(b).write_bytes(b"x")                      # 与 a 内容一致
        self._db([a, b])
        r = self._run()
        dup = _item(r, "duplicate_photos")
        self.assertEqual(dup["status"], STATUS_WARN)
        self.assertEqual(dup["count"], 1, "2 个文件 → 多出 1 个副本")

    def test_format_report_lists_items_and_conclusion(self):
        p = self._photo("a.jpg")
        self._db([p])
        text = format_report(self._run())
        self.assertIn("数据库完整性", text)
        self.assertIn("体检结论", text)
        self.assertIn("全部正常", text)


class SettingsHealthUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_run_health_check_fills_label(self):
        from ui.settings_center import SettingsCenterPage
        page = SettingsCenterPage(win=None)
        try:
            fake = {"items": [{"key": "k", "label": "示例项", "status": STATUS_OK,
                               "count": 0, "detail": "一切正常", "fix": ""}],
                    "warnings": 0, "errors": 0, "ok_count": 1}
            with mock.patch("core.health_check.run_health_check",
                            return_value=fake):
                page._run_health_check()
            self.assertIn("示例项", page._health_label.text())
            self.assertIn("全部正常", page._health_label.text())
        finally:
            page.close()


if __name__ == "__main__":
    unittest.main()
