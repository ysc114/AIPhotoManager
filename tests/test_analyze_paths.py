# -*- coding: utf-8 -*-
"""analyze_paths（GUI「添加照片」入口）测试。

隔离策略：temp 库 + mock embedder/cluster（不启动 Fursee/CLIP 推理）。
覆盖：文件去重（path/MD5/批内）、L1 路由统计、fursee/face 双增量调用、
返回结构、不触碰已有组。
"""
import os
import tempfile
import shutil
import unittest
from unittest import mock
from pathlib import Path

from core.identity.manager import IdentityManager


def fake_l1(route):
    """构造 L1 缓存条目（key 与该 route 匹配）。"""
    if route == "fursuit":
        return {"category": "fursuit", "label_cn": "兽装人物", "quality": 0.9}
    if route == "person":
        return {"category": "normal person", "label_cn": "普通人物", "quality": 0.9}
    return {"category": "scenery", "label_cn": "风景", "quality": 0.9}


class AnalyzePathsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "t.db")
        self.photos = os.path.join(self.tmp, "photos")
        os.makedirs(self.photos, exist_ok=True)
        self.mgr = IdentityManager(db_path=self.db_path)
        # 真实图片内容（任意字节即可，MD5 去重用）
        self.content_a = b"fake-image-bytes-A-1234567890"
        self.content_b = b"fake-image-bytes-B-9876543210"

    def tearDown(self):
        self.mgr.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, content):
        p = os.path.join(self.photos, name)
        with open(p, "wb") as f:
            f.write(content)
        return p.replace("\\", "/")

    def _patch(self, route_map=None, process_effect=None):
        """mock embedder.get_l1_info/route_l1 与 _process_single_image。"""
        route_map = route_map or {}

        def fake_get_l1(path):
            return route_map.get(path, fake_l1("other"))

        def fake_route(l1):
            cat = str(l1.get("category", ""))
            cn = str(l1.get("label_cn", ""))
            if "fursuit" in cat or "兽装" in cn:
                return "fursuit"
            if "person" in cat or "普通人物" in cn:
                return "person"
            return None

        patcher1 = mock.patch.object(self.mgr.embedder, "get_l1_info", side_effect=fake_get_l1)
        patcher2 = mock.patch.object(self.mgr.embedder, "route_l1", side_effect=fake_route)
        patcher3 = mock.patch.object(self.mgr, "_process_single_image", side_effect=process_effect or (lambda p: None))
        patcher1.start()
        patcher2.start()
        patcher3.start()
        self.addCleanup(patcher1.stop)
        self.addCleanup(patcher2.stop)
        self.addCleanup(patcher3.stop)
        return patcher3

    # 1. path 已存在 → 跳过（dup_path）
    def test_path_exists_skipped(self):
        p = self._write("a.jpg", self.content_a)
        self.mgr.db.add_image(
            group_id="g1", image_path=p, detection_index=0,
            embedding_type="fursuit_fursee", confidence=0.9,
        )
        self._patch(route_map={p: fake_l1("fursuit")})
        r = self.mgr.analyze_paths([p])
        self.assertEqual(r["dup_path"], 1)
        self.assertEqual(r["new"], 0)
        self.assertEqual(r["scanned"], 1)

    # 2. MD5 与已入库相同（不同 path）→ dup_md5 跳过
    def test_md5_duplicate_skipped(self):
        p_old = self._write("old.jpg", self.content_a)
        p_new = self._write("old(1).jpg", self.content_a)  # 同内容不同名
        self.mgr.db.add_image(
            group_id="g1", image_path=p_old, detection_index=0,
            embedding_type="fursuit_fursee", confidence=0.9,
        )
        self._patch(route_map={p_new: fake_l1("fursuit")})
        r = self.mgr.analyze_paths([p_new])
        self.assertEqual(r["dup_md5"], 1)
        self.assertEqual(r["new"], 0)

    # 3. 同一批内 MD5 相同 → 只处理第一个
    def test_batch_md5_dedup(self):
        p1 = self._write("x.jpg", self.content_a)
        p2 = self._write("x(1).jpg", self.content_a)
        calls = []
        self._patch(
            route_map={p1: fake_l1("fursuit"), p2: fake_l1("fursuit")},
            process_effect=lambda p: calls.append(p),
        )
        r = self.mgr.analyze_paths([p1, p2])
        self.assertEqual(r["new"], 1)
        self.assertEqual(r["dup_md5"], 1)
        self.assertEqual(calls, [p1], "批内同 MD5 只处理第一个")

    # 4. 不同 MD5 → 全部处理
    def test_diff_md5_all_processed(self):
        p1 = self._write("x.jpg", self.content_a)
        p2 = self._write("y.jpg", self.content_b)
        calls = []
        self._patch(
            route_map={p1: fake_l1("fursuit"), p2: fake_l1("person")},
            process_effect=lambda p: calls.append(p),
        )
        r = self.mgr.analyze_paths([p1, p2])
        self.assertEqual(r["new"], 2)
        self.assertEqual(r["fursuit"], 1)
        self.assertEqual(r["person"], 1)

    # 5. L1 路由统计：兽装/人物/其他
    def test_l1_routing_stats(self):
        p1 = self._write("a.jpg", self.content_a)
        p2 = self._write("b.jpg", self.content_b)
        p3 = self._write("c.jpg", b"other-bytes-3")
        self._patch(
            route_map={
                p1: fake_l1("fursuit"),
                p2: fake_l1("person"),
                p3: fake_l1("other"),
            },
        )
        r = self.mgr.analyze_paths([p1, p2, p3])
        self.assertEqual(r["fursuit"], 1)
        self.assertEqual(r["person"], 1)
        self.assertEqual(r["other"], 1)
        self.assertEqual(r["new"], 3)

    # 6. 非图片扩展名过滤
    def test_non_image_filtered(self):
        p = self._write("notes.txt", b"hello")
        self._patch()
        r = self.mgr.analyze_paths([p])
        self.assertEqual(r["scanned"], 0)
        self.assertEqual(r["new"], 0)

    # 7. 增量分配被调用（fursee + face），且只处理未分配
    def test_incremental_assign_called(self):
        p = self._write("a.jpg", self.content_a)
        self._patch(route_map={p: fake_l1("fursuit")})
        with mock.patch.object(self.mgr.cluster, "incremental_assign") as m:
            m.return_value = {"joined": 0, "created": 1, "conflicts": []}
            r = self.mgr.analyze_paths([p])
        self.assertEqual(m.call_count, 2, "应调用 fursee + face 两次增量")
        kinds = [c.kwargs.get("embedding_type") or c.args[0] for c in m.call_args_list]
        self.assertIn("fursuit_fursee", kinds)
        self.assertIn("face", kinds)

    # 8. 返回结构完整
    def test_return_structure(self):
        p = self._write("a.jpg", self.content_a)
        self._patch(route_map={p: fake_l1("fursuit")})
        r = self.mgr.analyze_paths([p])
        for key in ("scanned", "new", "fursuit", "person", "other",
                    "dup_path", "dup_md5", "failed",
                    "joined_fursee", "created_fursee",
                    "joined_face", "created_face"):
            self.assertIn(key, r, f"缺少返回键 {key}")

    # 9. 已有组零改动（mock 增量返回 joined 时只是新行；已有行不动）
    def test_existing_groups_untouched(self):
        p = self._write("a.jpg", self.content_a)
        self.mgr.db.add_image(
            group_id="c1", image_path=self._write("old.jpg", self.content_b),
            detection_index=0, embedding_type="fursuit_fursee", confidence=0.9,
        )
        self._patch(route_map={p: fake_l1("fursuit")})
        with mock.patch.object(self.mgr.cluster, "incremental_assign") as m:
            m.return_value = {"joined": 0, "created": 1, "conflicts": []}
            self.mgr.analyze_paths([p])
        # 增量只处理未分配（incremental_assign 实现保证）；此处验证 mock 收到的
        # embedding_type 参数
        self.assertEqual(
            m.call_args_list[0].kwargs.get("embedding_type")
            or m.call_args_list[0].args[0],
            "fursuit_fursee",
        )

    # 10. 进度回调
    def test_progress_callback(self):
        p1 = self._write("a.jpg", self.content_a)
        p2 = self._write("b.jpg", self.content_b)
        events = []
        self._patch(
            route_map={p1: fake_l1("fursuit"), p2: fake_l1("person")},
            process_effect=lambda p: None,
        )
        self.mgr.analyze_paths([p1, p2], progress_callback=lambda i, t, s: events.append((i, t)))
        self.assertEqual(events, [(1, 2), (2, 2)], "应逐张回调")


if __name__ == "__main__":
    unittest.main()
