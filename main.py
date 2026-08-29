import sys

from PySide6.QtWidgets import QApplication
from ui.main_window_v3 import MainWindow
from core.thumbnail_cache import thumbnail_cache


def main():
    app = QApplication(sys.argv)

    window = MainWindow()
    window.show()

    # 退出时关闭缩略图缓存后台线程，避免 PySide6 清理时崩溃
    app.aboutToQuit.connect(thumbnail_cache.shutdown)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()