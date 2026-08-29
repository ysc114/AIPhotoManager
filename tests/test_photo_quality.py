"""photo_quality 角色内 AI 照片精选单元测试（temp 隔离，不碰生产库/生产缓存）。"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw, ImageFilter

from core.photo_quality.technical import technical_metrics, technical_score
from core.photo_quality.aesthetic import aesthetic_score
from core.photo_quality.duplicate import group_duplicates, similarity
from core.photo_quality.scorer import PhotoQualityAnalyzer


def _mk_img(w=400, h=300, color=(110, 140, 170)):
    img = Image.new("RGB", (w, h), color)
    dr = ImageDraw.Draw(img)
    for i in range(0, w, 30):
        dr.line([(i, 0), (i, h)], fill=(200, 90, 60), width=3)
    for j in range(0, h, 25):
        dr.line([(0, j), (w, j)], fill=(60, 200, 120), width=2)
    dr.ellipse([100, 80, 300, 220], outline=(255, 255, 255), width=4)
    return img


class TechnicalTests(unittest.TestCase):
    """技术指标：清晰图应显著高于模糊图。"""

    def test_sharpness_separates(self):
        sharp = technical_metrics(_mk_img())
        blur = technical_metrics(_mk_img().filter(ImageFilter.GaussianBlur(6)))
        self.assertGreater(sharp["sharpness"], blur["sharpness"])
        self.assertGreater(sharp["edge"], blur["edge"])
        self.assertGreaterEqual(technical_score(sharp), technical_score(blur))

    def test_exposure_penalizes_dark_bright(self):
        base = _mk_img()
        m_norm = technical_metrics(base)
        m_dark = technical_metrics(base.point(lambda x: x * 0.1))
        m_bright = technical_metrics(base.point(lambda x: min(255, x + 120)))
        self.assertGreater(m_norm["exposure"], m_dark["exposure"])
        self.assertGreater(m_norm["exposure"], m_bright["exposure"])

    def test_aesthetic_in_range(self):
        m = technical_metrics(_mk_img())
        a = aesthetic_score(_mk_img(), metrics=m)
        self.assertTrue(0.0 <= a <= 1.0)


class DuplicateTests(unittest.TestCase):
    """近似分组：复制/微调应入组，完全不同应分开。"""

    def setUp(self):
        self.base = _mk_img()

    def test_similarity_copy(self):
        s = similarity(self.base, self.base.copy())
        self.assertGreater(s, 0.9)

    def test_grouping(self):
        items = [
            ("a", self.base),
            ("b", self.base.copy()),
            ("c", self.base.rotate(2, expand=True)),
            ("d", Image.new("RGB", (400, 300), (30, 30, 200))),
        ]
        groups = group_duplicates(items)
        self.assertEqual(len(groups), 1)
        members = set(groups[0])
        self.assertIn("a", members)
        self.assertIn("b", members)
        self.assertIn("c", members)
        self.assertNotIn("d", members)


class ScorerTests(unittest.TestCase):
    """评分入口 + 缓存 + 单角色隔离。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pqtest_")
        self.cache_file = os.path.join(self.tmp, "pq.json")
        self.an = PhotoQualityAnalyzer(cache_file=self.cache_file)

    def _mk(self, name, img=None):
        p = os.path.join(self.tmp, name)
        (img or _mk_img()).save(p)
        return p

    def test_analyze_role_pipeline(self):
        p1 = self._mk("a.png")
        p2 = self._mk("a_copy.png", _mk_img())
        p3 = self._mk("other.png", Image.new("RGB", (400, 300), (20, 20, 200)))
        r = self.an.analyze_role("roleX", [p1, p2, p3])
        self.assertEqual(r["analyzed"], 3)
        self.assertEqual(r["cached"], 0)
        self.assertGreaterEqual(r["total"], 1)   # 至少一个精选
        # 相似组包含 a/a_copy
        members = {m for g in r["groups"] for m in g["members"]}
        self.assertIn(p1, members)
        self.assertIn(p2, members)
        # 每个精选都有理由
        for pk in r["picks"]:
            self.assertTrue(pk["reason"])
            self.assertTrue(0.0 <= pk["score"] <= 1.0)

    def test_cache_hit(self):
        photos = [self._mk("a.png"), self._mk("b.png", _mk_img(300, 300))]
        self.an.analyze_role("roleA", photos)
        r2 = self.an.analyze_role("roleA", photos)
        self.assertEqual(r2["analyzed"], 0)
        self.assertEqual(r2["cached"], 2)
        # 持久化：新实例读同一缓存
        an2 = PhotoQualityAnalyzer(cache_file=self.cache_file)
        entry = an2.get_role_result("roleA")
        self.assertIsNotNone(entry)
        self.assertGreaterEqual(entry["total"], 0)

    def test_single_role_isolated(self):
        a = [self._mk("a1.png"), self._mk("a2.png", _mk_img(300, 300, (200, 80, 80)))]
        b = [self._mk("b1.png", Image.new("RGB", (300, 200), (10, 10, 10)))]
        self.an.analyze_role("role1", a)
        self.an.analyze_role("role2", b)
        self.assertTrue(self.an.has_role("role1"))
        self.assertTrue(self.an.has_role("role2"))
        # role2 的结果不包含 role1 的照片
        r2 = self.an.get_role_result("role2")
        paths2 = {pk["path"] for pk in r2["picks"]}
        self.assertNotIn(a[0], paths2)

    def test_regenerate_after_change(self):
        p = self._mk("x.png")
        self.an.analyze_role("r", [p])
        # 修改文件内容
        _mk_img().save(p)
        r2 = self.an.analyze_role("r", [p])
        self.assertGreaterEqual(r2["analyzed"], 1)  # 文件变化 → 重算

    def test_missing_photo_skipped(self):
        r = self.an.analyze_role("r", [os.path.join(self.tmp, "nope.png")])
        self.assertEqual(r["analyzed"], 1)  # 尝试分析但被跳过
        self.assertGreaterEqual(r["total"], 0)


if __name__ == "__main__":
    unittest.main()
