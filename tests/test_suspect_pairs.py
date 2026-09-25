"""角色中心 2.0 · 第二阶段：疑似同一角色 —— 候选/拒绝/合并/撤销测试。

全部使用 temp 库隔离（不碰生产 identity_db）；决策 sidecar 与数据库
同目录生成、随 TemporaryDirectory 自动清理。
"""
import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from core.identity.manager import IdentityManager
from core.identity.suspects import (
    SuspectStore,
    compute_suspect_pairs,
    group_representatives,
    pick_merge_direction,
)


DIM = 8


def _unit_vec(cos_with_e0, axis=1):
    """R^DIM 里与 e0 夹角余弦 = cos_with_e0 的单位向量。

    分量落在 (e0, e_{axis}) 平面；不同向量给不同 axis，
    使它们的点积 = 两个余弦之积（残差轴互相正交，避免同平面
    夹角差导致余弦虚高）。
    """
    v = np.zeros(DIM, dtype=np.float32)
    v[0] = cos_with_e0
    v[axis] = math.sqrt(max(0.0, 1.0 - cos_with_e0 * cos_with_e0))
    return v


class SuspectPairCoreTests(unittest.TestCase):
    """候选生成 + 决策记录（纯 core，temp 库）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "identity_db.sqlite")
        self.mgr = IdentityManager(db_path=self.db_path)
        self.db = self.mgr.db

    def tearDown(self):
        self.mgr.close()
        self.tmp.cleanup()

    # ---------- 数据工厂 ----------

    def _mk_group(self, name="", emb=None, paths=None, group_type="fursuit_character"):
        """建组 + 每 path 一个 detection（默认 embedding_type=fursuit_fursee）。"""
        gid = self.db.create_group(group_type=group_type)
        if name:
            self.db.update_group(gid, name=name)
        emb = _unit_vec(1.0) if emb is None else np.asarray(emb, dtype=np.float32)
        for idx, path in enumerate(paths or []):
            self.db.add_image(
                gid,
                path,
                embedding=emb,
                embedding_type="fursuit_fursee",
                bbox=[idx, idx, idx + 10, idx + 10],
                confidence=0.9,
                detection_index=0,
            )
        return gid

    # ---------- 候选生成 ----------

    def test_candidates_only_above_floor_sorted_desc(self):
        """只返回 >= 0.60 的组对；相似度降序；fursuit_fursee 组之间计算。"""
        u = _unit_vec(1.0)
        v = _unit_vec(0.75, axis=1)
        y = _unit_vec(0.65, axis=2)
        g1 = self._mk_group("u组", emb=u, paths=["a.jpg"])
        g2 = self._mk_group("v组", emb=v, paths=["b.jpg"])
        g3 = self._mk_group("y组", emb=y, paths=["c.jpg"])
        g4 = self._mk_group("dup", emb=u, paths=["d.jpg"])  # 与 g1 完全同向量
        cands = self.mgr.get_suspect_candidates()
        sims = sorted((c["similarity"] for c in cands), reverse=True)
        # 全部同向量组对：1.0；cos>=0.60 的组对：0.75/0.65 …
        self.assertAlmostEqual(sims[0], 1.0, places=4)
        self.assertAlmostEqual(sims[1], 0.75, places=4)
        self.assertEqual(sims, sorted(sims, reverse=True))
        self.assertGreaterEqual(min(sims), 0.60 - 1e-9)
        pair_ids = {
            frozenset((c["group_a"]["character_id"], c["group_b"]["character_id"]))
            for c in cands
        }
        self.assertEqual(len(cands), 5)  # 1.0 / 0.75 / 0.75 / 0.65 / 0.65
        self.assertIn(frozenset((g1, g4)), pair_ids)
        self.assertIn(frozenset((g1, g2)), pair_ids)
        self.assertIn(frozenset((g1, g3)), pair_ids)  # 0.65 >= 0.60
        self.assertNotIn(frozenset((g2, g3)), pair_ids)  # 0.75*0.65 ≈ 0.49 < 0.60
        # 载荷包含 UI 需要的字段
        top = cands[0]
        self.assertIn("name", top["group_a"])
        self.assertIn("count", top["group_a"])
        self.assertIn("cover_image", top["group_a"])
        self.assertIn("detections", top["group_a"])

    def test_min_sim_parameter(self):
        """更高的候选线下限只影响推荐，不影响组数据。"""
        g1 = self._mk_group(emb=_unit_vec(1.0), paths=["a.jpg"])
        g2 = self._mk_group(emb=_unit_vec(0.75), paths=["b.jpg"])
        self.assertEqual(len(self.mgr.get_suspect_candidates(min_sim=0.60)), 1)
        self.assertEqual(len(self.mgr.get_suspect_candidates(min_sim=0.80)), 0)
        # 组本身保持独立（0.79 是 Fursee 自动合并线，候选功能不动它）
        self.assertEqual(len(self.db.get_images_by_group(g1)), 1)
        self.assertEqual(len(self.db.get_images_by_group(g2)), 1)

    def test_legacy_and_face_rows_not_in_candidates(self):
        """fursuit_visual（冻结）与 face 组不进入 Fursee 候选。"""
        g1 = self._mk_group(emb=_unit_vec(1.0), paths=["a.jpg"])
        # Legacy 冻结数据：同向量但 embedding_type=fursuit_visual
        legacy = self.db.create_group(group_type="fursuit_character")
        self.db.add_image(
            legacy, "legacy.jpg",
            embedding=_unit_vec(1.0), embedding_type="fursuit_visual",
            bbox=[0, 0, 10, 10], confidence=0.9, detection_index=0,
        )
        # 人脸组：同向量但 embedding_type=face
        person = self.db.create_group(group_type="real_person")
        self.db.add_image(
            person, "face.jpg",
            embedding=_unit_vec(1.0), embedding_type="face",
            bbox=[0, 0, 10, 10], confidence=0.9, detection_index=0,
        )
        cands = self.mgr.get_suspect_candidates()
        pair_ids = {
            frozenset((c["group_a"]["character_id"], c["group_b"]["character_id"]))
            for c in cands
        }
        self.assertEqual(len(cands), 0, "只应比较 Fursee 角色组")
        self.assertNotIn(frozenset((g1, legacy)), pair_ids)
        self.assertNotIn(frozenset((g1, person)), pair_ids)

    def test_multi_member_group_uses_normalized_centroid(self):
        """组代表 = mean → L2 归一化（与 incremental_assign 同口径）。"""
        self._mk_group(
            emb=_unit_vec(1.0),
            paths=["a1.jpg", "a2.jpg"],  # 同向量 → 代表仍是 e0
        )
        self._mk_group(emb=_unit_vec(0.75), paths=["b.jpg"])
        cands = self.mgr.get_suspect_candidates()
        self.assertEqual(len(cands), 1)
        self.assertAlmostEqual(cands[0]["similarity"], 0.75, places=4)

    # ---------- "不是同一角色" 决策 ----------

    def test_not_same_hides_pair_and_persists_across_reopen(self):
        g1 = self._mk_group(emb=_unit_vec(1.0), paths=["a.jpg"])
        g2 = self._mk_group(emb=_unit_vec(0.75), paths=["b.jpg"])
        self.assertTrue(self.mgr.mark_not_same(g1, g2))
        # 记录后该组对不再出现
        ids = {
            frozenset((c["group_a"]["character_id"], c["group_b"]["character_id"]))
            for c in self.mgr.get_suspect_candidates()
        }
        self.assertNotIn(frozenset((g1, g2)), ids)
        # sidecar 落盘
        store_path = SuspectStore.default_path(self.db_path)
        self.assertTrue(Path(store_path).is_file())
        with open(store_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIn(sorted([g1, g2]), data["not_same"])
        # 重开 Manager（模拟下次启动）判定仍在
        mgr2 = IdentityManager(db_path=self.db_path)
        try:
            ids2 = {
                frozenset((c["group_a"]["character_id"], c["group_b"]["character_id"]))
                for c in mgr2.get_suspect_candidates()
            }
            self.assertNotIn(frozenset((g1, g2)), ids2)
        finally:
            mgr2.close()

    def test_not_same_idempotent_and_validation(self):
        g1 = self._mk_group(emb=_unit_vec(1.0), paths=["a.jpg"])
        g2 = self._mk_group(emb=_unit_vec(0.75), paths=["b.jpg"])
        self.assertTrue(self.mgr.mark_not_same(g1, g2))
        self.assertFalse(self.mgr.mark_not_same(g2, g1), "重复判定应幂等 False")
        with self.assertRaises(ValueError):
            self.mgr.mark_not_same(g1, g1)
        with self.assertRaises(ValueError):
            self.mgr.mark_not_same("", g2)
        with self.assertRaises(ValueError):
            self.mgr.mark_not_same(g1, "no_such_group")

    # ---------- 合并（方向 / 保真 / 记录清理） ----------

    def test_pick_merge_direction_keeps_larger_group(self):
        big = {"character_id": "b1", "count": 5}
        small = {"character_id": "s1", "count": 2}
        target, source = pick_merge_direction(big, small)
        self.assertEqual(target["character_id"], "b1")
        self.assertEqual(source["character_id"], "s1")
        target2, source2 = pick_merge_direction(small, big)
        self.assertEqual(target2["character_id"], "b1")
        self.assertEqual(source2["character_id"], "s1")
        # 数量相同 → 保留第一个
        t3, s3 = pick_merge_direction(big, {"character_id": "x1", "count": 5})
        self.assertEqual(t3["character_id"], "b1")

    def test_merge_via_candidates_preserves_multi_detection_fields(self):
        """同框多 detection（同一照片两个角色）+ 字段保真 + 源组删除。"""
        emb_a = np.linspace(0.1, 0.9, DIM, dtype=np.float32)
        emb_b = np.linspace(0.9, 0.1, DIM, dtype=np.float32)
        g_a = self._mk_group(emb=emb_a, paths=["group.jpg", "other.jpg"])
        g_b = self.db.create_group(group_type="fursuit_character")
        self.db.add_image(
            g_b, "group.jpg", embedding=emb_b,
            embedding_type="fursuit_fursee",
            bbox=[50, 60, 90, 100], confidence=0.82, detection_index=1,
        )
        self.db.add_image(
            g_b, "third.jpg", embedding=emb_b,
            embedding_type="fursuit_fursee",
            bbox=[5, 6, 9, 10], confidence=0.71, detection_index=0,
        )
        self.db.update_group(g_b, name="Beta")
        result = self.mgr.merge_groups(g_a, [g_b])
        self.assertEqual(result["moved"], 2)
        self.assertIsNotNone(self.db.get_group(g_a))
        self.assertIsNone(self.db.get_group(g_b), "源组应被删除")
        rows = self.db.get_images_by_group(g_a)
        keys = {(r["image_path"], r["detection_index"]) for r in rows}
        self.assertEqual(
            keys,
            {("group.jpg", 0), ("other.jpg", 0), ("group.jpg", 1), ("third.jpg", 0)},
        )
        by_key = {(r["image_path"], r["detection_index"]): r for r in rows}
        self.assertEqual(by_key[("group.jpg", 1)]["bbox"], "[50, 60, 90, 100]")
        self.assertEqual(by_key[("group.jpg", 1)]["confidence"], 0.82)
        self.assertEqual(by_key[("group.jpg", 1)]["embedding_type"], "fursuit_fursee")
        np.testing.assert_array_equal(
            np.frombuffer(by_key[("group.jpg", 1)]["embedding"], dtype=np.float32),
            emb_b,
        )

    def test_merge_overrides_old_not_same_and_prunes_dead_groups(self):
        """显式合并覆盖旧"不是同一"判定；涉及被删源组的记录清理。"""
        g1 = self._mk_group(emb=_unit_vec(1.0), paths=["a.jpg"])
        g2 = self._mk_group(emb=_unit_vec(1.0), paths=["b.jpg"])
        g3 = self._mk_group(emb=_unit_vec(1.0), paths=["c.jpg"])
        self.assertTrue(self.mgr.mark_not_same(g1, g2))
        self.assertTrue(self.mgr.mark_not_same(g1, g3))
        self.mgr.merge_groups(g1, [g2])
        store = SuspectStore(SuspectStore.default_path(self.db_path))
        pairs = store.not_same_pairs()
        self.assertNotIn(frozenset((g1, g2)), pairs, "显式合并应覆盖旧判定")
        self.assertIn(frozenset((g1, g3)), pairs, "无关判定保留")
        self.assertIsNotNone(store.last_merge())
        # g2 已被合并删除 → 候选里自然不再出现；g1/g3 的"不是同一"
        # 判定仍生效（用户没说过它们相同），也不应再推荐
        ids = {
            frozenset((c["group_a"]["character_id"], c["group_b"]["character_id"]))
            for c in self.mgr.get_suspect_candidates()
        }
        self.assertNotIn(frozenset((g1, g3)), ids, "旧判定保留：g1/g3 不再推荐")
        self.assertNotIn(frozenset((g1, g2)), ids)

    # ---------- 撤销最近一次合并（单槽） ----------

    def test_undo_restores_groups_metadata_and_members(self):
        emb_a = np.linspace(0.2, 0.8, DIM, dtype=np.float32)
        emb_b = np.linspace(0.8, 0.2, DIM, dtype=np.float32)
        g_a = self._mk_group(emb=emb_a, paths=["a1.jpg", "a2.jpg"])
        g_b = self._mk_group(emb=emb_b, paths=["b1.jpg"])
        self.db.update_group(g_a, description="目标描述")
        self.db.update_group(g_b, name="Beta", description="源描述",
                             cover_image="b1.jpg")
        ids_before_a = {r["id"] for r in self.db.get_images_by_group(g_a)}
        ids_before_b = {r["id"] for r in self.db.get_images_by_group(g_b)}
        cover_before_a = self.db.get_group(g_a)["cover_image"]

        self.mgr.merge_groups(g_a, [g_b])
        self.assertIsNone(self.db.get_group(g_b))
        self.assertTrue(self.mgr.can_undo_last_merge())

        res = self.mgr.undo_last_merge()
        self.assertTrue(res["ok"])
        self.assertEqual(res["restored_members"], 1)
        self.assertEqual(res["restored_groups"][0]["group_id"], g_b)

        ga = self.db.get_group(g_a)
        gb = self.db.get_group(g_b)
        self.assertIsNotNone(gb, "源组行应被重建")
        self.assertEqual(gb["name"], "Beta")
        self.assertEqual(gb["description"], "源描述")
        self.assertEqual(gb["cover_image"], "b1.jpg")
        self.assertEqual(gb["type"], "fursuit_character")
        self.assertEqual(ga["cover_image"], cover_before_a, "目标组封面恢复")
        self.assertEqual(
            {r["id"] for r in self.db.get_images_by_group(g_a)}, ids_before_a
        )
        self.assertEqual(
            {r["id"] for r in self.db.get_images_by_group(g_b)}, ids_before_b
        )
        self.assertFalse(self.mgr.can_undo_last_merge())
        self.assertEqual(
            self.mgr.undo_last_merge(), {"ok": False, "reason": "no_record"}
        )

    def test_undo_only_reverts_last_merge(self):
        """单槽撤销：只撤销最近一次，不设多级系统。"""
        g_a = self._mk_group(emb=_unit_vec(1.0), paths=["a.jpg", "a2.jpg"])
        g_b = self._mk_group(emb=_unit_vec(1.0), paths=["b.jpg"])
        g_c = self._mk_group(emb=_unit_vec(1.0), paths=["c.jpg"])
        self.mgr.merge_groups(g_a, [g_b])  # 第一次合并
        self.mgr.merge_groups(g_a, [g_c])  # 第二次合并（覆盖快照）
        self.assertIsNone(self.db.get_group(g_b))
        self.assertIsNone(self.db.get_group(g_c))

        res = self.mgr.undo_last_merge()
        self.assertTrue(res["ok"])
        self.assertEqual(res["restored_groups"][0]["group_id"], g_c)
        self.assertIsNotNone(self.db.get_group(g_c), "第二次合并被撤销")
        self.assertIsNone(self.db.get_group(g_b), "第一次合并保持生效")
        self.assertEqual(
            {r["image_path"] for r in self.db.get_images_by_group(g_a)},
            {"a.jpg", "a2.jpg", "b.jpg"},
        )

    def test_undo_keeps_row_identity_and_schema_unchanged(self):
        """撤销前后行 id 不变、库 schema（user_version/表/列）零改动。"""
        tables_before = {
            r[0] for r in self.db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        version_before = self.db.conn.execute("PRAGMA user_version").fetchone()[0]
        g_a = self._mk_group(emb=_unit_vec(1.0), paths=["a.jpg", "a2.jpg"])
        g_b = self._mk_group(emb=_unit_vec(1.0), paths=["b.jpg"])
        self.mgr.mark_not_same(g_a, self._mk_group(emb=_unit_vec(0.5), paths=["z.jpg"]))
        self.mgr.merge_groups(g_a, [g_b])
        self.mgr.undo_last_merge()

        self.assertEqual(
            self.db.conn.execute("PRAGMA user_version").fetchone()[0],
            version_before,
        )
        tables_after = {
            r[0] for r in self.db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertEqual(tables_before, tables_after)
        # sidecar 是决策文件（不是 schema）
        path = SuspectStore.default_path(self.db_path)
        self.assertTrue(Path(path).is_file())
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIn("not_same", data)
        self.assertIsNone(data.get("last_merge"))

    # ---------- 纯函数 ----------

    def test_compute_suspect_pairs_pure_function(self):
        reps = {
            "u": _unit_vec(1.0).astype(np.float64),
            "v": _unit_vec(0.75, axis=1).astype(np.float64),
            "w": _unit_vec(0.30, axis=2).astype(np.float64),
        }
        pairs = compute_suspect_pairs(reps, excluded=(), min_sim=0.60, limit=10)
        self.assertEqual(len(pairs), 1)
        a, b, sim = pairs[0]
        self.assertEqual({a, b}, {"u", "v"})
        self.assertAlmostEqual(sim, 0.75, places=6)
        # 排除集合生效
        self.assertEqual(
            compute_suspect_pairs(
                reps, excluded=(frozenset(("u", "v")),), min_sim=0.60, limit=10
            ),
            [],
        )
        # 相似度降序 + limit
        reps2 = {"a": _unit_vec(1.0).astype(np.float64),
                 "b": _unit_vec(0.9, axis=1).astype(np.float64),
                 "c": _unit_vec(0.7, axis=2).astype(np.float64)}
        pairs2 = compute_suspect_pairs(reps2, min_sim=0.60, limit=10)
        self.assertEqual(
            [round(p[2], 4) for p in pairs2], [0.9, 0.7, 0.63]
        )
        self.assertEqual(
            compute_suspect_pairs(reps2, min_sim=0.60, limit=2),
            pairs2[:2],
        )

    def test_group_representatives_normalizes_mean(self):
        v = _unit_vec(0.8, axis=1).astype(np.float64)
        reps = group_representatives({"g1": [v, v * 2.0]})
        self.assertAlmostEqual(float(np.linalg.norm(reps["g1"])), 1.0, places=6)
        self.assertAlmostEqual(float(np.dot(reps["g1"], v)), 1.0, places=6)
        self.assertEqual(group_representatives({"empty": []}), {})


if __name__ == "__main__":
    unittest.main()
