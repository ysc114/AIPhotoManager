"""DuplicatesPage GUI 冒烟（② 疑似重复照片 · 视觉区块，offscreen）。

temp 照片目录 + temp 索引隔离；验证：自动扫描→候选组显示→
「保留此张」→ 待清理标记（不删除文件）→「忽略该组」→ 候选减少。
"""
import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from tests.test_visual_duplicates import make_scene


def settle(app, frames=20, dt=0.02):
    for _ in range(frames):
        t0 = time.time()
        app.processEvents()
        time.sleep(max(0.0, dt - (time.time() - t0)))


class VisualDuplicatesUiTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.photos = self.dir / "photos"
        self.photos.mkdir()
        make_scene(str(self.photos / "base.jpg"))
        make_scene(str(self.photos / "bright.jpg"), brightness=1.15)
        make_scene(str(self.photos / "shift.jpg"), roll=4)
        make_scene(str(self.photos / "other.jpg"), style="other")
        from ui.duplicates_page import DuplicatesPage
        self.index_path = str(self.dir / "visual.json")
        self.page = DuplicatesPage(
            photos_dir=str(self.photos), index_path=self.index_path)

    def tearDown(self):
        self.page.close()
        self.tmp.cleanup()

    def _wait_scan(self, timeout=10.0):
        t0 = time.time()
        while self.page._visual_worker is not None:
            settle(self.app, 4)
            if time.time() - t0 > timeout:
                break
        settle(self.app, 8)

    def test_scan_finds_group_and_keep_marks_pending(self):
        self._wait_scan()
        groups = self.page._visual_groups
        self.assertGreaterEqual(len(groups), 1, "应发现疑似重复组")
        top = groups[0]
        names = [p["name"] for p in top["photos"]]
        self.assertIn("base.jpg", names)
        self.assertNotIn("other.jpg", names)
        self.assertGreaterEqual(top["score"], 0.86)
        # 点击「保留此张」（第一张）
        buttons = [b for b in self.page.findChildren(QPushButton)
                   if b.text() == "保留此张"]
        self.assertGreater(len(buttons), 0)
        buttons[0].click()
        settle(self.app, 6)
        # 其余成员被标记待清理
        kept = top["photos"][0]["path"]
        self.assertTrue(self.page._visual.is_resolved(kept))
        self.assertTrue(self.page._visual.candidate_mark(top["photos"][1]["path"]))
        self.assertGreaterEqual(len(self.page._visual.pending_cleanup()), 1)
        # 未删除任何文件
        self.assertEqual(
            len(list(self.photos.iterdir())), 4,
            "视觉决策只标记，绝不删除文件")
        # 页面出现「已标记待清理」徽标
        labels = [l for l in self.page.findChildren(QLabel)
                  if "已标记待清理" in l.text()]
        self.assertGreaterEqual(len(labels), 1)

    def test_ignore_group_removes_candidate(self):
        self._wait_scan()
        groups = self.page._visual_groups
        self.assertGreaterEqual(len(groups), 1)
        before = len(groups)
        ignore_btn = [b for b in self.page.findChildren(QPushButton)
                      if b.text() == "忽略该组"]
        self.assertGreater(len(ignore_btn), 0)
        ignore_btn[0].click()
        settle(self.app, 6)
        self.assertLess(
            len(self.page._visual_groups), before,
            "忽略后该组不再推荐")
        # 决策持久化
        from core.visual_duplicates import VisualDuplicateIndex
        idx2 = VisualDuplicateIndex(str(self.photos), self.index_path)
        first = groups[0]
        self.assertTrue(
            idx2.is_ignored(first["photos"][0]["path"], first["photos"][1]["path"]))
        self.assertEqual(idx2.groups(), [], "重启后被忽略的组仍不推荐")


if __name__ == "__main__":
    unittest.main()
