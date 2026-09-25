import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

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


    def _multi_role_setup(self, window, tmpdir):
        """构造一张合照（角标显示 ×2）并返回 (photo, group, badge)。"""
        photo = self._synthetic_photo(tmpdir, "multi.jpg")
        group = {
            "character_id": "cid-current",
            "name": "当前角色",
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
                               return_value={photo: 2}):
            window._open_group("fursuit", group, "当前角色")
        self.app.processEvents()
        state = window._group_pages["fursuit"]
        tile = state["wall_grid_layout"].itemAt(0).widget()
        badges = [lab for lab in tile.findChildren(type(state["wall_count"]))
                  if "合照" in lab.text()]
        self.assertTrue(badges, "合照角标应存在")
        return photo, group, badges[0]

    def test_multi_role_menu_lists_other_roles_and_jumps(self):
        """菜单只列其他角色；选中后跳转该角色详情页。"""
        tmpdir = tempfile.mkdtemp()
        window = MainWindow()
        try:
            photo, _, badge = self._multi_role_setup(window, tmpdir)
            refs = [
                {"character_id": "cid-current", "name": "当前角色",
                 "type": "fursuit_character"},
                {"character_id": "cid-other", "name": "同框角色",
                 "type": "fursuit_character", "photos": 12},
            ]
            others = [r for r in refs if r["character_id"] != "cid-current"]
            menu = window._build_multi_role_menu(others)
            labels = [a.text() for a in menu.actions() if a.text()]
            data = [a.data() for a in menu.actions() if a.data()]
            self.assertTrue(any("同框角色" in x for x in labels))
            self.assertTrue(any("12 张照片" in x for x in labels),
                            "无名角色也应按照片数区分")
            self.assertEqual(data, ["cid-other"])

            class _StubAction:
                def data(self):
                    return "cid-other"

            class _StubMenu:
                def exec(self, *args, **kwargs):
                    return _StubAction()

            with mock.patch.object(window, "_image_group_refs",
                                   return_value=refs), \
                    mock.patch.object(window, "_build_multi_role_menu",
                                      return_value=_StubMenu()), \
                    mock.patch.object(window, "_open_group_by_id") as jump:
                window._show_multi_role_menu(badge, photo, "cid-current")
            jump.assert_called_once_with("cid-other")
        finally:
            window.close()
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_badge_click_routes_to_multi_role_menu(self):
        """点击角标 → 进入合照菜单派发（不触发照片页跳转）。"""
        tmpdir = tempfile.mkdtemp()
        window = MainWindow()
        try:
            photo, _, badge = self._multi_role_setup(window, tmpdir)
            self.assertIn(badge, window._tile_multi_map)
            with mock.patch.object(window, "_show_multi_role_menu") as menu_call, \
                    mock.patch.object(window, "_open_photo_in_photo_page") as photo_page:
                QTest.mouseClick(badge, Qt.LeftButton)
            self.assertTrue(menu_call.called, "角标点击应打开合照菜单")
            self.assertEqual(menu_call.call_args[0][1], photo)
            self.assertEqual(menu_call.call_args[0][2], "cid-current")
            self.assertFalse(photo_page.called, "角标点击不应跳到照片页")
        finally:
            window.close()
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_photo_page_roles_button_jumps(self):
        """照片页「同框角色」：列出该照片的角色并跳转。"""
        window = MainWindow()
        try:
            window.current_image_path = "C:/fake/photo.jpg"
            refs = [{"character_id": "cid-a", "name": "", "type": "fursuit_character",
                     "photos": 3, "detection_index": 0},
                    {"character_id": "cid-b", "name": "", "type": "fursuit_character",
                     "photos": 5, "detection_index": 1}]

            class _StubAction:
                def data(self):
                    return "cid-b"

            class _StubMenu:
                def exec(self, *args, **kwargs):
                    return _StubAction()

            captured = {}

            def _build(refs_arg, header=None):
                captured["header"] = header
                captured["n"] = len(refs_arg)
                return _StubMenu()

            with mock.patch.object(window, "_photo_roles_refs",
                                   return_value=refs), \
                    mock.patch.object(window, "_build_multi_role_menu", _build), \
                    mock.patch.object(window, "_open_group_by_id") as jump:
                window._show_photo_roles()
            self.assertEqual(captured["n"], 2)
            self.assertIn("这张照片里有 2 个角色", captured["header"])
            jump.assert_called_once_with("cid-b")
        finally:
            window.close()

    def test_photo_page_roles_button_click_and_hint(self):
        """按钮已接线；无角色归属时给提示而不是弹菜单。"""
        window = MainWindow()
        try:
            with mock.patch.object(window, "_show_photo_roles") as handler:
                QTest.mouseClick(window.btn_roles, Qt.LeftButton)
            self.assertTrue(handler.called, "按钮应触发 _show_photo_roles")

            window.current_image_path = "C:/fake/none.jpg"
            with mock.patch.object(window, "_photo_roles_refs", return_value=[]), \
                    mock.patch.object(window, "_build_multi_role_menu") as build:
                window._show_photo_roles()
            self.assertFalse(build.called, "没有角色归属时不应弹菜单")
            self.assertIn("还没有角色归属",
                          window.statusBar().currentMessage())
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
