"""视觉索引维护 UI 冒烟：入库后自动增量 / 并发去重 / 失败不弹窗 / 状态行。"""

import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from config.settings_manager import settings as S


class _FakeIndexWorker(QObject):
    """替代 _SemanticBuildWorker：不跑真实线程，由测试手动触发信号。"""

    progress_updated = Signal(int, int)
    finished_build = Signal(dict)
    failed = Signal(str)

    instances = []

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        _FakeIndexWorker.instances.append(self)

    def start(self):
        self._running = True

    def isRunning(self):
        return self._running

    def finish(self, stats=None):
        self._running = False
        self.finished_build.emit(
            stats or {"new": 3, "skipped_existing": 1, "total": 4})

    def fail(self, err="boom"):
        self._running = False
        self.failed.emit(err)


class VisualIndexAutoUpdateTests(unittest.TestCase):
    """入库完成后自动增量：只触发一次、可关、失败不打断主流程。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.main_window_v3 import MainWindow
        self.win = MainWindow()
        self.win._ui_ready = True
        _FakeIndexWorker.instances = []
        self._patch = mock.patch(
            "ui.main_window_v3._SemanticBuildWorker", _FakeIndexWorker)
        self._patch.start()
        self.orig_auto = S.get("data.auto_update_visual_index")
        S.set("data.auto_update_visual_index", True)

    def tearDown(self):
        S.set("data.auto_update_visual_index", self.orig_auto)
        self._patch.stop()
        self.win.close()

    def test_default_setting_is_on(self):
        self.assertTrue(bool(S.get("data.auto_update_visual_index")))

    def test_analyze_done_starts_single_update(self):
        with mock.patch.object(self.win, "_show_analyze_summary"):
            self.win._on_analyze_done({"scanned": 2, "new": 2})
            self.assertEqual(len(_FakeIndexWorker.instances), 1)
            self.assertTrue(_FakeIndexWorker.instances[0].isRunning())
            # 已有任务在跑 → 不重复启动
            self.win._on_analyze_done({"scanned": 1, "new": 1})
            self.assertEqual(len(_FakeIndexWorker.instances), 1)

    def test_auto_update_can_be_disabled(self):
        S.set("data.auto_update_visual_index", False)
        with mock.patch.object(self.win, "_show_analyze_summary"):
            self.win._on_analyze_done({})
        self.assertEqual(_FakeIndexWorker.instances, [])

    def test_update_after_worker_finished_starts_again(self):
        self.win._update_visual_index_async()
        _FakeIndexWorker.instances[-1].finish({"new": 0})
        self.assertTrue(self.win._update_visual_index_async())
        self.assertEqual(len(_FakeIndexWorker.instances), 2)

    def test_failure_is_non_blocking(self):
        self.win._update_visual_index_async()
        worker = _FakeIndexWorker.instances[-1]
        with mock.patch.object(QMessageBox, "critical") as critical:
            worker.fail("faiss boom")
        self.assertFalse(critical.called, "索引更新失败不得弹模态框")
        self.assertIn("视觉索引更新失败",
                      self.win.statusBar().currentMessage())

    def test_success_refreshes_settings_status(self):
        self.win._update_visual_index_async()
        worker = _FakeIndexWorker.instances[-1]
        with mock.patch.object(self.win, "_refresh_settings_index_status") as refresh:
            worker.finish({"new": 5, "skipped_existing": 0})
        self.assertTrue(refresh.called)
        self.assertIn("视觉索引已更新", self.win.statusBar().currentMessage())

    def test_similar_search_waits_while_index_updating(self):
        self.win._update_visual_index_async()
        with mock.patch("ui.main_window_v3._SimilarSearchWorker") as worker_cls:
            self.win._find_similar_photos()
        self.assertFalse(worker_cls.called, "索引更新中不应并发启动相似搜索")
        self.assertIn("视觉索引正在更新",
                      self.win.statusBar().currentMessage())


class SettingsIndexStatusTests(unittest.TestCase):
    """设置中心索引状态行 / 按钮文案切换。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.settings_center import SettingsCenterPage
        self.page = SettingsCenterPage(win=None)

    def tearDown(self):
        self.page.close()

    def _base(self, **kw):
        st = {"exists": False, "indexed": 0, "photos_total": 10,
              "model_name": "", "pretrained": "", "dimension": 0,
              "updated_at": None, "state": "missing"}
        st.update(kw)
        return st

    def test_missing_state(self):
        with mock.patch("core.visual_search.read_index_status",
                        return_value=self._base()):
            self.page.refresh_index_status()
        self.assertIn("尚未建立", self.page._index_status_label.text())
        self.assertIn("更新视觉索引", self.page._index_btn.text())
        self.assertFalse(self.page._index_needs_rebuild())

    def test_ready_state_shows_counts(self):
        with mock.patch("core.visual_search.read_index_status",
                        return_value=self._base(
                            state="ready", exists=True, indexed=194,
                            photos_total=194, model_name="ViT-L-14",
                            pretrained="datacomp_xl_s13b_b90k",
                            dimension=768, updated_at=1700000000.0)):
            self.page.refresh_index_status()
        text = self.page._index_status_label.text()
        self.assertIn("已索引 194/194", text)
        self.assertIn("ViT-L-14", text)
        self.assertFalse(self.page._index_needs_rebuild())

    def test_mismatch_state_switches_to_rebuild(self):
        with mock.patch("core.visual_search.read_index_status",
                        return_value=self._base(
                            state="mismatch", exists=True, indexed=10,
                            model_name="ViT-B-32", pretrained="x",
                            dimension=512, updated_at=1700000000.0)):
            self.page.refresh_index_status()
        self.assertIn("需要重建索引", self.page._index_status_label.text())
        self.assertIn("重建视觉索引", self.page._index_btn.text())
        self.assertTrue(self.page._index_needs_rebuild())

    def test_rebuild_cancel_keeps_index(self):
        self.page._set_index_btn_rebuild(True)
        with mock.patch("core.visual_search.clear_index_files") as clear, \
                mock.patch.object(QMessageBox, "question",
                                  return_value=QMessageBox.No):
            self.page._update_visual_index()
        self.assertFalse(clear.called, "取消确认后不得删除索引缓存")


if __name__ == "__main__":
    unittest.main()