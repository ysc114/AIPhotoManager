import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.main_window_v3 import MainWindow


class DetectionAwareUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_group_wall_keeps_same_photo_detections_separate(self):
        photo_path = str(
            Path(__file__).resolve().parents[1]
            / "photos"
            / "20260604_091343.jpg"
        )
        group = {
            "character_id": "ui-test-group",
            "name": "",
            "type": "fursuit_character",
            "images": [photo_path],
            "detections": [
                {
                    "image_path": photo_path,
                    "detection_index": 1,
                    "bbox": "[3340, 1262, 3721, 1623]",
                    "confidence": 0.91,
                    "embedding_type": "fursuit_fursee",
                },
                {
                    "image_path": photo_path,
                    "detection_index": 2,
                    "bbox": "[525, 1290, 876, 1646]",
                    "confidence": 0.89,
                    "embedding_type": "fursuit_fursee",
                },
            ],
        }

        window = MainWindow()
        try:
            window._open_group("fursuit", group, "测试角色")
            state = window._group_pages["fursuit"]

            self.assertEqual(
                state["current_members"],
                [(photo_path, 1), (photo_path, 2)],
            )
            self.assertEqual(state["wall_count"].text(), "2 个 detection · 1 张原图")

            window._open_photo_in_photo_page(group, photo_path, 2)
            self.app.processEvents()
            self.assertEqual(
                window._photo_detection_context["detection_index"],
                2,
            )
            plain = window._pixmap_for_full_preview(photo_path)
            window.show_preview(0)
            preview = window.preview_label.pixmap()
            self.assertIsNotNone(preview)
            self.assertFalse(preview.isNull())
            self.assertEqual(preview.size(), plain.size())
            plain_img = plain.toImage()
            preview_img = preview.toImage()
            diff = sum(
                1
                for y in range(plain_img.height())
                for x in range(plain_img.width())
                if plain_img.pixelColor(x, y) != preview_img.pixelColor(x, y)
            )
            self.assertGreater(diff, 0)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
