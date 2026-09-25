import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.main_window_v3 import MainWindow

from tests.test_visual_duplicates import make_scene


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

            # 新规则（2026-08-26）：同 path 多 detection → 只显示 1 格，
            # 取 confidence 最高的 detection（det1 conf 0.91 > det2 0.89）。
            self.assertEqual(
                state["current_members"],
                [(photo_path, 1)],
            )
            self.assertEqual(state["wall_count"].text(), "1 张照片")

            window._open_photo_in_photo_page(group, photo_path, 1)
            self.app.processEvents()
            self.assertEqual(
                window._photo_detection_context["detection_index"],
                1,
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



    def _synthetic_photo(self, tmpdir, name="wide.jpg"):
        """合成 400x200（宽高比 2.0）测试照片，便于区分整图与裁剪。"""
        path = os.path.join(tmpdir, name)
        make_scene(path, w=400, h=200, seed=11)
        return path

    def _first_tile_image_pixmap(self, window, page_key):
        state = window._group_pages[page_key]
        tile = state["wall_grid_layout"].itemAt(0).widget()
        for label in tile.findChildren(type(window.preview_label)):
            pix = label.pixmap()
            if pix is not None and not pix.isNull():
                return pix
        return None

    def test_detail_wall_shows_full_image_not_crop(self):
        """目标：卡片封面用 detection 裁剪，详情页显示完整原图。"""
        tmpdir = tempfile.mkdtemp()
        window = MainWindow()
        try:
            photo = self._synthetic_photo(tmpdir)
            # 竖长 bbox（裁剪后宽高比 ~0.28），与整图 2.0 差异明显
            bbox = "[10, 10, 60, 190]"
            group = {
                "character_id": "ui-test-full",
                "name": "",
                "type": "fursuit_character",
                "images": [photo],
                "detections": [{
                    "image_path": photo,
                    "detection_index": 0,
                    "bbox": bbox,
                    "confidence": 0.9,
                    "embedding_type": "fursuit_fursee",
                }],
            }
            with mock.patch.object(window, "_image_group_counts",
                                   return_value={}):
                window._open_group("fursuit", group, "整图测试")
            self.app.processEvents()

            pix = self._first_tile_image_pixmap(window, "fursuit")
            self.assertIsNotNone(pix, "详情页瓦片应有缩略图")
            wall_ratio = pix.width() / pix.height()
            self.assertAlmostEqual(wall_ratio, 2.0, delta=0.15,
                                   msg="详情页应显示完整原图（宽高比 2.0）")

            crop = window._pixmap_for_detection(photo, (bbox, "fursuit_fursee"))
            self.assertFalse(crop.isNull())
            crop_ratio = crop.width() / crop.height()
            self.assertLess(crop_ratio, 0.6,
                            "卡片封面仍应为 detection 裁剪（窄长主体）")
        finally:
            window.close()
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_multi_role_photo_shows_badge_and_header(self):
        """合照多角色可见化：瓦片角标 + 详情页头部提示。"""
        tmpdir = tempfile.mkdtemp()
        window = MainWindow()
        try:
            photo = self._synthetic_photo(tmpdir, "group.jpg")
            group = {
                "character_id": "ui-test-multi",
                "name": "",
                "type": "fursuit_character",
                "images": [photo],
                "detections": [{
                    "image_path": photo,
                    "detection_index": 0,
                    "bbox": "[0, 0, 100, 100]",
                    "confidence": 0.9,
                    "embedding_type": "fursuit_fursee",
                }],
            }
            with mock.patch.object(window, "_image_group_counts",
                                   return_value={photo: 3}):
                window._open_group("fursuit", group, "合照测试")
            self.app.processEvents()

            state = window._group_pages["fursuit"]
            self.assertIn("合照 1 张", state["wall_count"].text())
            tile = state["wall_grid_layout"].itemAt(0).widget()
            badges = [lab.text() for lab in tile.findChildren(type(
                state["wall_count"])) if "合照" in lab.text()]
            self.assertTrue(badges, "瓦片应有合照角色数角标")
            self.assertIn("×3", badges[0])
            self.assertIn("3 个角色", tile.toolTip())
        finally:
            window.close()
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
