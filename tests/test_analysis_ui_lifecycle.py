"""Analysis UI and manager setup/cleanup failure regressions."""

import os
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from ui.main_window_v3 import MainWindow, _AnalyzeWorker, _ScanDirWorker


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("worker_cls,args", [(_AnalyzeWorker, (["a.jpg"],)),
                                            (_ScanDirWorker, ())])
def test_manager_constructor_failure_reaches_ui(qt_app, worker_cls, args):
    worker = worker_cls(*args)
    failures = []
    worker.failed.connect(failures.append)
    with mock.patch("core.identity.IdentityManager", side_effect=OSError("database unavailable")):
        worker.start()
        assert worker.wait(3000)
        qt_app.processEvents()
    assert failures == ["database unavailable"]


@pytest.mark.parametrize("worker_cls,args", [(_AnalyzeWorker, (["a.jpg"],)),
                                            (_ScanDirWorker, ())])
def test_manager_cleanup_precedes_done_signal(qt_app, worker_cls, args):
    manager = mock.Mock()
    manager.analyze_paths.return_value = {"new": 1}
    manager.analyze_new_photos.return_value = {"new": 1}
    worker = worker_cls(*args)
    completed = []
    worker.finished_ok.connect(lambda result: completed.append(manager.close.called))
    with mock.patch("core.identity.IdentityManager", return_value=manager):
        worker.start()
        assert worker.wait(3000)
        qt_app.processEvents()
    assert completed == [True]


def test_ai_button_starts_worker_for_visible_filtered_photo(qt_app, tmp_path):
    source = tmp_path / "selected.png"
    Image.new("RGB", (16, 16), "blue").save(source)
    window = MainWindow()
    worker = mock.Mock()
    worker.isRunning.return_value = False
    try:
        window.image_list = [str(source)]
        window._populate_photo_list(window.image_list)
        with mock.patch("ui.main_window_v3.SingleImageAnalysisWorker", return_value=worker) as factory:
            window.btn_ai.click()
        factory.assert_called_once_with(str(source))
        worker.start.assert_called_once()
        assert window._ai_analysis_worker is worker
        # A second click before the terminal signal must not replace ownership.
        with mock.patch("ui.main_window_v3.SingleImageAnalysisWorker") as factory:
            window.btn_ai.click()
        factory.assert_not_called()
    finally:
        window._ai_analysis_worker = None  # This worker is a mock and never ran.
        window.close()


def test_queued_analysis_completion_does_not_restart_work_after_close(qt_app):
    window = MainWindow()
    try:
        window._sem_closing = True
        window._pending_worker = None
        with mock.patch.object(window, "_refresh_after_ingest") as refresh, \
                mock.patch.object(window, "_update_visual_index_async") as index, \
                mock.patch.object(window, "_show_analyze_summary") as summary:
            window._on_analyze_done({"new": 1})
        refresh.assert_not_called()
        index.assert_not_called()
        summary.assert_not_called()
    finally:
        window.close()
