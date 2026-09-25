# -*- coding: utf-8 -*-
"""UI Phase 3 测试（收藏/待处理/设置页结构）。"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QPushButton

from ui.main_window_v3 import MainWindow


class Phase3UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()   # 让 Qt 在 QApplication 存活时析构，
        self.app.processEvents()    # 避免退出期原生崩溃（0xC0000409）

    def test_favorites_page_built(self):
        self.assertTrue(hasattr(self.window, "favorites_page"))
        self.assertTrue(hasattr(self.window, "_load_favorites_page"))
        self.assertTrue(hasattr(self.window, "_toggle_favorite_current"))

    def test_pending_page_stats_label(self):
        self.assertTrue(hasattr(self.window, "_pending_stats_label"))
        self.assertTrue(hasattr(self.window, "_refresh_pending_stats"))

    def test_settings_page_built(self):
        self.assertTrue(hasattr(self.window, "settings_page"))
        self.assertTrue(hasattr(self.window, "_refresh_settings_page"))
        # 设置中心：关键功能按钮在位（备份/刷新统计/打开目录）
        btns = [b.text() for b in self.window.settings_page.findChildren(QPushButton)]
        for expected in ("💾 立即备份", "📂 打开备份目录", "🔄 刷新统计", "📡 重新扫描新照片"):
            self.assertIn(expected, btns, f"设置中心缺少按钮: {expected}")
        # 不提供危险的全量重聚按钮
        self.assertNotIn("重新聚类全部照片", btns)

    def test_nav_rows_map_to_real_pages(self):
        # 新版导航（AI精选提升为一级）：AI精选=1, 收藏=6, 待处理=7, 设置=8
        self.window._switch_page(1)
        self.assertIs(
            self.window.content_stack.currentWidget(),
            self.window.ai_pick_page,
        )
        self.window._switch_page(6)
        self.assertIs(
            self.window.content_stack.currentWidget(),
            self.window.favorites_page,
        )
        self.window._switch_page(7)
        self.assertIs(
            self.window.content_stack.currentWidget(),
            self.window.pending_page,
        )
        self.window._switch_page(8)
        self.assertIs(
            self.window.content_stack.currentWidget(),
            self.window.settings_page,
        )
        # 页 9：重复照片（经典模式左侧导航同样可达）
        self.window._switch_page(9)
        self.assertIs(
            self.window.content_stack.currentWidget(),
            self.window.duplicates_page,
        )
        # 导航项数量与顺序（10 项 = 内容栈页数）
        items = [self.window.nav_list.item(i).text() for i in range(self.window.nav_list.count())]
        self.assertEqual(len(items), 10)
        self.assertIn("AI精选", items[1])
        self.assertIn("重复照片", items[9])

    def test_photo_page_favorite_button(self):
        btns = [b.text() for b in self.window.photo_page.findChildren(QPushButton)]
        self.assertIn("⭐ 收藏当前", btns)

    def test_settings_version_info_is_lazy(self):
        """版本信息惰性填充：构造设置页不 spawn git、不连库；首次刷新才读。"""
        from unittest import mock
        from ui.settings_center import SettingsCenterPage
        with mock.patch.object(SettingsCenterPage, "_git_commit",
                               return_value="abc1234") as git, \
                mock.patch.object(SettingsCenterPage, "_schema_version",
                                  return_value=2) as schema:
            page = SettingsCenterPage(win=None)
            try:
                self.assertFalse(git.called, "构造设置页不应调用 git")
                self.assertFalse(schema.called, "构造设置页不应连库")
                page.refresh_version_info()
                self.assertEqual(git.call_count, 1)
                labels = {k: v.text() for k, v in page._info_labels.items()}
                self.assertIn("abc1234", labels["Git commit"])
                self.assertIn("Schema v2", labels["数据库"])
                # 第二次刷新复用缓存，不再 spawn git
                page.refresh_version_info()
                self.assertEqual(git.call_count, 1)
            finally:
                page.close()

    def test_rescan_switches_to_pending_page(self):
        """设置页「重新扫描新照片」→ 跳到待处理页（回归：曾写死 6=收藏页）。"""
        from unittest import mock
        win = self.window
        with mock.patch.object(win, "_scan_photos_dir") as scan, \
                mock.patch.object(win, "_switch_page") as sw:
            win.settings_center._rescan()
        self.assertTrue(scan.called, "应触发扫描")
        self.assertEqual(sw.call_args[0][0],
                         win.content_stack.indexOf(win.pending_page),
                         "应切到待处理页")

    def test_bottom_nav_dispatches_switch_page_once(self):
        """底部导航每次点击只派发一次切页（此前 setCurrentRow + 显式调用跑两遍）。"""
        from unittest import mock
        win = self.window
        with mock.patch.object(win, "_switch_page") as sw:
            win._on_bottom_nav_changed(7)
        self.assertEqual(sw.call_count, 1, "每次点击只应派发一次")
        self.assertEqual(win.nav_list.currentRow(), 7, "左侧导航应同步到同一行")

    def test_reentry_refreshes_duplicates_once_per_entry(self):
        """重复照片页每次进入恰好扫描一次（首次 1 次 + 再进入 1 次）。"""
        win = self.window
        dup_row = win.content_stack.indexOf(win.duplicates_page)
        calls = []
        orig = win.duplicates_page.refresh

        def counted():
            calls.append(1)
            return orig()

        win.duplicates_page.refresh = counted
        try:
            win._on_bottom_nav_changed(dup_row)
            win._on_bottom_nav_changed(0)
            win._on_bottom_nav_changed(dup_row)
        finally:
            win.duplicates_page.refresh = orig
        self.assertEqual(len(calls), 2, f"应恰好扫描 2 次，实际 {len(calls)}")

    def test_photo_actions_use_latest_preview(self):
        """回归：AI 分析 A 后切到 B，同框角色/相似搜索应针对 B。"""
        from pathlib import Path
        from unittest import mock
        import ui.main_window_v3 as mw

        photos = sorted((Path(__file__).resolve().parents[1] / "photos").glob("*.png"))
        if len(photos) < 2:
            self.skipTest("photos/ 不足两张 PNG")
        a, b = str(photos[0]), str(photos[1])

        win = self.window
        win.image_list = [a, b]
        win.current_image_path = a          # 模拟刚分析过 A 的残留状态
        win._populate_photo_list([a, b], select=1)
        win.show_preview(1)
        self.app.processEvents()
        self.assertEqual(win._current_photo_path(), b.replace("\\", "/"))

        roles_seen = []
        win._photo_roles_refs = lambda p: (roles_seen.append(p), [])[1]
        try:
            win._show_photo_roles()          # refs 为空 → 只刷状态栏
        finally:
            del win._photo_roles_refs
        self.assertEqual(len(roles_seen), 1)
        self.assertEqual(os.path.basename(roles_seen[0]), os.path.basename(b),
                         "应查询当前预览的 B")

        sim_seen = []

        class _Sig:
            def connect(self, *_args, **_kwargs):
                pass

        class _FakeWorker:
            def __init__(self, p):
                sim_seen.append(p)
                self.progress_updated = _Sig()
                self.done_sig = _Sig()
                self.failed = _Sig()

            def start(self):
                pass

            def isRunning(self):
                return False

        with mock.patch.object(mw, "_SimilarSearchWorker", _FakeWorker):
            win._find_similar_photos()
        self.assertEqual(len(sim_seen), 1)
        self.assertEqual(os.path.basename(sim_seen[0]), os.path.basename(b),
                         "相似搜索种子应是当前预览的 B")

    def test_submit_feedback_rejects_switched_photo(self):
        """回归：分析 A 后切到 B 再提交 → 拒绝，不污染反馈数据。"""
        from pathlib import Path
        from unittest import mock

        photos = sorted((Path(__file__).resolve().parents[1] / "photos").glob("*.png"))
        if len(photos) < 2:
            self.skipTest("photos/ 不足两张 PNG")
        a, b = str(photos[0]), str(photos[1])

        win = self.window
        win.image_list = [a, b]
        win._populate_photo_list([a, b], select=1)
        win.show_preview(1)
        self.app.processEvents()
        win.current_image_path = a          # 刚分析的是 A
        win.current_ai_category = "人物"
        combo = mock.MagicMock()          # 轻量桩：避免无父 Qt 对象析构崩溃
        combo.currentText.return_value = "人物"
        win.feedback_combo = combo

        saved = []
        win.advisor.save_feedback = lambda *args: saved.append(args)
        win.start_ai_analysis = lambda: None

        with mock.patch("ui.main_window_v3.QMessageBox") as box:
            win.submit_feedback()
            self.assertEqual(saved, [], "切换照片后不得写入反馈")
            self.assertTrue(box.information.called, "应给出明确提示")

            win.show_preview(0)             # 回到 A → 同一张，允许提交
            self.app.processEvents()
            win.submit_feedback()
            self.assertEqual(len(saved), 1, "同一张照片应允许提交")
            self.assertTrue(str(saved[0][0]).endswith(os.path.basename(a)))

    def test_close_waits_background_workers(self):
        """回归：关窗应等后台线程结束（QThread 未结束退出会崩）。"""
        from PySide6.QtCore import QThread

        class _Slow(QThread):
            def run(self):
                self.msleep(600)

        win = self.window
        slow = _Slow(win)
        win._health_worker = slow
        slow.start()
        self.assertTrue(slow.isRunning())
        win.close()
        self.assertFalse(slow.isRunning(), "关窗应等到线程结束")
        self.assertIsNone(getattr(win, "_health_worker", None))

    def test_no_health_check_after_close(self):
        """回归：关窗后延时定时器不得再启动后台体检线程。"""
        win = self.window
        win.close()
        win._startup_health_check()          # 模拟 2.5s 延时定时器晚到
        self.assertIsNone(getattr(win, "_health_worker", None),
                          "关窗后不应再启动体检线程")


if __name__ == "__main__":
    unittest.main()
