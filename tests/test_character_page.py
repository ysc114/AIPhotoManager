# -*- coding: utf-8 -*-
"""角色页面测试：复用兽装 UI、类别显示、all 过滤、详情页。

隔离策略：temp 库 + 内存 group 注入（不启动 Fursee/CLIP）。
"""
import os
import tempfile
import shutil
import unittest
from unittest import mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from ui.main_window_v3 import MainWindow
from core.identity.manager import IdentityManager


PHOTOS = Path(__file__).resolve().parents[1] / "photos"
PHOTO_A = str(PHOTOS / "20260604_091343.jpg")
PHOTO_B = str(PHOTOS / "20260623_100924.jpg")


def fursee_group(cid, name="", images=None, dets=None):
    images = images or [PHOTO_A]
    dets = dets or [{
        "image_path": PHOTO_A, "detection_index": 0,
        "bbox": "[0,0,100,100]", "confidence": 0.9,
        "embedding_type": "fursuit_fursee",
    }]
    return {
        "character_id": cid, "name": name, "type": "fursuit_character",
        "images": images, "detections": dets,
        "source_types": ["fursuit_fursee"],
        "cover_image": images[0],
    }


def face_group(cid, name="", images=None):
    images = images or [PHOTO_A]
    return {
        "character_id": cid, "name": name, "type": "real_person",
        "images": images,
        "detections": [{
            "image_path": images[0], "detection_index": 0,
            "bbox": "[0,0,100,100]", "confidence": 0.9,
            "embedding_type": "face",
        }],
        "source_types": ["face"],
        "cover_image": images[0],
    }


class CharacterPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        assert os.path.exists(PHOTO_A), f"测试照片缺失 {PHOTO_A}"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "t.db")
        self.mgr = IdentityManager(db_path=self.db_path)
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.mgr.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # 1. 角色页能显示兽装角色
    def test_character_page_shows_fursuit(self):
        cid = self.mgr.db.create_group("", "fursuit_character")
        self.mgr.db.add_image(
            group_id=cid, image_path=PHOTO_A, detection_index=0,
            embedding_type="fursuit_fursee", confidence=0.9,
        )
        groups = self.mgr.get_groups(group_type="all")
        cids = {g["character_id"] for g in groups}
        self.assertIn(cid, cids)

    # 2. 角色页能显示人物角色
    def test_character_page_shows_face(self):
        cid = self.mgr.db.create_group("", "real_person")
        self.mgr.db.add_image(
            group_id=cid, image_path=PHOTO_A, detection_index=0,
            embedding_type="face", confidence=0.9,
        )
        groups = self.mgr.get_groups(group_type="all")
        cids = {g["character_id"] for g in groups}
        self.assertIn(cid, cids)

    # 3. 角色页排除 legacy fursuit_visual
    def test_character_page_excludes_legacy(self):
        cid = self.mgr.db.create_group("", "fursuit_character")
        self.mgr.db.add_image(
            group_id=cid, image_path=PHOTO_A, detection_index=0,
            embedding_type="fursuit_visual", confidence=0.9,
        )
        groups = self.mgr.get_groups(group_type="all")
        cids = {g["character_id"] for g in groups}
        self.assertNotIn(cid, cids, "角色页不应包含 legacy visual 组")

    # 4. 卡片类别标签：兽装角色 · Fursee
    def test_card_category_fursuit(self):
        self.assertEqual(
            self.window._format_group_category(fursee_group("x")),
            "兽装角色 · Fursee",
        )

    # 5. 卡片类别标签：人物角色 · Face
    def test_card_category_face(self):
        self.assertEqual(
            self.window._format_group_category(face_group("x")),
            "人物角色 · Face",
        )

    # 6. 名称规则：name 空 → 未命名角色 #NNN（不写库）
    def test_display_name_rule(self):
        self.assertEqual(
            self.window._compute_display_name(
                {"name": ""}, 1, "角色"
            ),
            "未命名角色 #001",
        )
        self.assertEqual(
            self.window._compute_display_name(
                {"name": "小黑猫"}, 1, "角色"
            ),
            "小黑猫",
        )

    # 7. 重命名持久化（update_name → db）
    def test_rename_persisted(self):
        cid = self.mgr.db.create_group("", "fursuit_character")
        self.mgr.update_name(cid, "新名字")
        g = self.mgr.db.get_all_groups()
        self.assertEqual([x["name"] for x in g if x["id"] == cid], ["新名字"])

    # 8. 唯一照片数量正确（_unique_photo_count）
    def test_unique_photo_count(self):
        group = fursee_group(
            "c8",
            images=[PHOTO_A, PHOTO_B],
            dets=[
                {"image_path": PHOTO_A, "detection_index": 0,
                 "bbox": "[0,0,10,10]", "confidence": 0.9,
                 "embedding_type": "fursuit_fursee"},
                {"image_path": PHOTO_A, "detection_index": 1,
                 "bbox": "[5,5,9,9]", "confidence": 0.8,
                 "embedding_type": "fursuit_fursee"},
                {"image_path": PHOTO_B, "detection_index": 0,
                 "bbox": "[0,0,10,10]", "confidence": 0.9,
                 "embedding_type": "fursuit_fursee"},
            ],
        )
        self.assertEqual(self.window._unique_photo_count(group), 2)

    # 9. 详情页显示类别
    def test_detail_shows_category(self):
        group = fursee_group("c9")
        self.window._open_group("character", group, "测试角色")
        state = self.window._group_pages["character"]
        self.assertIn("兽装角色 · Fursee", state["wall_count"].text())
        self.assertIn("1 张照片", state["wall_count"].text())

    # 10. 详情页照片墙复用（同 path 多 det → 1 格）
    def test_detail_wall_same_path_multi_det_one(self):
        group = fursee_group(
            "c10",
            images=[PHOTO_A],
            dets=[
                {"image_path": PHOTO_A, "detection_index": 0,
                 "bbox": "[0,0,100,100]", "confidence": 0.9,
                 "embedding_type": "fursuit_fursee"},
                {"image_path": PHOTO_A, "detection_index": 1,
                 "bbox": "[5,5,60,60]", "confidence": 0.95,
                 "embedding_type": "fursuit_fursee"},
            ],
        )
        self.window._open_group("character", group, "测试")
        members = self.window._group_pages["character"]["current_members"]
        self.assertEqual(len(members), 1, "同 path 多 det 只显示 1 格")
        self.assertEqual(members[0][1], 1, "应选 conf 最高 det")

    # 11. 详情页照片墙：同 MD5 副本 → 1 格
    def test_detail_wall_md5_dedup(self):
        import shutil as _s
        tmp2 = tempfile.mkdtemp()
        try:
            p2 = os.path.join(tmp2, "copy(1).jpg")
            _s.copy2(PHOTO_A, p2)
            group = fursee_group(
                "c11",
                images=[PHOTO_A, p2],
                dets=[
                    {"image_path": PHOTO_A, "detection_index": 0,
                     "bbox": "[0,0,100,100]", "confidence": 0.9,
                     "embedding_type": "fursuit_fursee"},
                    {"image_path": p2, "detection_index": 0,
                     "bbox": "[0,0,100,100]", "confidence": 0.88,
                     "embedding_type": "fursuit_fursee"},
                ],
            )
            self.window._open_group("character", group, "测试")
            members = self.window._group_pages["character"]["current_members"]
            self.assertEqual(len(members), 1, "同 MD5 副本只显示 1 格")
        finally:
            _s.rmtree(tmp2, ignore_errors=True)

    # 12. 兽装页仍正常（filter 未受影响）
    def test_fursuit_page_unchanged(self):
        groups = self.mgr.get_groups(group_type="fursuit_character")
        self.assertIsInstance(groups, list)

    # 13. 人物页仍正常
    def test_person_page_unchanged(self):
        groups = self.mgr.get_groups(group_type="real_person")
        self.assertIsInstance(groups, list)

    # 14. 不修改 schema / character_id（纯查询）
    def test_no_schema_or_cid_change(self):
        cid = self.mgr.db.create_group("", "fursuit_character")
        before = self.mgr.db.conn.execute(
            "PRAGMA user_version"
        ).fetchone()[0]
        _ = self.mgr.get_groups(group_type="all")
        after = self.mgr.db.conn.execute(
            "PRAGMA user_version"
        ).fetchone()[0]
        self.assertEqual(before, after)
        g = self.mgr.db.get_all_groups()
        self.assertIn(cid, [x["id"] for x in g])

    # 15. 角色页无重复 character_id（all 过滤集合去重）
    def test_character_page_no_duplicate_cid(self):
        cid = self.mgr.db.create_group("", "fursuit_character")
        self.mgr.db.add_image(
            group_id=cid, image_path=PHOTO_A, detection_index=0,
            embedding_type="fursuit_fursee", confidence=0.9,
        )
        groups = self.mgr.get_groups(group_type="all")
        cids = [g["character_id"] for g in groups]
        self.assertEqual(len(cids), len(set(cids)), "角色页不得重复 character_id")


if __name__ == "__main__":
    unittest.main()
