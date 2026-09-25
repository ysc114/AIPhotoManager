# -*- coding: utf-8 -*-
"""缓存/反馈文件持久化安全：原子写 + 损坏文件留存。

背景（2026-09-25）：analysis_cache.json 与 feedback.json 之前是直接覆盖写，
中途被杀/断电/同步盘打断会留下半截 JSON；下次启动按空内容加载，紧接着
一次保存就把人工分类/历史反馈覆盖没了。这里锁死两条不变量：
1. 保存走「临时文件 + 原子替换」，不留 .tmp；
2. 读到损坏文件先另存 .corrupt-*.bak，再按空内容继续（可人工恢复）。
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from core.ai_advisor import AIAdvisor
from core.analysis_cache import AnalysisCache


def _result(category="x", quality=0.9):
    return {"category": category, "quality": quality, "scores": {},
            "layer1": None, "layer2": None, "layer3": None}


class AnalysisCacheDurabilityTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "analysis_cache.json")

    def test_save_is_atomic_and_leaves_no_tmp(self):
        cache = AnalysisCache(cache_file=self.path)
        cache.set("photos/a.jpg", _result())
        self.assertTrue(os.path.exists(self.path))
        self.assertFalse(os.path.exists(self.path + ".tmp"),
                         "保存后不得残留临时文件")
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("photos/a.jpg", data)

    def test_corrupt_cache_quarantined(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('{"photos/a.jpg": {"_category_cn": "人物"')   # 半截 JSON
        cache = AnalysisCache(cache_file=self.path)
        self.assertEqual(cache._cache, {}, "损坏文件按空缓存处理")
        backups = [n for n in os.listdir(self.tmp) if ".corrupt-" in n]
        self.assertEqual(len(backups), 1, "损坏文件必须留存备份")
        with open(os.path.join(self.tmp, backups[0]), "r", encoding="utf-8") as f:
            self.assertIn("_category_cn", f.read(), "备份应保留原内容便于恢复")

    def test_corrupt_backup_survives_next_save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("not-json-at-all")
        cache = AnalysisCache(cache_file=self.path)
        cache.set("photos/b.jpg", _result("y", 0.5))
        backups = [n for n in os.listdir(self.tmp) if ".corrupt-" in n]
        self.assertEqual(len(backups), 1)
        with open(os.path.join(self.tmp, backups[0]), "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "not-json-at-all",
                             "备份内容必须原样保留，不被新保存覆盖")


    def test_write_failure_keeps_previous_file_intact(self):
        """写一半失败（模拟断电/被杀）→ 旧内容完好、不留 .tmp。"""
        cache = AnalysisCache(cache_file=self.path)
        cache.set("photos/a.jpg", _result())
        with open(self.path, "rb") as f:
            good = f.read()

        with mock.patch("core.analysis_cache.json.dump",
                        side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                cache.set("photos/b.jpg", _result("z", 0.1))

        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), good, "失败后旧缓存内容必须完好")
        self.assertFalse(os.path.exists(self.path + ".tmp"), "失败后不得残留 .tmp")

class FeedbackDurabilityTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "feedback.json")
        self.advisor = AIAdvisor()
        self.advisor.feedback_file = self.path
        # save_feedback 会同步写分析缓存 → 用 temp 实例隔离，绝不碰生产缓存
        import core.analysis_cache as ac
        patcher = mock.patch.object(
            ac, "get_cache",
            return_value=ac.AnalysisCache(
                cache_file=os.path.join(self.tmp, "cache.json")))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_feedback_appends_and_is_atomic(self):
        self.advisor.save_feedback("photos/a.jpg", "风景", "人物", "2026-09-25 10:00:00")
        self.advisor.save_feedback("photos/b.jpg", "人物", "风景", "2026-09-25 10:01:00")
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual([r["image_path"] for r in data],
                         ["photos/a.jpg", "photos/b.jpg"], "历史反馈不得丢失")
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_corrupt_feedback_quarantined(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('[{"image_path": "photos/old.jpg"')     # 半截 JSON
        self.advisor.save_feedback("photos/new.jpg", "风景", "人物", "2026-09-25 10:02:00")
        backups = [n for n in os.listdir(self.tmp) if ".corrupt-" in n]
        self.assertEqual(len(backups), 1, "损坏的反馈文件必须留存")
        with open(os.path.join(self.tmp, backups[0]), "r", encoding="utf-8") as f:
            self.assertIn("photos/old.jpg", f.read())
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual([r["image_path"] for r in data], ["photos/new.jpg"])

    def test_write_failure_keeps_previous_feedback(self):
        """写一半失败 → 历史反馈完好、不留 .tmp。"""
        self.advisor.save_feedback("photos/a.jpg", "风景", "人物", "2026-09-25 10:00:00")
        with open(self.path, "rb") as f:
            good = f.read()

        with mock.patch("core.ai_advisor.json.dump",
                        side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                self.advisor.save_feedback("photos/b.jpg", "人物", "风景", "2026-09-25 10:01:00")

        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), good, "失败后历史反馈必须完好")
        self.assertFalse(os.path.exists(self.path + ".tmp"), "失败后不得残留 .tmp")


if __name__ == "__main__":
    unittest.main()
