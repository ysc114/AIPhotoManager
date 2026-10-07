"""Exercise actual key delivery to the ordinary top search bar."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ui.search_bar import GlassSearchBar


@pytest.fixture
def bar():
    app = QApplication.instance() or QApplication([])
    widget = GlassSearchBar()
    widget.show()
    widget._render_results({"photos": [
        {"name": "first", "path": "missing-first.jpg"},
        {"name": "second", "path": "missing-second.jpg"},
    ]}, "test")
    widget._show_panel()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_enter_skips_section_title_and_opens_first_result(bar):
    selected = []
    bar.photo_activated.connect(selected.append)
    QTest.keyClick(bar._edit, Qt.Key_Return)
    assert selected == ["missing-first.jpg"]
    assert not bar._panel.isVisible()


def test_arrow_navigation_then_enter_opens_selected_result(bar):
    selected = []
    bar.photo_activated.connect(selected.append)
    QTest.keyClick(bar._edit, Qt.Key_Down)
    QTest.keyClick(bar._edit, Qt.Key_Down)
    QTest.keyClick(bar._edit, Qt.Key_Up)
    assert bar._panel.currentRow() == 1
    QTest.keyClick(bar._edit, Qt.Key_Down)
    QTest.keyClick(bar._edit, Qt.Key_Return)
    assert selected == ["missing-second.jpg"]


def test_escape_hides_results_and_cancels_pending_refresh(bar):
    bar._debounce.start()
    QTest.keyClick(bar._edit, Qt.Key_Escape)
    assert not bar._panel.isVisible()
    assert not bar._debounce.isActive()
