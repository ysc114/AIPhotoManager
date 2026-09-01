"""全局搜索 + 底部 Dock 单元测试（离屏，只读查询不碰数据）。"""

import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from config.settings_manager import settings as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def settle(app, frames=40, dt=0.02):
    for _ in range(frames):
        t0 = time.time()
        app.processEvents()
        time.sleep(max(0.0, dt - (time.time() - t0)))


class SearchIndexTests(unittest.TestCase):
    """搜索索引：角色/照片/收藏匹配 + 只读。"""

    @classmethod
    def setUpClass(cls):
        from core.search_index import SearchIndex
        cls.idx = SearchIndex(project_root=ROOT)
        cls.idx.refresh()

    def test_index_built(self):
        self.assertGreaterEqual(len(self.idx._roles), 100)
        self.assertGreaterEqual(len(self.idx._photos), 100)

    def test_search_role_by_name(self):
        named = [g for g in self.idx._roles if g.get("name")]
        if not named:
            return  # 全库未命名角色时跳过名称断言
        g = named[0]
        r = self.idx.search(g["name"][:2])
        self.assertTrue(any(x.get("character_id") == g.get("character_id")
                            for x in r["roles"]), "角色名搜索应命中")

    def test_search_role_by_id(self):
        cid = str(self.idx._roles[0].get("character_id") or "")[:6]
        r = self.idx.search(cid)
        self.assertGreaterEqual(len(r["roles"]), 1)

    def test_search_role_by_category(self):
        r = self.idx.search("兽装")
        self.assertGreaterEqual(len(r["roles"]), 1)
        for g in r["roles"]:
            self.assertEqual(g.get("type"), "fursuit_character")

    def test_search_photo_by_filename(self):
        p = self.idx._photos[0]
        q = os.path.splitext(p["name"])[0][:4]
        r = self.idx.search(q)
        self.assertTrue(any(x["path"] == p["path"] for x in r["photos"]),
                        "文件名搜索应命中")

    def test_search_empty_query(self):
        """空关键词：API 返回受 limit 限制的全量（筛选可独立生效），
        面板显隐由 UI 层控制。"""
        r = self.idx.search("")
        self.assertLessEqual(len(r["roles"]), 15)
        self.assertLessEqual(len(r["photos"]), 20)

    def test_search_no_match(self):
        r = self.idx.search("__不存在_zzz__")
        self.assertEqual(r["roles"], [])
        self.assertEqual(r["photos"], [])


class SearchBarUITests(unittest.TestCase):
    """搜索框 UI + 跳转（复用现有页面）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.orig_nav = S.get("nav")
        from ui.main_window_v3 import MainWindow
        self.win = MainWindow()
        self.win._ui_ready = True
        self.win.show()
        settle(self.app, 10)

    def tearDown(self):
        for k, v in self.orig_nav.items():
            S.set(f"nav.{k}", v)
        self.win.close()

    def test_search_bar_present(self):
        sb = self.win.search_bar
        self.assertTrue(sb.isVisible())
        self.assertIn("搜索", sb._edit.placeholderText())

    def test_search_input_shows_panel(self):
        sb = self.win.search_bar
        sb._edit.setText("12")
        settle(self.app, 30)
        self.assertTrue(sb._panel_visible)
        self.assertGreater(sb._panel.count(), 0)
        sb._edit.clear()
        settle(self.app, 10)
        self.assertFalse(sb._panel_visible)

    def test_photo_result_opens_preview(self):
        from core.search_index import search_index
        search_index.refresh()
        photos = search_index._photos
        if not photos:
            return
        sb = self.win.search_bar
        sb._edit.setText(os.path.splitext(photos[0]["name"])[0][:4])
        settle(self.app, 30)
        for i in range(sb._panel.count()):
            w = sb._panel.itemWidget(sb._panel.item(i))
            if w and w.kind == "photo":
                sb._on_item_clicked(sb._panel.item(i))
                break
        settle(self.app, 15)
        self.assertEqual(self.win.content_stack.currentIndex(), 2)

    def test_role_result_opens_group(self):
        from core.search_index import search_index
        search_index.refresh()
        roles = search_index._roles
        if not roles:
            return
        sb = self.win.search_bar
        g = roles[0]
        cid = str(g.get("character_id") or "")[:6]
        sb._edit.setText(cid)
        settle(self.app, 30)
        for i in range(sb._panel.count()):
            w = sb._panel.itemWidget(sb._panel.item(i))
            if w and w.kind == "role":
                sb._on_item_clicked(sb._panel.item(i))
                break
        settle(self.app, 15)
        self.assertIn(self.win.content_stack.currentIndex(), (3, 4, 5))

    def test_search_no_db_write(self):
        """搜索只读：identity_db SHA 不变。"""
        import hashlib
        db = os.path.join(ROOT, "identity_db.sqlite")
        before = hashlib.sha256(open(db, "rb").read()).hexdigest()
        from core.search_index import search_index
        search_index.search("12")
        search_index.search("兽装")
        after = hashlib.sha256(open(db, "rb").read()).hexdigest()
        self.assertEqual(before, after)


class DockTests(unittest.TestCase):
    """底部 Dock：选中放大/胶囊/极光联动。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.orig_nav = S.get("nav")
        from ui.main_window_v3 import MainWindow
        self.win = MainWindow()
        self.win._ui_ready = True
        self.win.show()
        settle(self.app, 8)

    def tearDown(self):
        for k, v in self.orig_nav.items():
            S.set(f"nav.{k}", v)
        self.win.close()

    def test_dock_capsule_follows(self):
        bn = self.win.bottom_nav
        self.win._on_bottom_nav_changed(4)
        settle(self.app, 60)
        expect = bn._capsule_rect(4)
        self.assertLess(abs(bn._capsule[0] - expect.x()), 3)
        self.assertLess(abs(bn._capsule[1] - expect.width()), 3)

    def test_dock_aurora_off_keeps_glass(self):
        bn = self.win.bottom_nav
        S.set("aurora.enabled", False)
        settle(self.app, 8)
        self.assertTrue(bn.isVisible())
        self.assertFalse(bn._aurora._timer.isActive())
        S.set("aurora.enabled", True)

    def test_dock_ten_entries(self):
        self.assertEqual(self.win.bottom_nav._n, 10)
        keys = [k for k, _ in self.win.bottom_nav._entries]
        self.assertEqual(keys[0], "overview")
        self.assertIn("duplicates", keys)
        self.assertEqual(keys[-1], "settings")


if __name__ == "__main__":
    unittest.main()
