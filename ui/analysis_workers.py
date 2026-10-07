"""Background workers for photo-analysis actions initiated from the UI."""

from PySide6.QtCore import QThread, Signal


class SingleImageAnalysisWorker(QThread):
    done = Signal(str, object)
    failed = Signal(str, str)
    cancelled = Signal(str)

    def __init__(self, image_path, parent=None):
        super().__init__(parent)
        self.image_path = str(image_path)

    def run(self):
        try:
            if self.isInterruptionRequested():
                self.cancelled.emit(self.image_path)
                return
            from core.ai_classifier import AIClassifier

            result = AIClassifier().analyze(self.image_path)
            if self.isInterruptionRequested():
                self.cancelled.emit(self.image_path)
            else:
                self.done.emit(self.image_path, result)
        except Exception as exc:
            if self.isInterruptionRequested():
                self.cancelled.emit(self.image_path)
            else:
                self.failed.emit(self.image_path, str(exc))
