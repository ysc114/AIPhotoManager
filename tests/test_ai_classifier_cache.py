# -*- coding: utf-8 -*-
"""ai_classifier 空缓存修复测试：tests/test_ai_classifier_cache.py

验证缓存命中逻辑（2026-08-25 修复：空 dict {} 视为无效缓存）：
1. cached=None       → 执行真实分析
2. cached={}         → 执行真实分析（本次 Bug 修复点）
3. cached=非空有效    → 命中缓存，不重复分析
4. 空缓存重新分析后  → 得到正常分类结果（set 写入有效值）
5. 已有正常缓存不受影响（非空缓存原样保留、命中）

全部使用 FakeCache / FakeModel，不触碰生产 analysis_cache.json 与身份库。
"""
import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from core.ai_classifier import AIClassifier


class FakeCache:
    """内存缓存替身（隔离磁盘，零写生产文件）。"""

    def __init__(self, data=None):
        raw = dict(data or {})
        self.d = {k.replace("\\", "/"): v for k, v in raw.items()}

    def get(self, image_path):
        return self.d.get(image_path.replace("\\", "/"))

    def set(self, image_path, result):
        self.d[image_path.replace("\\", "/")] = result


class FakeModel:
    """CLIP 替身：固定返回正常三级分类结果。"""

    def analyze(self, image):
        return {
            "category": "a person wearing a fursuit costume",
            "quality": 0.85,
            "scores": {"fursuit": 0.85, "other": 0.15},
            "layer1": {"category": "fursuit", "label_cn": "兽装人物", "confidence": 0.9},
            "layer2": None,
            "layer3": None,
        }


class AIClassifierCacheTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.img_path = os.path.join(self._tmp, "test.jpg")
        Image.new("RGB", (64, 64), (120, 120, 120)).save(self.img_path)
        self.fake_model = FakeModel()

    def _make_clf(self, cache):
        clf = AIClassifier(use_yolo=False)  # 跳过 YOLO，走全图分类
        clf.model = self.fake_model          # 注入替身模型（不触发 ModelHub）
        clf.cache = cache
        return clf

    # 1. cached=None → 真实分析
    def test_none_reruns_analysis(self):
        cache = FakeCache()
        clf = self._make_clf(cache)
        with patch.object(FakeModel, "analyze", wraps=self.fake_model.analyze) as spy:
            result = clf.analyze(self.img_path)
        spy.assert_called_once()
        self.assertEqual(result["category"], "a person wearing a fursuit costume")

    # 2. cached={} → 真实分析（Bug 修复点）
    def test_empty_dict_reruns_analysis(self):
        cache = FakeCache({self.img_path: {}})  # 历史脏数据空 dict
        clf = self._make_clf(cache)
        with patch.object(FakeModel, "analyze", wraps=self.fake_model.analyze) as spy:
            result = clf.analyze(self.img_path)
        spy.assert_called_once()
        self.assertEqual(result["category"], "a person wearing a fursuit costume")

    # 3. cached=非空有效 → 命中，不重复分析
    def test_valid_cache_hits_no_analysis(self):
        valid = {
            "category": "cached_cat",
            "quality": 0.1,
            "scores": {},
            "layer1": {"category": "cached_l1", "label_cn": "", "confidence": 0.1},
            "layer2": None,
            "layer3": None,
            "_cached_at": "2026-08-25 00:00:00",
        }
        cache = FakeCache({self.img_path: valid})
        clf = self._make_clf(cache)
        with patch.object(FakeModel, "analyze", wraps=self.fake_model.analyze) as spy:
            result = clf.analyze(self.img_path)
        spy.assert_not_called()
        self.assertEqual(result["category"], "cached_cat")

    # 4. 空缓存重新分析后得到正常分类结果（并回写缓存）
    def test_empty_cache_reanalyzed_gets_result(self):
        cache = FakeCache({self.img_path: {}})
        clf = self._make_clf(cache)
        clf.analyze(self.img_path)
        # 重分析后缓存被写回有效值，再次调用应命中
        cached = cache.get(self.img_path)
        self.assertTrue(cached, "空缓存重分析后应写回非空结果")
        self.assertEqual(cached.get("category"), "a person wearing a fursuit costume")
        with patch.object(FakeModel, "analyze", wraps=self.fake_model.analyze) as spy:
            clf.analyze(self.img_path)
        spy.assert_not_called()  # 第二次命中缓存

    # 5. 已有正常缓存不受影响（保留原值）
    def test_existing_valid_cache_untouched(self):
        valid = {
            "category": "keep_me",
            "quality": 0.2,
            "scores": {},
            "layer1": {"category": "l1", "label_cn": "", "confidence": 0.2},
            "layer2": None,
            "layer3": None,
            "_cached_at": "2026-08-25 00:00:00",
        }
        cache = FakeCache({self.img_path: valid})
        clf = self._make_clf(cache)
        clf.analyze(self.img_path)  # 命中，不覆盖
        self.assertEqual(cache.get(self.img_path)["category"], "keep_me")
        self.assertEqual(cache.get(self.img_path)["_cached_at"], "2026-08-25 00:00:00")


if __name__ == "__main__":
    unittest.main()
