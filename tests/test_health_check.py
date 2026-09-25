# -*- coding: utf-8 -*-
"""数据体检测试：全部临时目录，绝不触碰生产库/缓存。"""
import os
import shutil
import tempfile
import time
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

    def _fresh_backup(self):
        """造一个比库更新的备份（备份项判 ok 用）。"""
        d = Path(self.tmp) / "backups" / "autobackup_test"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "identity_db.sqlite"
        f.write_bytes(b"backup")
        now = time.time()
        os.utime(f, (now, now))
        if os.path.exists(self.db_path):
            old = now - 3600
            os.utime(self.db_path, (old, old))
        return f

    def _run(self, **kw):
        kw.setdefault("photos_dir", self.photos)
        kw.setdefault("db_path", self.db_path)
        kw.setdefault("analysis_cache_file", self.cache_file)
        kw.setdefault("visual_index_path", self.index_path)
        kw.setdefault("visual_search_cache_dir",
                      os.path.join(self.tmp, "vs_cache"))
        kw.setdefault("project_root", self.tmp)   # 同步残留项也隔离到临时目录
        return run_health_check(**kw)

    def test_all_healthy(self):
        p = self._photo("a.jpg")
        self._db([p])
        self._fresh_backup()
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
        self._fresh_backup()
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


class HealthActionButtonsTests(unittest.TestCase):
    """体检「一键修复」按钮：按结果启用/置灰，跳转复用既有导航。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _page(self):
        from ui.settings_center import SettingsCenterPage
        return SettingsCenterPage(win=None)

    @staticmethod
    def _result(**counts):
        def item(key, label, status, count=0, detail="", fix=""):
            return {"key": key, "label": label, "status": status,
                    "count": count, "detail": detail, "fix": fix}
        return {"items": [
            item("stale_analysis_cache", "分析缓存",
                 STATUS_WARN if counts.get("stale_analysis") else STATUS_OK,
                 counts.get("stale_analysis", 0)),
            item("stale_visual_fingerprints", "视觉指纹缓存",
                 STATUS_WARN if counts.get("stale_visual") else STATUS_OK,
                 counts.get("stale_visual", 0)),
            item("pending_photos", "photos/ 待入库",
                 STATUS_WARN if counts.get("pending") else STATUS_OK,
                 counts.get("pending", 0)),
            item("visual_search_index", "语义搜索索引",
                 STATUS_WARN if counts.get("index_warn") else STATUS_OK, 0,
                 detail="状态 mismatch：已索引 10/20"),
            item("duplicate_photos", "完全重复照片",
                 STATUS_WARN if counts.get("duplicates") else STATUS_OK,
                 counts.get("duplicates", 0)),
            item("backup_freshness", "数据备份",
                 STATUS_WARN if counts.get("backup_stale") else STATUS_OK,
                 counts.get("backup_stale", 0),
                 detail="最近备份 29 天前"),
        ], "warnings": 0, "errors": 0, "ok_count": 6}

    def test_buttons_disabled_without_issues(self):
        page = self._page()
        try:
            page._sync_health_actions(self._result())
            for key, btn in page._health_btns.items():
                self.assertFalse(btn.isEnabled(), f"{key} 无问题应置灰")
        finally:
            page.close()

    def test_buttons_enabled_with_counts(self):
        page = self._page()
        try:
            page._sync_health_actions(self._result(
                stale_analysis=3, stale_visual=5, pending=28,
                index_warn=1, duplicates=2, backup_stale=29))
            btns = page._health_btns
            self.assertTrue(btns["stale_caches"].isEnabled())
            self.assertIn("8", btns["stale_caches"].toolTip(), "3+5 条失效缓存")
            self.assertTrue(btns["pending"].isEnabled())
            self.assertIn("28", btns["pending"].toolTip())
            self.assertTrue(btns["index"].isEnabled())
            self.assertIn("mismatch", btns["index"].toolTip())
            self.assertTrue(btns["duplicates"].isEnabled())
            self.assertIn("2", btns["duplicates"].toolTip())

            self.assertTrue(btns["backup"].isEnabled(), "备份过期应可一键备份")
            self.assertIn("29 天前", btns["backup"].toolTip())
        finally:
            page.close()

    def test_backup_button_calls_backup(self):
        page = self._page()
        try:
            page._sync_health_actions(self._result(backup_stale=29))
            with mock.patch.object(page, "_do_backup_now") as backup:
                page._health_btns["backup"].click()
            self.assertTrue(backup.called, "一键备份应调用既有备份动作")
        finally:
            page.close()

    def test_goto_pages_uses_existing_navigation(self):
        from ui.main_window_v3 import MainWindow
        win = MainWindow()
        win._ui_ready = True
        page = None
        try:
            from ui.settings_center import SettingsCenterPage
            page = SettingsCenterPage(win=win)
            with mock.patch.object(win, "_switch_page") as sw:
                page._goto_pending_page()
                self.assertEqual(sw.call_args[0][0],
                                 win.content_stack.indexOf(win.pending_page))
                page._goto_duplicates_page()
                self.assertEqual(sw.call_args[0][0],
                                 win.content_stack.indexOf(win.duplicates_page))
        finally:
            if page is not None:
                page.close()
            win.close()

    def test_real_run_syncs_buttons_consistently(self):
        """真实库跑一次体检：按钮可用性与报告中的 warn 项一致。"""
        page = self._page()
        try:
            page._run_health_check()
            res = page._health_result
            pending = [i for i in res["items"] if i["key"] == "pending_photos"][0]
            self.assertEqual(
                page._health_btns["pending"].isEnabled(),
                pending["status"] == STATUS_WARN)
            stale = sum(int(i["count"] or 0) for i in res["items"]
                        if i["key"] in ("stale_analysis_cache",
                                        "stale_visual_fingerprints")
                        and i["status"] == STATUS_WARN)
            self.assertEqual(page._health_btns["stale_caches"].isEnabled(),
                             stale > 0)
        finally:
            page.close()


class BackupFreshnessTests(unittest.TestCase):
    """备份新鲜度：无备份/过期/库比备份更新 → warn；新鲜备份 → ok。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="backup_")
        self.db_path = os.path.join(self.tmp, "id.sqlite")
        Path(self.db_path).write_bytes(b"db")
        self.day = 86400

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _backup(self, age_days):
        d = Path(self.tmp) / "backups" / "autobackup_test"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "identity_db.sqlite"
        f.write_bytes(b"backup")
        ts = time.time() - age_days * self.day
        os.utime(f, (ts, ts))
        return f

    def _run(self):
        return run_health_check(
            photos_dir=os.path.join(self.tmp, "photos"),
            db_path=self.db_path,
            analysis_cache_file=os.path.join(self.tmp, "analysis_cache.json"),
            visual_index_path=os.path.join(self.tmp, "visual_similarity.json"),
            visual_search_cache_dir=os.path.join(self.tmp, "vs_cache"),
            include_duplicates=False,
            project_root=self.tmp)

    def test_no_backup_warns(self):
        item = _item(self._run(), "backup_freshness")
        self.assertEqual(item["status"], STATUS_WARN)
        self.assertIn("还没有任何数据库备份", item["detail"])
        self.assertIn("立即备份", item["fix"])

    def test_fresh_backup_is_ok(self):
        self._backup(1)
        db_ts = time.time() - 2 * self.day
        os.utime(self.db_path, (db_ts, db_ts))     # 库比备份旧 → 已覆盖
        item = _item(self._run(), "backup_freshness")
        self.assertEqual(item["status"], STATUS_OK, item["detail"])
        self.assertEqual(item["count"], 1)

    def test_stale_backup_warns(self):
        self._backup(30)
        item = _item(self._run(), "backup_freshness")
        self.assertEqual(item["status"], STATUS_WARN)
        self.assertIn("30 天前", item["detail"])

    def test_db_newer_than_backup_warns(self):
        self._backup(3)
        now = time.time()
        os.utime(self.db_path, (now, now))         # 备份之后库又改了
        item = _item(self._run(), "backup_freshness")
        self.assertEqual(item["status"], STATUS_WARN)
        self.assertIn("之后库又有改动", item["detail"])


class StartupHealthCheckTests(unittest.TestCase):
    """启动后台体检：有 warning/error 才在状态栏提示，正常时静默。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.main_window_v3 import MainWindow
        self.win = MainWindow()
        self.win._ui_ready = True

    def tearDown(self):
        self.win.close()

    @staticmethod
    def _result(warn=0, err=0):
        return {"items": [], "warnings": warn, "errors": err, "ok_count": 9}

    def test_silent_when_healthy(self):
        self.win.statusBar().clearMessage()
        self.win._on_health_check_done(self._result())
        self.assertEqual(self.win.statusBar().currentMessage(), "")
        self.assertTrue(self.win._health_snapshot)

    def test_hint_when_warnings(self):
        self.win._on_health_check_done(self._result(warn=2))
        msg = self.win.statusBar().currentMessage()
        self.assertIn("体检发现 2 项", msg)
        self.assertIn("数据体检", msg)

    def test_hint_mentions_errors(self):
        self.win._on_health_check_done(self._result(warn=1, err=1))
        msg = self.win.statusBar().currentMessage()
        self.assertIn("1 项待处理", msg)
        self.assertIn("1 项异常", msg)

    def test_start_starts_single_worker(self):
        started = []

        class _StubWorker:
            def __init__(self):
                self._running = False
                started.append(self)

            class _Sig:
                def connect(self, *a, **k):
                    pass

            done = _Sig()
            failed = _Sig()

            def start(self):
                self._running = True

            def isRunning(self):
                return self._running

        with mock.patch("ui.main_window_v3._HealthCheckWorker", _StubWorker):
            self.win._startup_health_check()
            self.win._startup_health_check()      # 已在跑 → 不重复启动
        self.assertEqual(len(started), 1)


class SyncPollutionTests(unittest.TestCase):
    """云同步残留：统计 .cfg 占位文件（.git 内的单独计数，.venv 不统计）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sync_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cfg(self, rel):
        p = Path(self.tmp) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
        return p

    def _run(self):
        # 复用同一套临时路径，避免读到生产文件
        photos = os.path.join(self.tmp, "photos")
        os.makedirs(photos, exist_ok=True)
        return run_health_check(
            photos_dir=photos,
            db_path=os.path.join(self.tmp, "id.sqlite"),
            analysis_cache_file=os.path.join(self.tmp, "analysis_cache.json"),
            visual_index_path=os.path.join(self.tmp, "visual_similarity.json"),
            visual_search_cache_dir=os.path.join(self.tmp, "vs_cache"),
            include_duplicates=False,
            project_root=self.tmp)

    def test_clean_project_reports_ok(self):
        item = _item(self._run(), "sync_pollution")
        self.assertEqual(item["status"], STATUS_OK)
        self.assertEqual(item["count"], 0)

    def test_counts_git_and_other_but_skips_venv(self):
        self._cfg(r".git\refs\main.baiduyun.uploading.cfg")
        self._cfg(r"cache\thumbnails\a.webp.baiduyun.uploading.cfg")
        self._cfg(r".venv\Lib\site-packages\x.py.baiduyun.uploading.cfg")
        item = _item(self._run(), "sync_pollution")
        self.assertEqual(item["status"], STATUS_WARN)
        self.assertEqual(item["count"], 2, ".venv 内的不计入")
        self.assertIn(".git 内 1 个", item["detail"])
        self.assertIn("排除出", item["fix"])


if __name__ == "__main__":
    unittest.main()
