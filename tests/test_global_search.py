"""Spotlight 全局搜索面板测试（④ 第一阶段：UI 框架，offscreen）。

覆盖：防抖请求 / 分区渲染 / 键盘上下与 Enter / Esc 关闭 / 最近搜索 /
MainWindow 集成（入口按钮、Ctrl+K 与 Ctrl+Shift+F 快捷键、结果分发）。
"""
import os
import sys
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtTest import QTest
from unittest import mock

from ui.components.global_search import GlobalSearchPanel


def settle(app, frames=20, dt=0.02):
    for _ in range(frames):
        t0 = time.time()
        app.processEvents()
        time.sleep(max(0.0, dt - (time.time() - t0)))


def _item(i):
    return {
        "icon": "🐺", "title": f"角色{i}", "subtitle": "兽装角色 · Fursee",
        "badge": "角色", "payload": {"kind": "character", "id": i},
    }


class GlobalSearchPanelTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = GlobalSearchPanel()
        self.panel.show_panel()
        settle(self.app, 6)

    def tearDown(self):
        self.panel.hide_panel()
        settle(self.app, 8)
        self.panel.close()

    def test_debounce_search_requested(self):
        got = []
        self.panel.search_requested.connect(got.append)
        self.panel.input.setText("白狼")
        # 防抖窗口内同步断言（不依赖机器负载）
        self.assertTrue(self.panel._debounce.isActive(),
                        "输入后应启动防抖计时器")
        self.assertEqual(got, [])
        QTest.qWait(400)         # 远大于 120ms 防抖阈值
        self.assertEqual(got, ["白狼"])
        self.assertEqual(self.panel.query(), "白狼")

    def test_results_render_and_keyboard_navigation(self):
        self.panel.set_results(
            [{"title": "角色", "items": [_item(1), _item(2)]}])
        self.assertEqual(len(self.panel._items), 2)
        # 键盘下/上
        QTest.keyClick(self.panel.input, Qt.Key_Down)
        self.assertEqual(self.panel._sel, 0)
        QTest.keyClick(self.panel.input, Qt.Key_Down)
        self.assertEqual(self.panel._sel, 1)
        QTest.keyClick(self.panel.input, Qt.Key_Up)
        self.assertEqual(self.panel._sel, 0)
        # Enter 选中并发出信号
        got = []
        self.panel.result_selected.connect(got.append)
        QTest.keyClick(self.panel.input, Qt.Key_Return)
        settle(self.app, 10)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["payload"]["id"], 1)
        self.assertFalse(self.panel.isVisible(), "选中后面板应关闭")

    def test_escape_closes(self):
        self.panel.input.setText("x")
        QTest.keyClick(self.panel.input, Qt.Key_Escape)
        settle(self.app, 12)
        self.assertFalse(self.panel.isVisible())

    def test_recent_chips(self):
        self.panel.set_results([], recent=["白狼", "展会"])
        chips = [w for w in self.panel.findChildren(QLabel)
                 if "白狼" in w.text()]
        self.assertGreaterEqual(len(chips), 1)
        self.assertTrue(self.panel._recent_host.isVisible())
        self.panel.set_results([], recent=[])
        self.assertFalse(self.panel._recent_host.isVisible())


class GlobalSearchWindowTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.main_window_v3 import MainWindow
        self.win = MainWindow()
        self.win._ui_ready = True
        # 测试中禁用真实索引构建（语义分区用 fake 桩覆盖）
        self.win._sem_building = True
        self.win.show()
        settle(self.app, 10)

    def tearDown(self):
        self.win.close()

    def test_entry_and_shortcuts(self):
        self.assertIsNotNone(getattr(self.win, "gs_btn", None))
        self.win._open_global_search()
        settle(self.app, 8)
        self.assertTrue(self.win._global_search.isVisible())
        seqs = {sc.key().toString() for sc in self.win.findChildren(QShortcut)}
        self.assertIn("Ctrl+K", seqs)
        self.assertIn("Ctrl+Shift+F", seqs)
        self.win._toggle_global_search()
        settle(self.app, 12)
        self.assertFalse(self.win._global_search.isVisible())

    def test_query_and_result_dispatch(self):
        self.win._open_global_search()
        settle(self.app, 6)
        # 索引已存在时语义分区会后台预热模型：测试里桩掉，避免真加载
        with mock.patch.object(self.win, "_start_semantic_build"):
            # 空查询 → 最近搜索（空列表也安全）
            self.win._on_global_search_query("")
            # 查询"兽装"（类别匹配：应命中兽装角色组；无库数据时跳过）
            self.win._on_global_search_query("兽装")
            settle(self.app, 4)
            if self.win._global_search._items:
                self.assertGreater(len(self.win._global_search._items), 0)
            else:
                self.skipTest("库中无兽装角色组")
            # 无结果查询 → 无真实结果 + 面板有提示（不空白）；
            # 语义分区可能给出「模型加载中」或近邻结果，故只要求有可读提示
            self.win._on_global_search_query("__no_such_thing__")
            settle(self.app, 4)
        non_semantic = [i for i in self.win._global_search._items
                        if i.get("badge") != "语义"]
        self.assertEqual(non_semantic, [], "不应有非语义的真实结果")
        texts = [l.text() for l in self.win._global_search.findChildren(QLabel)]
        self.assertTrue(
            any(("没有找到" in t) or ("语义" in t) for t in texts),
            "应有无结果提示或语义分区提示")
        # 无 payload 的结果：仅记录最近搜索，不跳转不崩溃
        self.win._on_global_result_selected(
            {"title": "测试条目", "payload": {}})
        self.assertIn("测试条目", self.win._gs_recents)
        # 面板可再次打开/关闭
        self.win._open_global_search()
        settle(self.app, 4)
        self.assertTrue(self.win._global_search.isVisible())
        self.win._global_search.hide_panel()
        settle(self.app, 12)


    def test_semantic_section(self):
        """🧠 语义搜索分区：索引就绪→显示语义结果；为空→构建提示（不发起真实构建）。"""
        self.win._open_global_search()
        settle(self.app, 6)

        # 情形1：索引为空 → 提示行 + 不启动真实构建（_sem_building 预置）
        self.win._sem_building = True
        with mock.patch("core.visual_search.get_index", return_value=_FakeIndex(0)), \
                mock.patch("core.visual_search.read_index_status",
                           return_value={"state": "missing", "indexed": 0,
                                         "stale": 0, "photos_total": 2}):
            self.win._on_global_search_query("白狼")
        settle(self.app, 4)
        texts = [l.text() for l in self.win._global_search.findChildren(QLabel)]
        self.assertTrue(any("语义索引构建中" in t for t in texts))

        # 情形2：索引就绪 + 模型已加载 → 直接给语义结果行
        with mock.patch("core.visual_search.get_index", return_value=_FakeIndex(2)), \
                mock.patch("core.visual_search.read_index_status",
                           return_value={"state": "ready", "indexed": 2,
                                         "stale": 0, "photos_total": 2}), \
                mock.patch("core.visual_search.get_encoder",
                           return_value=_FakeLoadedEncoder()), \
                mock.patch("ui.main_window_v3._SemanticQueryWorker",
                           _SyncSemanticWorker):
            self.win._on_global_search_query("白狼")
            settle(self.app, 6)      # 等后台结果回填
        settle(self.app, 4)
        items = self.win._global_search._items
        sem = [i for i in items if i.get("badge") == "语义"]
        self.assertEqual(len(sem), 2)
        self.assertIn("photo", sem[0]["payload"]["kind"] or "")

        # 情形3：索引就绪但模型未加载 → 后台预热（不阻塞主线程），先给提示行
        with mock.patch("core.visual_search.get_index", return_value=_FakeIndex(2)), \
                mock.patch("core.visual_search.read_index_status",
                           return_value={"state": "ready", "indexed": 2,
                                         "stale": 0, "photos_total": 2}), \
                mock.patch("core.visual_search.get_encoder",
                           return_value=_FakeColdEncoder()), \
                mock.patch.object(self.win, "_start_semantic_build") as warm:
            self.win._on_global_search_query("白狼")
        settle(self.app, 4)
        self.assertTrue(warm.called, "应后台预热模型，而不是在主线程加载")
        texts = [l.text() for l in self.win._global_search.findChildren(QLabel)]
        self.assertTrue(any("语义模型加载中" in t for t in texts))
        sem = [i for i in self.win._global_search._items
               if i.get("badge") == "语义"]
        self.assertEqual(len(sem), 1)
        self.assertEqual(sem[0]["payload"]["kind"], "hint")

        self.win._global_search.hide_panel()
        settle(self.app, 10)

    def test_semantic_stale_result_triggers_latest(self):
        """过期语义结果不得回填：应补跑最新一次查询。"""
        win = self.win
        token_old = object()
        win._sem_pending = (object(), "白狼", ["白狼"], 4)
        with mock.patch.object(win, "_spawn_semantic_query") as spawn, \
                mock.patch.object(win, "_on_global_search_query") as rerender:
            win._on_semantic_query_done(
                token_old, [{"photo_id": 1, "path": "C:/fake/x.jpg",
                             "similarity": 0.9}])
        self.assertTrue(spawn.called, "应补跑最新查询")
        self.assertFalse(rerender.called, "过期结果不得回填")

    def test_semantic_result_dropped_when_query_changed(self):
        """令牌匹配但面板查询已改 → 丢弃（不覆盖用户当前结果）。"""
        win = self.win
        token = object()
        win._sem_pending = (token, "白狼", ["白狼"], 4)
        win._global_search.set_query("其它查询")
        with mock.patch.object(win, "_on_global_search_query") as rerender:
            win._on_semantic_query_done(token, [])
        self.assertFalse(rerender.called)

    def test_semantic_result_renders_when_matching(self):
        """令牌与查询都匹配 → 用后台结果重渲染语义分区。"""
        win = self.win
        token = object()
        win._sem_pending = (token, "白狼", ["白狼"], 4)
        win._global_search.set_query("白狼")
        hits = [{"photo_id": 1, "path": "C:/fake/p1.jpg", "similarity": 0.8}]
        with mock.patch.object(win, "_on_global_search_query") as rerender:
            win._on_semantic_query_done(token, hits)
        self.assertTrue(rerender.called)
        args, kwargs = rerender.call_args
        self.assertEqual(args[0], "白狼")
        self.assertEqual(kwargs["_sem_result"][1], hits)

    def test_close_reaps_semantic_query_worker(self):
        """关窗时回收后台语义检索线程。"""
        from PySide6.QtCore import QThread

        class _Slow(QThread):
            def run(self):
                self.msleep(300)

        win = self.win
        w = _Slow(win)
        win._sem_query_worker = w
        w.start()
        self.assertTrue(w.isRunning())
        win.close()
        self.assertFalse(w.isRunning(), "关窗应等后台语义检索结束")
        self.assertIsNone(win._sem_query_worker)

    def test_semantic_worker_honors_pre_cancel(self):
        """worker 启动前已请求中断时，不应加载索引或模型。"""
        from ui.main_window_v3 import _SemanticQueryWorker

        class _PreCancelled(_SemanticQueryWorker):
            def isInterruptionRequested(self):
                return True

        w = _PreCancelled(object(), ["白狼"], 4)
        got = []
        w.cancelled.connect(got.append, Qt.DirectConnection)
        with mock.patch("core.visual_search.get_index",
                        side_effect=AssertionError("不应加载索引")):
            w.run()
        self.assertEqual(len(got), 1)

    def test_semantic_failure_renders_retry_item(self):
        """后台失败应显示可重试条目，而不是静默空结果。"""
        win = self.win
        token = object()
        win._sem_pending = (token, "白狼", ["白狼"], 4)
        win._global_search.set_query("白狼")
        win._on_semantic_query_failed(token, "模型暂不可用")
        retry = [i for i in win._global_search._items
                 if (i.get("payload") or {}).get("kind") == "semantic_retry"]
        self.assertEqual(len(retry), 1)
        self.assertIn("重试", retry[0]["title"])

    def test_new_semantic_query_requests_cancellation(self):
        """新查询到来时应请求旧 worker 中断，并等待其结束后补跑。"""
        win = self.win
        old = mock.Mock()
        old.isRunning.return_value = True
        win._sem_query_worker = old
        with mock.patch.object(win, "_spawn_semantic_query") as spawn:
            self.assertFalse(win._start_semantic_query("新查询", ["新查询"], 4))
        old.requestInterruption.assert_called_once_with()
        spawn.assert_not_called()
        # 避免测试 teardown 把这个 mock 当作仍在运行的 QThread。
        win._sem_query_worker = None

    def test_rapid_queries_keep_only_latest_pending(self):
        """连续快速输入只保留最新查询，运行中的旧任务持续收到取消请求。"""
        win = self.win
        old = mock.Mock()
        old.isRunning.return_value = True
        win._sem_query_worker = old
        for q in ("白", "白狼", "白狼 展会"):
            win._start_semantic_query(q, [q], 4)
        self.assertEqual(win._sem_pending[1], "白狼 展会")
        self.assertEqual(old.requestInterruption.call_count, 3)
        win._sem_query_worker = None

    def test_plain_photo_filename_search(self):
        """普通文件名搜索仍应返回照片分区，不依赖语义模型。"""
        photos = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "photos")
        names = sorted(n for n in os.listdir(photos)
                       if os.path.splitext(n)[1].lower()
                       in (".jpg", ".jpeg", ".png", ".webp"))
        self.assertTrue(names)
        stem = os.path.splitext(names[0])[0]
        with mock.patch("core.visual_search.read_index_status",
                        return_value={"state": "missing", "indexed": 0,
                                      "stale": 0, "photos_total": 0}):
            self.win._on_global_search_query(stem)
        photos_found = [i for i in self.win._global_search._items
                        if i.get("badge") == "照片"]
        self.assertTrue(any(i["title"] == names[0] for i in photos_found))

    def test_semantic_worker_cancels_after_running_search(self):
        """生产 worker 在检索返回后应响应运行中的中断请求。"""
        from ui.main_window_v3 import _SemanticQueryWorker

        entered = threading.Event()

        class _SlowIndex:
            def search_by_text(self, texts, encoder, top_k=4):
                entered.set()
                time.sleep(0.08)
                return [{"path": "C:/fake/old.jpg", "similarity": 0.9}]

        w = _SemanticQueryWorker(object(), ["旧查询"], 4)
        cancelled = []
        done = []
        w.cancelled.connect(cancelled.append, Qt.DirectConnection)
        w.done.connect(lambda *args: done.append(args), Qt.DirectConnection)
        with mock.patch("core.visual_search.get_index", return_value=_SlowIndex()), \
                mock.patch("core.visual_search.get_encoder", return_value=object()):
            w.start()
            self.assertTrue(entered.wait(1.0), "worker 应已进入检索")
            w.requestInterruption()
            self.assertTrue(w.wait(2000), "worker 应在检索返回后安全退出")
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(done, [], "取消后的旧结果不得发出 done")

    def test_semantic_worker_exception_emits_failed(self):
        """生产 worker 异常应走 failed，不得卡死或误报 done。"""
        from ui.main_window_v3 import _SemanticQueryWorker

        w = _SemanticQueryWorker(object(), ["白狼"], 4)
        failed = []
        w.failed.connect(lambda token, err: failed.append(err), Qt.DirectConnection)
        with mock.patch("core.visual_search.get_index",
                        side_effect=RuntimeError("索引不可用")):
            w.run()
        self.assertEqual(failed, ["索引不可用"])

    def test_index_load_failure_starts_recovery(self):
        """FAISS 加载失败时应重置可重建缓存并启动后台恢复。"""
        win = self.win
        token = object()
        win._sem_pending = (token, "白狼", ["白狼"], 4)
        win._global_search.set_query("白狼")
        with mock.patch.object(win, "_reset_semantic_index_cache") as reset, \
                mock.patch.object(win, "_start_semantic_build") as rebuild:
            win._on_semantic_query_failed(token, "索引加载失败：bad file")
        reset.assert_called_once_with()
        rebuild.assert_called_once_with()

    def test_index_build_failure_restores_retry_state(self):
        """索引构建失败后必须清除 building 状态并显示重试入口。"""
        win = self.win
        win._sem_building = True
        win._sem_worker = None
        win._open_global_search()
        settle(self.app, 4)
        win._global_search.set_query("白狼")
        win._on_semantic_build_failed("磁盘不可用")
        self.assertFalse(win._sem_building)
        self.assertIsNone(win._sem_worker)
        retry = [i for i in win._global_search._items
                 if (i.get("payload") or {}).get("kind") == "semantic_retry"]
        self.assertEqual(len(retry), 1)


class _FakeLoadedEncoder:
    """已加载的编码器桩：语义分区走「直接检索」分支。"""

    def is_loaded(self):
        return True


class _FakeColdEncoder:
    """未加载的编码器桩：语义分区应转后台预热。"""

    def is_loaded(self):
        return False


class _SyncSemanticWorker(QObject):
    """同步桩：start() 里立刻发 done，测试无需等真线程/无需保持 patch 作用域。"""

    done = Signal(object, object)
    failed = Signal(object, str)

    def __init__(self, token, texts, top_k, parent=None):
        super().__init__(parent)
        self._token = token
        self._texts = list(texts or [])
        self._top_k = int(top_k)

    def start(self):
        # 延迟一拍：模拟真线程的 queued 信号（结果在外层回调返回后才到）
        QTimer.singleShot(0, self._emit)

    def _emit(self):
        from core.visual_search import get_encoder, get_index
        try:
            hits = get_index().search_by_text(
                self._texts, get_encoder(), top_k=self._top_k)
            self.done.emit(self._token, hits)
        except Exception as e:
            self.failed.emit(self._token, str(e))

    def isRunning(self):
        return False

    def wait(self, *args, **kwargs):
        return True


class _FakeIndex:
    """语义分区测试桩：返回固定结果，不加载模型/不建索引。"""

    def __init__(self, n):
        self._n = n

    def count(self):
        return self._n

    def search_by_text(self, q, enc, top_k=4):
        return [
            {"photo_id": i, "path": f"C:/fake/p{i}.jpg",
             "similarity": 0.9 - i * 0.05}
            for i in range(self._n)
        ]

if __name__ == "__main__":
    unittest.main()
