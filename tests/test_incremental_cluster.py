# -*- coding: utf-8 -*-
"""Incremental Assignment（增量分配）测试：tests/test_incremental_cluster.py

覆盖设计中的 8 项：
1. 加入已有组          2. 创建新组          3. 多组冲突（margin 保守）
4. 同照片多 det 独立   5. 人工合并保护       6. visual/face 隔离
7. 幂等                8. 0.79 阈值边界

全部使用临时库（tempfile），不触碰生产库。不依赖 Fursee/GPU。
"""
import os
import tempfile
import unittest

import numpy as np

from core.identity.database import IdentityDatabase
from core.identity.cluster import IdentityCluster

DIM = 8


def unit(*vals):
    """由前几维构造单位向量（其余维 0），自动归一化。"""
    v = np.zeros(DIM, dtype=np.float32)
    for i, val in enumerate(vals):
        v[i] = val
    n = np.linalg.norm(v)
    return (v / n).astype(np.float32) if n > 0 else v


class IncrementalClusterTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.db = IdentityDatabase(db_path=os.path.join(self._tmpdir, "t.db"))
        self.cluster = IdentityCluster(self.db)

    def tearDown(self):
        self.db.close()

    def _add(self, path, det, emb, gid="", etype="fursuit_fursee"):
        self.db.add_image(
            group_id=gid, image_path=path, embedding=emb,
            embedding_type=etype, detection_index=det,
            confidence=0.9, bbox=[0, 0, 10, 10],
        )

    def _assign(self, **kw):
        kw.setdefault("embedding_type", "fursuit_fursee")
        kw.setdefault("threshold", 0.79)
        kw.setdefault("margin", 0.02)
        return self.cluster.incremental_assign(**kw)

    # 1. 加入已有组
    def test_join_existing_group(self):
        ga = self.db.create_group(group_type="fursuit_character")
        self._add("p1.jpg", 0, unit(1.0), gid=ga)
        self._add("p2.jpg", 0, unit(1.0), gid=ga)
        # 新 det 与组 centroid cos≈0.95
        self._add("p3.jpg", 0, unit(0.95, 0.3118))
        r = self._assign()
        self.assertEqual(r["joined"], 1)
        self.assertEqual(r["created"], 0)
        self.assertEqual(r["conflicts"], [])
        # p3 进入已有组 ga
        row = self.db.conn.execute(
            "SELECT group_id FROM identity_image WHERE image_path='p3.jpg' AND detection_index=0"
        ).fetchone()
        self.assertEqual(row[0], ga)
        # 已有行 group_id 零改动
        n_ga = self.db.conn.execute(
            "SELECT COUNT(*) FROM identity_image WHERE group_id=?", (ga,)
        ).fetchone()[0]
        self.assertEqual(n_ga, 3)

    # 2. 创建新组
    def test_create_new_group(self):
        ga = self.db.create_group(group_type="fursuit_character")
        self._add("p1.jpg", 0, unit(1.0), gid=ga)
        before_groups = self.db.conn.execute(
            "SELECT COUNT(*) FROM identity_group"
        ).fetchone()[0]
        self._add("p2.jpg", 0, unit(0.3, 0.9539))
        r = self._assign()
        self.assertEqual(r["created"], 1)
        self.assertEqual(r["joined"], 0)
        row = self.db.conn.execute(
            "SELECT group_id FROM identity_image WHERE image_path='p2.jpg' AND detection_index=0"
        ).fetchone()
        self.assertNotEqual(row[0], ga)  # 新角色新 character_id
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM identity_group").fetchone()[0],
            before_groups + 1,
        )

    # 3. 多组冲突：两组 cos 都≥0.79 且差距<0.02 → 保守不合并
    def test_multi_group_conflict(self):
        ga = self.db.create_group(group_type="fursuit_character")
        gb = self.db.create_group(group_type="fursuit_character")
        self._add("p1.jpg", 0, unit(1.0), gid=ga)            # A=(1,0,...)
        self._add("p2.jpg", 0, unit(0.8, 0.6), gid=gb)       # B=(0.8,0.6,...)
        # x=(0.949,0.316) → cos(A)=0.949, cos(B)=0.949（并列）
        self._add("p3.jpg", 0, unit(0.949, 0.316))
        r = self._assign()
        self.assertEqual(r["joined"], 0)
        self.assertEqual(len(r["conflicts"]), 1)
        self.assertAlmostEqual(r["conflicts"][0]["top_cos"],
                               r["conflicts"][0]["second_cos"], places=3)
        # 冲突行保持未分配（group_id=''），不创建新组
        row = self.db.conn.execute(
            "SELECT group_id FROM identity_image WHERE image_path='p3.jpg' AND detection_index=0"
        ).fetchone()
        self.assertEqual(row[0], "")

    # 4. 同一照片多 detection 独立归类
    def test_same_photo_multi_detection_independent(self):
        ga = self.db.create_group(group_type="fursuit_character")
        gb = self.db.create_group(group_type="fursuit_character")
        self._add("p1.jpg", 0, unit(1.0), gid=ga)
        self._add("p2.jpg", 0, unit(0.0, 1.0), gid=gb)
        self._add("photo.jpg", 0, unit(0.95, 0.3118))   # det0 → A
        self._add("photo.jpg", 1, unit(0.3118, 0.95))   # det1 → B
        r = self._assign()
        self.assertEqual(r["joined"], 2)
        rows = dict(
            self.db.conn.execute(
                "SELECT detection_index, group_id FROM identity_image "
                "WHERE image_path='photo.jpg'"
            ).fetchall()
        )
        self.assertEqual(rows[0], ga)
        self.assertEqual(rows[1], gb)
        self.assertNotEqual(rows[0], rows[1])

    # 5. 人工合并保护：merge 后组 id 永久保留，增量不拆散
    def test_manual_merge_preserved(self):
        ga = self.db.create_group(group_type="fursuit_character")
        gb = self.db.create_group(group_type="fursuit_character")
        self._add("a1.jpg", 0, unit(0.9, 0.4359), gid=ga)
        self._add("b1.jpg", 0, unit(0.85, 0.5268), gid=gb)
        # 人工合并：gb 并入 ga
        self.db.merge_group_members(ga, [gb])
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM identity_image WHERE group_id=?", (ga,)
            ).fetchone()[0], 2,
        )
        # 增量：新 det 与 ga centroid 高相似 → 加入 ga
        self._add("new.jpg", 0, unit(0.87, 0.4924))
        r = self._assign()
        self.assertEqual(r["joined"], 1)
        # ga 保留、成员 3；gb 源组已删
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM identity_image WHERE group_id=?", (ga,)
            ).fetchone()[0], 3,
        )
        self.assertIsNone(
            self.db.conn.execute(
                "SELECT id FROM identity_group WHERE id=?", (gb,)
            ).fetchone(),
        )

    # 6. fursuit_visual / face 完全隔离
    def test_visual_face_isolated(self):
        gv = self.db.create_group(group_type="fursuit_character")
        self._add("v1.jpg", 0, unit(0.5, 0.8660), gid=gv, etype="fursuit_visual")
        self._add("v2.jpg", 0, unit(0.5, 0.8660), gid=gv, etype="fursuit_visual")
        gf = self.db.create_group(group_type="real_person")
        self._add("f1.jpg", 0, unit(0.0, 1.0), gid=gf, etype="face")
        # 新增 fursee 行触发增量
        self._add("new.jpg", 0, unit(0.95, 0.3118))
        r = self._assign()
        self.assertEqual(r["created"], 1)
        # visual/face 行数与 group_id 零变化
        vis = self.db.conn.execute(
            "SELECT group_id FROM identity_image WHERE embedding_type='fursuit_visual'"
        ).fetchall()
        self.assertEqual([v[0] for v in vis], [gv, gv])
        face = self.db.conn.execute(
            "SELECT group_id FROM identity_image WHERE embedding_type='face'"
        ).fetchall()
        self.assertEqual([v[0] for v in face], [gf])

    # 7. 幂等：第二次调用不重复处理/不重复建组
    def test_idempotent(self):
        ga = self.db.create_group(group_type="fursuit_character")
        self._add("p1.jpg", 0, unit(1.0), gid=ga)
        self._add("new.jpg", 0, unit(0.95, 0.3118))
        r1 = self._assign()
        self.assertEqual(r1["joined"], 1)
        n_groups_1 = self.db.conn.execute(
            "SELECT COUNT(*) FROM identity_group"
        ).fetchone()[0]
        r2 = self._assign()
        self.assertEqual(r2["joined"], 0)
        self.assertEqual(r2["created"], 0)
        self.assertEqual(r2["pending"], 0)
        n_groups_2 = self.db.conn.execute(
            "SELECT COUNT(*) FROM identity_group"
        ).fetchone()[0]
        self.assertEqual(n_groups_1, n_groups_2)

    # 8. 0.79 阈值边界
    def test_threshold_boundary(self):
        ga = self.db.create_group(group_type="fursuit_character")
        self._add("p1.jpg", 0, unit(1.0), gid=ga)
        # cos=0.792 ≥0.79 → 加入
        self._add("ok.jpg", 0, unit(0.792, 0.6105))
        # cos=0.785 <0.79 → 新建
        self._add("no.jpg", 0, unit(0.785, 0.6194))
        r = self._assign()
        self.assertEqual(r["joined"], 1)
        self.assertEqual(r["created"], 1)
        ok_gid = self.db.conn.execute(
            "SELECT group_id FROM identity_image WHERE image_path='ok.jpg'"
        ).fetchone()[0]
        no_gid = self.db.conn.execute(
            "SELECT group_id FROM identity_image WHERE image_path='no.jpg'"
        ).fetchone()[0]
        self.assertEqual(ok_gid, ga)
        self.assertNotEqual(no_gid, ga)


if __name__ == "__main__":
    unittest.main()
