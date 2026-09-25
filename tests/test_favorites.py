# -*- coding: utf-8 -*-
"""收藏功能测试（Phase 3-1）。

隔离：temp 库 + offscreen UI。覆盖：收藏/取消/幂等/唯一照片/不触碰角色。
"""
import os
import tempfile
import shutil
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from core.identity.database import IdentityDatabase
from ui.main_window_v3 import MainWindow


class FavoriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = IdentityDatabase(os.path.join(self.tmp, "t.db"))
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_favorite(self):
        self.db.add_favorite("/x/a.jpg")
        self.assertEqual(self.db.list_favorites(), ["/x/a.jpg"])
        self.assertTrue(self.db.is_favorite("/x/a.jpg"))

    def test_add_duplicate_idempotent(self):
        self.db.add_favorite("/x/a.jpg")
        self.db.add_favorite("/x/a.jpg")
        self.assertEqual(len(self.db.list_favorites()), 1, "同一照片不能重复收藏")

    def test_remove_favorite(self):
        self.db.add_favorite("/x/a.jpg")
        self.db.add_favorite("/x/b.jpg")
        self.db.remove_favorite("/x/a.jpg")
        self.assertEqual(self.db.list_favorites(), ["/x/b.jpg"])
        self.assertFalse(self.db.is_favorite("/x/a.jpg"))

    def test_favorite_does_not_touch_identity(self):
        """收藏不修改角色/检测/embedding/character_id。"""
        # 预置一个角色与成员
        import numpy as np
        gid = self.db.create_group("", "fursuit_character")
        emb = np.full(512, 0.1, dtype=np.float32)
        self.db.add_image(
            group_id=gid, image_path="/x/fur.jpg", detection_index=0,
            embedding=emb, embedding_type="fursuit_fursee",
            bbox="[1,2,3,4]", confidence=0.9,
        )
        self.db.add_favorite("/x/fur.jpg")
        rows = self.db.conn.execute(
            "SELECT group_id, embedding_type, bbox, confidence, length(embedding) "
            "FROM identity_image"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], gid)
        self.assertEqual(rows[0][1], "fursuit_fursee")
        # bbox 存储时被序列化为 list（[1,2,3,4] 合法），收藏不应改变它
        self.assertIn("1", rows[0][2])
        self.assertIn("4", rows[0][2])
        self.assertEqual(rows[0][3], 0.9)
        self.assertEqual(rows[0][4], 512 * 4)
        # character_id 未变
        self.assertIn(gid, [x["id"] for x in self.db.get_all_groups()])

    def test_favorite_table_in_new_db(self):
        tables = [
            r[0] for r in self.db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        self.assertIn("favorite_image", tables)
        # user_version 保持 2
        self.assertEqual(
            self.db.conn.execute("PRAGMA user_version").fetchone()[0], 2
        )

    def test_ui_favorites_page_exists(self):
        self.assertTrue(hasattr(self.window, "favorites_page"))
        self.assertTrue(hasattr(self.window, "_load_favorites_page"))
        # 照片页有收藏按钮
        from PySide6.QtWidgets import QPushButton
        btns = [b.text() for b in self.window.photo_page.findChildren(QPushButton)]
        self.assertIn("⭐ 收藏当前", btns)
        self.assertIn("♥ 收藏页", btns)


    def test_preview_favorite_lands_on_photo_page_and_restores(self):
        """收藏页点照片 → 照片页（回归：曾误切 AI精选页）+ 可返回原列表。"""
        win = self.window if hasattr(self, "window") else self.win
        win._on_bottom_nav_changed(win.content_stack.indexOf(win.photo_page))
        self.app.processEvents()
        before = list(win.image_list)
        if not before:
            # 照片页尚未自动载入时，先手动放两张真实照片
            before = [str(Path(__file__).resolve().parents[1] / "photos" / "12.png")]
            win.image_list = list(before)
            win._populate_photo_list(before)
        target = before[0]
        win._preview_favorite(target)
        self.app.processEvents()
        self.assertIs(win.content_stack.currentWidget(), win.photo_page,
                      "应切到照片页")
        self.assertEqual(win._photo_list_mode, "favorite")
        self.assertFalse(win.btn_restore_list.isHidden())
        win._restore_photo_list()
        self.app.processEvents()
        self.assertEqual(win.image_list, before, "应恢复原列表")
        self.assertEqual(win._photo_list_mode, "")

    def test_favorite_button_follows_plain_preview(self):
        """普通照片页预览也能收藏：无角色上下文时按钮仍跟随状态。"""
        import core.identity as identity_mod
        from unittest import mock

        photo = str(Path(__file__).resolve().parents[1] / "photos" / "12.png")
        win = self.window
        win.image_list = [photo]
        win._photo_detection_context = None
        win._populate_photo_list([photo], select=0)
        win.show_preview(0)
        self.app.processEvents()

        raw = win._current_photo_path()
        self.assertTrue(raw.endswith("12.png"),
                        "回归：普通预览也要给出库内路径")

        class _Mgr:
            def __init__(_self):
                _self.db = self.db

            def close(_self):
                pass

        class _Reader:
            def __init__(_self):
                _self.db = self.db

            def close(_self):
                pass

        with mock.patch.object(identity_mod, "IdentityManager", _Mgr), \
                mock.patch.object(identity_mod, "get_reader", lambda: _Reader()):
            win._sync_favorite_button()
            self.assertEqual(win.btn_fav_toggle.text(), "⭐ 收藏当前")
            self.db.add_favorite(raw)
            win.show_preview(0)
            self.assertIn("已收藏", win.btn_fav_toggle.text(),
                          "已收藏照片预览时按钮应提示取消收藏")

            win._toggle_favorite_current()      # 取消收藏
            self.app.processEvents()
            self.assertFalse(self.db.is_favorite(raw))
            self.assertEqual(win.btn_fav_toggle.text(), "⭐ 收藏当前")

            win._toggle_favorite_current()      # 再收藏
            self.app.processEvents()
            self.assertTrue(self.db.is_favorite(raw))
            self.assertIn("已收藏", win.btn_fav_toggle.text())

            win._remove_favorite(raw)          # 收藏页右键取消
            self.app.processEvents()
            self.assertFalse(self.db.is_favorite(raw))
            self.assertEqual(win.btn_fav_toggle.text(), "⭐ 收藏当前",
                             "删除后照片页按钮应回落")

        win.show_preview(-1)
        self.assertEqual(win.btn_fav_toggle.text(), "⭐ 收藏当前")
        self.assertEqual(win._current_photo_path(), "")


if __name__ == "__main__":
    unittest.main()
