# -*- coding: utf-8 -*-
"""第三阶段：禁止无参全量聚类 / analyze_folder 改为增量分配 测试。

tests/test_no_full_recluster.py
覆盖：
1. cluster.run(None) 抛 ValueError（禁止无参全量）
2. cluster.run("fursuit_fursee") 显式定向仍可用
3. analyze_folder 不拆散已有组（人工合并组保持）
4. analyze_folder 对已入库照片幂等（查重跳过，无重复写）
5. face 增量路径独立可用
全部临时库，不触碰生产库、不触发 CLIP/Fursee 推理。
"""
import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from core.identity.database import IdentityDatabase
from core.identity.cluster import IdentityCluster
from core.identity.manager import IdentityManager

DIM = 8


def unit(*vals):
    v = np.zeros(DIM, dtype=np.float32)
    for i, val in enumerate(vals):
        v[i] = val
    n = np.linalg.norm(v)
    return (v / n).astype(np.float32) if n > 0 else v


class NoFullReclusterTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.db = IdentityDatabase(db_path=os.path.join(self._tmp, "t.db"))
        self.cluster = IdentityCluster(self.db)

    def tearDown(self):
        self.db.close()

    def _add(self, path, det, emb, gid="", etype="fursuit_fursee"):
        self.db.add_image(
            group_id=gid, image_path=path, embedding=emb,
            embedding_type=etype, detection_index=det,
            confidence=0.9, bbox=[0, 0, 10, 10],
        )

    # 1. run(None) 抛 ValueError
    def test_run_none_raises(self):
        with self.assertRaises(ValueError):
            self.cluster.run(None)
        with self.assertRaises(ValueError):
            self.cluster.run()

    # 2. 显式定向 run 仍可用
    def test_run_explicit_type_works(self):
        self._add("p1.jpg", 0, unit(1.0), gid="g1")
        stats = self.cluster.run(embedding_type="fursuit_fursee")
        self.assertIn("fursuit_fursee", stats)

    # 3. analyze_folder 不拆散已有组（人工合并保护）
    def test_analyze_folder_preserves_existing_groups(self):
        ga = self.db.create_group(group_type="fursuit_character")
        gb = self.db.create_group(group_type="fursuit_character")
        self._add("a1.jpg", 0, unit(0.9, 0.4359), gid=ga)
        self._add("b1.jpg", 0, unit(0.85, 0.5268), gid=gb)
        self.db.merge_group_members(ga, [gb])
        n_before = self.db.conn.execute(
            "SELECT COUNT(*) FROM identity_image WHERE group_id=?", (ga,)
        ).fetchone()[0]
        self.assertEqual(n_before, 2)

        mgr = IdentityManager(db_path=self.db.db_path)
        try:
            # 模拟"新照片入库"：patch 单图处理为写入一行未分配 det
            new_vec = unit(0.87, 0.4924)
            def fake_process(image_path):
                mgr.db.add_image(
                    group_id="", image_path=image_path.replace("\\", "/"),
                    embedding=new_vec, embedding_type="fursuit_fursee",
                    detection_index=0, confidence=0.9, bbox=[0, 0, 10, 10],
                )
            with patch.object(mgr, "_process_single_image", side_effect=fake_process):
                groups = mgr.analyze_folder(["new.jpg"])
            # ga 保持：成员 2 + 新 det 并入（cos≈0.99 → 加入 ga）
            n_after = self.db.conn.execute(
                "SELECT COUNT(*) FROM identity_image WHERE group_id=?", (ga,)
            ).fetchone()[0]
            self.assertEqual(n_after, 3, "已有组被拆散或新 det 未并入!")
            # gb 源组不复活
            self.assertIsNone(
                self.db.conn.execute(
                    "SELECT id FROM identity_group WHERE id=?", (gb,)
                ).fetchone()
            )
            # 返回 groups 非空
            self.assertTrue(groups)
        finally:
            mgr.close()

    # 4. analyze_folder 对已入库照片幂等（真实查重，不触发推理）
    def test_analyze_folder_idempotent_on_existing(self):
        self._add("p1.jpg", 0, unit(1.0), gid="g1")
        mgr = IdentityManager(db_path=self.db.db_path)
        try:
            n0 = self.db.conn.execute("SELECT COUNT(*) FROM identity_image").fetchone()[0]
            with patch.object(mgr, "_process_single_image", wraps=mgr._process_single_image) as spy:
                mgr.analyze_folder(["p1.jpg"])  # 已在库 → 查重跳过（不触 CLIP）
            spy.assert_called_once()
            n1 = self.db.conn.execute("SELECT COUNT(*) FROM identity_image").fetchone()[0]
            self.assertEqual(n0, n1, "已入库照片被重复写入!")
        finally:
            mgr.close()

    # 5. face 增量路径独立
    def test_face_incremental_independent(self):
        gf = self.db.create_group(group_type="real_person")
        self._add("f1.jpg", 0, unit(1.0), gid=gf, etype="face")
        self._add("fnew.jpg", 0, unit(0.95, 0.3118), etype="face")
        r = self.cluster.incremental_assign(
            embedding_type="face", threshold=0.92, margin=0.02
        )
        self.assertEqual(r["joined"], 1)
        row = self.db.conn.execute(
            "SELECT group_id FROM identity_image WHERE image_path='fnew.jpg'"
        ).fetchone()
        self.assertEqual(row[0], gf)
        # fursee 路径不受影响
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM identity_image WHERE embedding_type='fursuit_fursee'"
            ).fetchone()[0], 0,
        )


if __name__ == "__main__":
    unittest.main()
