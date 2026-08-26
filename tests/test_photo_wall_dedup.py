# -*- coding: utf-8 -*-
"""照片墙「唯一照片内容」去重测试（2026-08-26 修复）。

规则：
① 同一 image_path 多 detection → UI 只显示 1 格，取组内最高 confidence
   detection 做 crop；
② 不同 image_path 但 MD5 内容相同 → 只显示 1 格（局部 seen_md5，跨组
   不共享）；
③ 跨角色组不去重；bbox 无效回退完整原图；点击仍打开完整原图；
④ 数据库 detection 数量/character_id 完全不变。

全部使用内存 group 数据 + offscreen 渲染，不碰生产库。
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.main_window_v3 import MainWindow


PHOTOS = Path(__file__).resolve().parents[1] / "photos"
PHOTO_A = str(PHOTOS / "20260604_091343.jpg")     # 已有测试照片
PHOTO_B = str(PHOTOS / "20260623_100924.jpg")     # 另一张不同照片


def make_group(cid, images, detections):
    """构造内存 group（与 get_groups 返回结构一致）。"""
    return {
        "character_id": cid,
        "name": "",
        "type": "fursuit_character",
        "images": images,
        "detections": detections,
    }


def make_det(path, det_idx, bbox, conf, etype="fursuit_fursee"):
    return {
        "image_path": path,
        "detection_index": det_idx,
        "bbox": bbox,
        "confidence": conf,
        "embedding_type": etype,
    }


class PhotoWallDedupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        assert os.path.exists(PHOTO_A), f"测试照片缺失: {PHOTO_A}"
        assert os.path.exists(PHOTO_B), f"测试照片缺失: {PHOTO_B}"

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()

    # 1. 同 path + 多 detection → UI 只有 1 格
    def test_same_path_multi_det_shows_one_tile(self):
        group = make_group(
            "g1",
            [PHOTO_A],
            [
                make_det(PHOTO_A, 0, "[0,0,100,100]", 0.90),
                make_det(PHOTO_A, 1, "[200,200,300,300]", 0.80),
            ],
        )
        self.window._open_group("fursuit", group, "角色1")
        members = self.window._group_pages["fursuit"]["current_members"]
        self.assertEqual(len(members), 1, "同 path 多 det 应只显示 1 格")
        self.assertEqual(members[0][0], PHOTO_A)

    # 2. 同 path + 多 detection → 最高 confidence 用于 crop
    def test_same_path_picks_highest_confidence_det(self):
        group = make_group(
            "g2",
            [PHOTO_A],
            [
                make_det(PHOTO_A, 0, "[0,0,100,100]", 0.60),
                make_det(PHOTO_A, 1, "[200,200,300,300]", 0.95),
                make_det(PHOTO_A, 2, "[400,400,500,500]", 0.70),
            ],
        )
        self.window._open_group("fursuit", group, "角色2")
        members = self.window._group_pages["fursuit"]["current_members"]
        self.assertEqual(members, [(PHOTO_A, 1)], "应选 conf=0.95 的 det1 做 crop")

    # 3. 同 MD5 + 不同 path → UI 只有 1 格
    def test_same_md5_diff_path_shows_one_tile(self):
        tmpdir = tempfile.mkdtemp()
        try:
            p1 = os.path.join(tmpdir, "copy_a.jpg")
            p2 = os.path.join(tmpdir, "copy_b(1).jpg")
            shutil.copy2(PHOTO_A, p1)
            shutil.copy2(PHOTO_A, p2)  # 内容完全相同 → 同 MD5
            group = make_group(
                "g3",
                [p1, p2],
                [
                    make_det(p1, 0, "[0,0,100,100]", 0.90),
                    make_det(p2, 0, "[0,0,100,100]", 0.85),
                ],
            )
            self.window._open_group("fursuit", group, "角色3")
            members = self.window._group_pages["fursuit"]["current_members"]
            self.assertEqual(len(members), 1, "同 MD5 不同 path 应只显示 1 格")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # 4. 不同 MD5 → 正常显示多格
    def test_diff_md5_shows_multiple_tiles(self):
        group = make_group(
            "g4",
            [PHOTO_A, PHOTO_B],
            [
                make_det(PHOTO_A, 0, "[0,0,100,100]", 0.90),
                make_det(PHOTO_B, 0, "[0,0,100,100]", 0.90),
            ],
        )
        self.window._open_group("fursuit", group, "角色4")
        members = self.window._group_pages["fursuit"]["current_members"]
        self.assertEqual(len(members), 2, "不同 MD5 应显示 2 格")

    # 5. 同一合照属于两个不同角色 → 两个角色组各显示 1 格（跨组不去重）
    def test_cross_group_no_dedup(self):
        group_a = make_group(
            "roleA",
            [PHOTO_A],
            [make_det(PHOTO_A, 0, "[0,0,100,100]", 0.90)],
        )
        group_b = make_group(
            "roleB",
            [PHOTO_A],
            [make_det(PHOTO_A, 1, "[200,200,300,300]", 0.88)],
        )
        self.window._open_group("fursuit", group_a, "角色A")
        ma = self.window._group_pages["fursuit"]["current_members"]
        self.window._open_group("fursuit", group_b, "角色B")
        mb = self.window._group_pages["fursuit"]["current_members"]
        self.assertEqual(ma, [(PHOTO_A, 0)], "角色A 应有 1 格")
        self.assertEqual(mb, [(PHOTO_A, 1)], "角色B 应有 1 格（不被 A 去重）")

    # 6. bbox 无效 → 回退完整原图（不崩溃、有 tile）
    def test_invalid_bbox_falls_back_full_image(self):
        group = make_group(
            "g6",
            [PHOTO_A],
            [make_det(PHOTO_A, 0, "", 0.90)],  # 空 bbox
        )
        self.window._open_group("fursuit", group, "角色6")
        members = self.window._group_pages["fursuit"]["current_members"]
        self.assertEqual(len(members), 1)
        pix = self.window._pixmap_for_detection(
            PHOTO_A, ("", "fursuit_fursee"), None
        )
        self.assertFalse(pix.isNull(), "bbox 无效应回退完整原图")

    # 7. 点击 tile → 仍打开完整原图（照片页上下文含完整图）
    def test_click_tile_opens_full_image(self):
        group = make_group(
            "g7",
            [PHOTO_A],
            [make_det(PHOTO_A, 0, "[0,0,100,100]", 0.90)],
        )
        self.window._open_group("fursuit", group, "角色7")
        members = self.window._group_pages["fursuit"]["current_members"]
        path, det_idx = members[0]
        self.window._open_photo_in_photo_page(group, path, det_idx)
        ctx = self.window._photo_detection_context
        self.assertEqual(ctx["path"], self.window._resolve_display_path(PHOTO_A))
        self.assertEqual(ctx["detection_index"], 0)

    # 8. 卡片照片数量 = UI 唯一照片数量（同 path 多 det / 同 MD5 均计 1）
    def test_card_count_is_unique_photos(self):
        tmpdir = tempfile.mkdtemp()
        try:
            p1 = os.path.join(tmpdir, "copy_a.jpg")
            p2 = os.path.join(tmpdir, "copy_b(1).jpg")
            shutil.copy2(PHOTO_A, p1)
            shutil.copy2(PHOTO_A, p2)  # 同 MD5
            group = make_group(
                "g8",
                [p1, p2, PHOTO_B],
                [
                    make_det(p1, 0, "[0,0,100,100]", 0.90),
                    make_det(p1, 1, "[10,10,50,50]", 0.80),  # 同 path 多 det
                    make_det(p2, 0, "[0,0,100,100]", 0.85),
                    make_det(PHOTO_B, 0, "[0,0,100,100]", 0.90),
                ],
            )
            count = self.window._unique_photo_count(group)
            # p1/p2 同 MD5 = 1；PHOTO_B 不同 = 1 → 共 2
            self.assertEqual(count, 2, f"唯一照片数应为 2，实际 {count}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # 9. 新增 detection 后重新打开 → 仍正确去重（新照片链路同样生效）
    def test_reopen_after_new_detection_still_dedups(self):
        group = make_group(
            "g9",
            [PHOTO_A],
            [make_det(PHOTO_A, 0, "[0,0,100,100]", 0.90)],
        )
        self.window._open_group("fursuit", group, "角色9")
        self.assertEqual(
            len(self.window._group_pages["fursuit"]["current_members"]), 1
        )
        # 模拟新照片分析后：同一 path 出现第二个 det，且组新增同 MD5 副本
        tmpdir = tempfile.mkdtemp()
        try:
            p2 = os.path.join(tmpdir, "copy(1).jpg")
            shutil.copy2(PHOTO_A, p2)
            group["detections"].append(make_det(PHOTO_A, 1, "[5,5,60,60]", 0.99))
            group["detections"].append(make_det(p2, 0, "[0,0,100,100]", 0.88))
            group["images"].append(p2)
            self.window._open_group("fursuit", group, "角色9")
            members = self.window._group_pages["fursuit"]["current_members"]
            # 同 path 多 det → 1；同 MD5 副本 → 1 → 共 1 格
            self.assertEqual(len(members), 1, "新增 det/副本后仍应只显示 1 格")
            self.assertEqual(members[0][1], 1, "应选 conf=0.99 的新 det")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # 10. 数据库 detection 数量/character_id 不发生变化（纯内存操作）
    def test_database_untouched(self):
        group = make_group(
            "g10",
            [PHOTO_A],
            [
                make_det(PHOTO_A, 0, "[0,0,100,100]", 0.90),
                make_det(PHOTO_A, 1, "[200,200,300,300]", 0.80),
            ],
        )
        dets_before = [dict(d) for d in group["detections"]]
        cid_before = group["character_id"]
        self.window._open_group("fursuit", group, "角色10")
        # UI 操作后 group 数据未被修改
        self.assertEqual(group["character_id"], cid_before)
        self.assertEqual(
            [d["detection_index"] for d in group["detections"]],
            [d["detection_index"] for d in dets_before],
        )
        self.assertEqual(
            [d["confidence"] for d in group["detections"]],
            [d["confidence"] for d in dets_before],
        )
        # 照片墙成员不新增任何 det
        self.assertEqual(len(self.window._group_pages["fursuit"]["current_members"]), 1)


if __name__ == "__main__":
    unittest.main()
