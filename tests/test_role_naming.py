# -*- coding: utf-8 -*-
"""角色显示名（稳定序号）测试：纯展示层，不改库、不动 schema。"""
import os
import shutil
import sqlite3
import tempfile
import unittest

from core.identity import display_name, type_label
from core.identity.database import IdentityDatabase
from core.identity.manager import IdentityManager


class DisplayNameTests(unittest.TestCase):
    def test_named_group_keeps_user_name(self):
        self.assertEqual(display_name("小白", "fursuit_character", 7), "小白")
        self.assertEqual(display_name("  小白  ", "fursuit_character", 7), "小白")

    def test_unnamed_group_uses_stable_serial(self):
        self.assertEqual(display_name("", "fursuit_character", 7),
                         "未命名兽装角色 #007")
        self.assertEqual(display_name(None, "real_person", 12),
                         "未命名人物 #012")

    def test_unnamed_without_serial_falls_back(self):
        self.assertEqual(display_name("", "fursuit_character"), "未命名兽装角色")

    def test_photos_suffix(self):
        self.assertEqual(display_name("", "fursuit_character", 3, photos=5),
                         "未命名兽装角色 #003 · 5 张照片")
        self.assertEqual(display_name("小白", "real_person", 1, photos=2),
                         "小白 · 2 张照片")

    def test_type_label(self):
        self.assertEqual(type_label("fursuit_character"), "兽装角色")
        self.assertEqual(type_label("real_person"), "人物")
        self.assertEqual(type_label(""), "角色")
        self.assertEqual(type_label(None), "角色")


class GroupSerialTests(unittest.TestCase):
    """稳定序号：同类型内 created_at DESC → 最新的组是 #1；按类型分区。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="naming_")
        self.db_path = os.path.join(self.tmp, "id.sqlite")
        self.db = IdentityDatabase(self.db_path)
        con = sqlite3.connect(self.db_path)
        rows = [
            ("g_old", "fursuit_character", "2026-01-01 00:00:00"),
            ("g_new", "fursuit_character", "2026-09-01 00:00:00"),
            ("p_one", "real_person", "2026-05-01 00:00:00"),
        ]
        for gid, gtype, created in rows:
            con.execute(
                "INSERT INTO identity_group (id, name, type, created_at)"
                " VALUES (?,?,?,?)", (gid, "", gtype, created))
            con.execute(
                "INSERT INTO identity_image (group_id, image_path,"
                " detection_index, embedding_type) VALUES (?,?,?,?)",
                (gid, f"C:/fake/{gid}.jpg", 0, "fursuit_fursee"))
        con.commit()
        con.close()

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_serials_are_per_type_and_newest_first(self):
        serials = self.db.get_group_serials()
        self.assertEqual(serials["g_new"], 1)
        self.assertEqual(serials["g_old"], 2)
        self.assertEqual(serials["p_one"], 1, "人物类型另起一段序号")

    def test_get_groups_annotates_serial(self):
        mgr = IdentityManager(db_path=self.db_path)
        try:
            groups = {g["character_id"]: g for g in mgr.get_groups()}
            serials = mgr.db.get_group_serials()
            self.assertEqual(set(groups), set(serials))
            for cid, g in groups.items():
                self.assertEqual(g["serial"], serials[cid])
            self.assertEqual(groups["g_new"]["serial"], 1)
        finally:
            mgr.close()


class GroupRefDedupeTests(unittest.TestCase):
    """get_groups_by_image：同一角色多个 detection 只返回一行（最高 confidence）。

    生产库实测有 4 张照片在同一角色下有 2 个 detection，会让合照跳转菜单
    把同一角色列两次。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="refs_")
        self.db_path = os.path.join(self.tmp, "id.sqlite")
        self.db = IdentityDatabase(self.db_path)
        self.path = "C:/fake/same.jpg"
        con = sqlite3.connect(self.db_path)
        for gid, created in (("gA", "2026-01-01 00:00:00"),
                             ("gB", "2026-02-01 00:00:00")):
            con.execute(
                "INSERT INTO identity_group (id, name, type, created_at)"
                " VALUES (?,?,?,?)", (gid, "", "fursuit_character", created))
        # 注意：schema 的 UNIQUE(image_path, detection_index) 是全局的，
        # 同一张照片的不同角色必须占用不同的 detection_index
        for gid, det, conf in (("gA", 0, 0.70), ("gA", 1, 0.95),
                               ("gB", 2, 0.80)):
            con.execute(
                "INSERT INTO identity_image (group_id, image_path,"
                " detection_index, embedding_type, confidence)"
                " VALUES (?,?,?,?,?)", (gid, self.path, det,
                                        "fursuit_fursee", conf))
        con.commit()
        con.close()

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_same_group_detections_deduped(self):
        refs = self.db.get_groups_by_image(self.path)
        cids = [r["character_id"] for r in refs]
        self.assertEqual(sorted(cids), ["gA", "gB"], "每个角色只出现一次")
        self.assertEqual(len(cids), len(set(cids)))
        ga = [r for r in refs if r["character_id"] == "gA"][0]
        self.assertAlmostEqual(ga["confidence"], 0.95, places=3,
                               msg="同角色多 detection 取 confidence 最高者")
        self.assertEqual(ga["detection_index"], 1)
