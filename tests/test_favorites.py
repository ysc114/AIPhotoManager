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


if __name__ == "__main__":
    unittest.main()
