"""Keep the existing batch classification actions off the Qt GUI thread."""

import os

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog, QLabel, QMessageBox, QProgressBar, QPushButton, QVBoxLayout,
)


class BatchAnalysisWorker(QThread):
    progress = Signal(int, str)

    def __init__(self, kind, paths, target_folder=None):
        super().__init__()
        self.kind = kind
        self.paths = list(paths)
        self.target_folder = target_folder
        self.result = None
        self.error = None

    def run(self):
        organizer = None
        try:
            if self.isInterruptionRequested():
                self.result = {"cancelled": True}
                return
            if self.kind == "classify":
                from core.auto_organizer import auto_organize

                self.result = auto_organize(
                    self.paths, self.target_folder, mode="copy",
                    remove_duplicates=True,
                    progress_callback=lambda cur, total, status: self.progress.emit(
                        int(cur / max(1, total) * 100), status),
                    cancelled=self.isInterruptionRequested,
                )
            else:
                from core.ai_organizer import AIOrganizer

                organizer = AIOrganizer()
                self.result = organizer.organize_folder(
                    self.paths,
                    progress_callback=lambda step, status, percent: self.progress.emit(
                        percent, status),
                    cancelled=self.isInterruptionRequested,
                )
            if self.result is None:
                raise RuntimeError("AI 整理没有返回结果，请重试")
        except Exception as exc:
            self.error = str(exc)
        finally:
            if organizer is not None:
                try:
                    organizer.close()
                except Exception as exc:
                    self.error = self.error or str(exc)


class _BatchProgressDialog(QDialog):
    def __init__(self, window, worker):
        super().__init__(window)
        self.window = window
        self.worker = worker
        self.title = "自动分类" if worker.kind == "classify" else "AI智能整理"
        self.setWindowTitle(self.title + "进度")
        self.setMinimumWidth(450)
        layout = QVBoxLayout(self)
        self.label = QLabel("准备开始...")
        self.label.setWordWrap(True)
        self.label.setStyleSheet("font-size: 14px; padding: 15px;")
        layout.addWidget(self.label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self._cancel)
        layout.addWidget(self.cancel_button)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)

    def _cancel(self):
        if self.worker.isRunning():
            self.worker.requestInterruption()
            self.label.setText("正在取消，等待当前图片处理完成...")
            self.cancel_button.setEnabled(False)

    def reject(self):
        if self.worker.isRunning():
            self._cancel()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker.isRunning():
            self._cancel()
            event.ignore()
        else:
            super().closeEvent(event)

    def _on_progress(self, percent, status):
        if not self.worker.isInterruptionRequested():
            self.progress.setValue(percent)
            self.label.setText(status)
            self.window.statusBar().showMessage(f"{self.title}：{status}")

    def _on_finished(self):
        # QThread.finished precedes final thread-local cleanup. Retain ownership
        # until wait confirms native thread exit; never block the GUI for it.
        if not self.worker.wait(0):
            QTimer.singleShot(25, self._on_finished)
            return
        if getattr(self.window, "_batch_analysis_worker", None) is self.worker:
            self.window._batch_analysis_worker = None
            self.window._batch_analysis_dialog = None
        self.close()
        if not getattr(self.window, "_sem_closing", False):
            self._show_result()
        self.deleteLater()

    def _show_result(self):
        if self.worker.error:
            self.window.statusBar().showMessage(self.title + "失败，可以重试")
            QMessageBox.critical(self.window, self.title + "失败", self.worker.error)
            return
        result = self.worker.result or {}
        cancelled = result.get("cancelled", False)
        status = self.title + ("已取消" if cancelled else "完成")
        report = status + "\n\n"
        report += f"成功：{result.get('success', 0)} 张\n"
        report += f"失败：{result.get('failed', 0)} 张\n"
        if self.worker.kind == "classify":
            report += f"缓存命中：{result.get('cache_hits', 0)} 张\n"
            report += f"跳过重复：{result.get('duplicates_skipped', 0)} 张\n"
            report += f"输出位置：{self.worker.target_folder}\n"
        else:
            report += f"人物分组：{len(result.get('characters', []))} 组\n"
        for category, count in result.get("categories", {}).items():
            report += f"  【{category}】：{count} 张\n"
        errors = result.get("errors", [])
        if errors:
            report += "\n错误详情（前5条）：\n"
            for error in errors[:5]:
                if isinstance(error, (tuple, list)):
                    report += f"{os.path.basename(error[0])}：{error[1]}\n"
                else:
                    report += str(error) + "\n"
        self.window.statusBar().showMessage(status)
        show = QMessageBox.warning if errors or result.get("failed") else QMessageBox.information
        show(self.window, status, report)


def start_batch_analysis(window, kind, paths, target_folder=None):
    """The main window owns the worker until its existing shutdown routine reaps it."""
    worker = BatchAnalysisWorker(kind, paths, target_folder)
    dialog = _BatchProgressDialog(window, worker)
    window._batch_analysis_worker = worker
    window._batch_analysis_dialog = dialog
    dialog.show()
    worker.start()
