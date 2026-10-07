"""Photo-page video extraction and super-resolution task controls."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QLabel, QMessageBox, QProgressDialog,
)


_ROOT = Path(__file__).resolve().parents[1]
_PHOTOS = _ROOT / "photos"
_VIDEO_MANIFEST = _ROOT / "cache" / "video_frames.json"
_UPSCALE_MANIFEST = _ROOT / "cache" / "super_resolution.json"


class _VideoFramesWorker(QThread):
    progress_updated = Signal(int, str)
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, sources, interval, parent=None):
        super().__init__(parent)
        self.sources = list(sources)
        self.interval = interval

    def run(self):
        from core.video_frames import extract_video_frames

        result = {"frames": [], "new": 0, "reused": 0, "skipped": 0,
                  "errors": [], "cancelled": False, "output_dir": str(_PHOTOS)}
        try:
            for index, source in enumerate(self.sources):
                if self.isInterruptionRequested():
                    result["cancelled"] = True
                    break

                def on_progress(done, total, saved, i=index, path=source):
                    fraction = done / total if total else 0
                    percent = int((i + min(1, fraction)) / len(self.sources) * 100)
                    self.progress_updated.emit(
                        percent, f"{Path(path).name} · {done}/{total or '?'} 帧 · 已抽取 {saved} 张")

                try:
                    item = extract_video_frames(
                        source, self.interval, _PHOTOS, _VIDEO_MANIFEST,
                        progress=on_progress,
                        cancelled=self.isInterruptionRequested,
                    )
                    result["frames"].extend(item["frames"])
                    result["new"] += item["new"]
                    result["reused"] += item["reused"]
                    result["skipped"] += int(item["skipped"])
                    if item["cancelled"]:
                        result["cancelled"] = True
                        break
                except Exception as exc:
                    result["errors"].append((Path(source).name, str(exc)))
                self.progress_updated.emit(
                    int((index + 1) / len(self.sources) * 100),
                    f"已处理 {index + 1}/{len(self.sources)} 个视频")
            self.done.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class _UpscaleWorker(QThread):
    progress_updated = Signal(int, str)
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, sources, scale, parent=None):
        super().__init__(parent)
        self.sources = list(sources)
        self.scale = scale

    def run(self):
        from core.super_resolution import upscale_images

        def on_progress(index, total, fraction, name):
            percent = int((index + fraction) / total * 100) if total else 0
            if percent != getattr(self, "_last_percent", -1):
                self._last_percent = percent
                self.progress_updated.emit(percent, f"{name} · {index + 1}/{total}")

        try:
            result = upscale_images(
                self.sources, self.scale, _PHOTOS, _UPSCALE_MANIFEST,
                progress=on_progress, cancelled=self.isInterruptionRequested)
            self.done.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class _MediaTasksMixin:
    def super_resolution(self):
        worker = getattr(self, "_upscale_worker", None)
        if worker is not None and worker.isRunning():
            self.statusBar().showMessage("AI 超分正在进行中", 4000)
            return
        visible_paths = [self.image_list_widget.item(i).data(Qt.UserRole)
                         for i in range(self.image_list_widget.count())]
        visible_paths = [path for path in visible_paths if path]
        if not visible_paths:
            QMessageBox.information(self, "AI 超分", "请先打开照片文件夹。")
            return
        current = self.image_list_widget.currentRow()
        settings = QDialog(self)
        settings.setWindowTitle("AI 图片超分")
        form = QFormLayout(settings)
        scope = QComboBox(settings)
        scope.addItem("当前照片", "current")
        scope.addItem(f"当前列表全部 ({len(visible_paths)} 张)", "all")
        if current < 0:
            scope.setCurrentIndex(1)
        form.addRow("处理范围", scope)
        factor = QComboBox(settings)
        factor.addItem("2 倍", 2)
        factor.addItem("3 倍", 3)
        factor.setToolTip("2 倍由 3 倍 AI 推理结果高质量缩放得到")
        form.addRow("输出倍率", factor)
        auto_analyze = QCheckBox("完成后分析待处理照片", settings)
        form.addRow(auto_analyze)
        output_label = QLabel(str(_PHOTOS), settings)
        output_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        output_label.setWordWrap(True)
        form.addRow("输出位置", output_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=settings)
        buttons.accepted.connect(settings.accept)
        buttons.rejected.connect(settings.reject)
        form.addRow(buttons)
        if settings.exec() != QDialog.Accepted:
            return
        selected = self.image_list_widget.item(current) if current >= 0 else None
        sources = ([selected.data(Qt.UserRole)] if scope.currentData() == "current"
                   and selected is not None else visible_paths)
        self._start_upscale(sources, factor.currentData(), auto_analyze.isChecked())

    def _start_upscale(self, sources, scale, auto_analyze=False):
        worker = getattr(self, "_upscale_worker", None)
        if worker is not None and worker.isRunning():
            return False
        if not sources:
            return False
        dialog = QProgressDialog("准备超分…", "取消", 0, 100, self)
        dialog.setWindowTitle("AI 图片超分")
        dialog.setWindowModality(Qt.NonModal)
        dialog.setAutoClose(False)
        dialog.setMinimumDuration(0)
        dialog.setValue(0)
        worker = _UpscaleWorker(sources, scale)
        dialog.canceled.connect(worker.requestInterruption)
        worker.progress_updated.connect(self._on_upscale_progress)
        worker.done.connect(self._on_upscale_done)
        worker.failed.connect(self._on_upscale_failed)
        self._upscale_progress = dialog
        self._upscale_worker = worker
        self._upscale_auto_analyze = bool(auto_analyze)
        dialog.show()
        worker.start()
        return True

    def _on_upscale_progress(self, percent, message):
        dialog = getattr(self, "_upscale_progress", None)
        if dialog is not None:
            dialog.setValue(max(0, min(100, percent)))
            dialog.setLabelText(message)
        self.statusBar().showMessage(f"AI 超分：{message}", 3000)

    def _finish_upscale_task(self):
        worker = getattr(self, "_upscale_worker", None)
        if worker is not None and not self._reap_worker(worker):
            return False
        self._upscale_worker = None
        dialog = getattr(self, "_upscale_progress", None)
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
            self._upscale_progress = None
        return True

    def _on_upscale_done(self, result):
        if getattr(self, "_sem_closing", False):
            return
        if not self._finish_upscale_task():
            from PySide6.QtCore import QTimer
            QTimer.singleShot(100, lambda: self._on_upscale_done(result))
            return
        outputs = list(dict.fromkeys(result.get("outputs", [])))
        if outputs:
            self._add_pending_files(outputs)
            self._gs_photo_names = None
            self._photos_autoload_done = False
            if self.content_stack.currentWidget() is self.photo_page and not getattr(
                    self, "_photo_list_mode", ""):
                self.image_list = []
                self._ensure_photos_loaded()
        summary = (f"新增 {result['new']} 张，复用 {result['reused']} 张。\n"
                   f"输出：{result['output_dir']}\n"
                   "已加入待处理队列，可运行现有 AI 分析。")
        if result.get("cancelled"):
            summary += "\n任务已取消；已完成图片保留。"
        if result.get("errors"):
            details = "\n".join(f"{name}: {error}" for name, error in result["errors"][:5])
            summary += f"\n失败 {len(result['errors'])} 张，可修复后重新运行：\n{details}"
        if self._upscale_auto_analyze and outputs and not result.get("cancelled"):
            self._switch_page(self.content_stack.indexOf(self.pending_page))
            self._start_analyze_selected()
        (QMessageBox.warning if result.get("errors") else QMessageBox.information)(
            self, "AI 超分结果", summary)
        self.statusBar().showMessage(
            f"AI 超分完成：新增 {result['new']} 张，失败 {len(result['errors'])} 张", 8000)

    def _on_upscale_failed(self, error):
        if getattr(self, "_sem_closing", False):
            return
        if not self._finish_upscale_task():
            from PySide6.QtCore import QTimer
            QTimer.singleShot(100, lambda: self._on_upscale_failed(error))
            return
        QMessageBox.critical(self, "AI 超分失败", error)
        self.statusBar().showMessage("AI 超分失败，可重新运行", 8000)

    def extract_video_frames(self):
        worker = getattr(self, "_video_worker", None)
        if worker is not None and worker.isRunning():
            self.statusBar().showMessage("视频抽帧正在进行中", 4000)
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择视频", "",
            "视频 (*.mp4 *.mov *.avi *.mkv *.webm *.m4v *.wmv)")
        if not files:
            return

        settings = QDialog(self)
        settings.setWindowTitle("视频抽帧")
        form = QFormLayout(settings)
        interval = QDoubleSpinBox(settings)
        interval.setRange(0.1, 3600.0)
        interval.setDecimals(1)
        interval.setSingleStep(0.5)
        interval.setValue(2.0)
        interval.setSuffix(" 秒")
        form.addRow("抽帧间隔", interval)
        auto_analyze = QCheckBox("抽帧后分析待处理照片", settings)
        auto_analyze.setChecked(False)
        form.addRow(auto_analyze)
        output_label = QLabel(str(_PHOTOS), settings)
        output_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        output_label.setWordWrap(True)
        form.addRow("输出位置", output_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=settings)
        buttons.accepted.connect(settings.accept)
        buttons.rejected.connect(settings.reject)
        form.addRow(buttons)
        if settings.exec() != QDialog.Accepted:
            return
        self._start_video_frames(files, interval.value(), auto_analyze.isChecked())

    def _start_video_frames(self, sources, interval, auto_analyze=False):
        worker = getattr(self, "_video_worker", None)
        if worker is not None and worker.isRunning():
            return False
        dialog = QProgressDialog("准备抽帧…", "取消", 0, 100, self)
        dialog.setWindowTitle("视频抽帧")
        dialog.setWindowModality(Qt.NonModal)
        dialog.setAutoClose(False)
        dialog.setMinimumDuration(0)
        dialog.setValue(0)
        worker = _VideoFramesWorker(sources, interval)
        dialog.canceled.connect(worker.requestInterruption)
        worker.progress_updated.connect(self._on_video_progress)
        worker.done.connect(self._on_video_done)
        worker.failed.connect(self._on_video_failed)
        self._video_progress = dialog
        self._video_worker = worker
        self._video_auto_analyze = bool(auto_analyze)
        dialog.show()
        worker.start()
        return True

    def _on_video_progress(self, percent, message):
        dialog = getattr(self, "_video_progress", None)
        if dialog is not None:
            dialog.setValue(max(0, min(100, percent)))
            dialog.setLabelText(message)
        self.statusBar().showMessage(f"视频抽帧：{message}", 3000)

    def _finish_video_task(self):
        worker = getattr(self, "_video_worker", None)
        if worker is not None and not self._reap_worker(worker):
            return False
        self._video_worker = None
        dialog = getattr(self, "_video_progress", None)
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
            self._video_progress = None
        return True

    def _on_video_done(self, result):
        if getattr(self, "_sem_closing", False):
            return
        if not self._finish_video_task():
            from PySide6.QtCore import QTimer
            QTimer.singleShot(100, lambda: self._on_video_done(result))
            return
        frames = list(dict.fromkeys(result.get("frames", [])))
        if frames:
            self._add_pending_files(frames)
            self._gs_photo_names = None
            self._photos_autoload_done = False
            if self.content_stack.currentWidget() is self.photo_page and all(
                    Path(p).parent == _PHOTOS for p in self.image_list):
                if not getattr(self, "_photo_list_mode", ""):
                    self.image_list = []
                    self._ensure_photos_loaded()
        summary = (f"新增 {result['new']} 张，复用 {result['reused']} 张，"
                   f"跳过已处理视频 {result['skipped']} 个。\n"
                   f"输出：{result['output_dir']}\n"
                   f"已加入待处理队列，可在待处理页运行现有 AI 分析。")
        if result.get("cancelled"):
            summary += "\n任务已取消；已完成的帧保留，下次可续跑。"
        if result.get("errors"):
            details = "\n".join(f"{name}: {err}" for name, err in result["errors"][:5])
            summary += f"\n失败 {len(result['errors'])} 个视频，可修复后重新运行：\n{details}"
        if self._video_auto_analyze and frames and not result.get("cancelled"):
            self._switch_page(self.content_stack.indexOf(self.pending_page))
            self._start_analyze_selected()
        (QMessageBox.warning if result.get("errors") else QMessageBox.information)(
            self, "视频抽帧结果", summary)
        self.statusBar().showMessage(
            f"视频抽帧完成：新增 {result['new']} 张，失败 {len(result['errors'])} 个视频",
            8000)

    def _on_video_failed(self, error):
        if getattr(self, "_sem_closing", False):
            return
        if not self._finish_video_task():
            from PySide6.QtCore import QTimer
            QTimer.singleShot(100, lambda: self._on_video_failed(error))
            return
        QMessageBox.critical(self, "视频抽帧失败", error)
        self.statusBar().showMessage("视频抽帧失败", 8000)
