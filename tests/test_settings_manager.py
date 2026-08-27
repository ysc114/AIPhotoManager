"""SettingsManager 配置管理单元测试（temp 文件隔离，不碰生产配置）。"""

import json
import os
import tempfile
import unittest

from config.settings_manager import SettingsManager, DEFAULT_SETTINGS


class SettingsManagerTests(unittest.TestCase):

    def setUp(self):
        self.tmp = os.path.join(tempfile.mkdtemp(), "settings_test.json")
        self.sm = SettingsManager(self.tmp)

    def tearDown(self):
        try:
            os.remove(self.tmp)
        except OSError:
            pass

    # 1. 默认配置完整
    def test_defaults(self):
        self.assertEqual(self.sm.get("ui.mode"), "new")
        self.assertEqual(self.sm.get("ui.liquid_glass"), True)
        self.assertEqual(self.sm.get("ai.fursuit_threshold"), 0.79)
        self.assertEqual(self.sm.get("ai.face_threshold"), 0.92)
        self.assertEqual(self.sm.get("ai.fursee_eps"), 0.6481)
        self.assertEqual(self.sm.get("ai.min_samples"), 1)
        self.assertEqual(self.sm.get("backup.keep_count"), 7)
        self.assertEqual(self.sm.get("notifications.error"), True)

    # 2. 点号路径读写 + 持久化往返
    def test_set_get_roundtrip(self):
        self.sm.set("ui.mode", "classic")
        self.sm.set("ui.liquid_glass", False)
        sm2 = SettingsManager(self.tmp)
        self.assertEqual(sm2.get("ui.mode"), "classic")
        self.assertEqual(sm2.get("ui.liquid_glass"), False)
        # 未改动的默认键保留
        self.assertEqual(sm2.get("ai.fursuit_threshold"), 0.79)

    # 3. set_many 批量
    def test_set_many(self):
        self.sm.set_many({
            "scan.auto_scan_photos": False,
            "backup.keep_count": 3,
            "notifications.analysis_done": False,
        })
        sm2 = SettingsManager(self.tmp)
        self.assertEqual(sm2.get("scan.auto_scan_photos"), False)
        self.assertEqual(sm2.get("backup.keep_count"), 3)
        self.assertEqual(sm2.get("notifications.analysis_done"), False)

    # 4. 损坏 JSON 回退默认
    def test_corrupt_file_falls_back(self):
        with open(self.tmp, "w", encoding="utf-8") as f:
            f.write("{ not valid json !!")
        sm = SettingsManager(self.tmp)
        self.assertEqual(sm.get("ui.mode"), "new")
        self.assertEqual(sm.get("ai.fursee_eps"), 0.6481)

    # 5. 缺失键返回默认值
    def test_missing_key(self):
        self.assertIsNone(self.sm.get("nope.does_not_exist"))
        self.assertEqual(self.sm.get("nope.x", 42), 42)

    # 6. 默认配置结构完整性（10 个分区）
    def test_default_sections(self):
        for sec in ("ui", "ai", "scan", "storage", "backup",
                    "notifications", "data", "advanced"):
            self.assertIn(sec, DEFAULT_SETTINGS)
            self.assertIsInstance(DEFAULT_SETTINGS[sec], dict)


if __name__ == "__main__":
    unittest.main()
