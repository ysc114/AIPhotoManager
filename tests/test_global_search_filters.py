# -*- coding: utf-8 -*-
"""Spotlight 面板筛选（类型/收藏）：与顶部搜索条同一语义。"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.components.global_search import GlobalSearchPanel


def _group(cid, name, gtype, serial=1):
    return {"character_id": cid, "name": name, "type": gtype,
            "serial": serial, "images": [], "detections": [],
            "source_types": ["fursuit_fursee" if gtype == "fursuit_character"
                             else "face"]}


class _StubMgr:
    """只实现 handler 需要的两个只读接口。"""

    def __init__(self, groups, favs):
        self._groups = groups
        self._favs = favs
        outer = self

        class _Db:
            def list_favorites(self):
                return list(outer._favs)

        self.db = _Db()

    def get_groups(self, group_type=None, group_id=None):
        return list(self._groups)

    def close(self):
        pass


class _FakeIndex:
    def __init__(self, results):
        self._results = results

    def count(self):
        return len(self._results)

    def search_by_text(self, q, enc, top_k=4):
        return list(self._results)


class _FakeLoadedEncoder:
    def is_loaded(self):
        return True


class PanelFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = GlobalSearchPanel()
        self.panel.show_panel()
        self.app.processEvents()

    def tearDown(self):
        self.panel.hide_panel()
        self.panel.close()

    def test_default_filters(self):
        self.assertEqual(self.panel.filters(),
                         {"type_filter": "all", "favorite_only": False})

    def test_filter_change_emits_search_request(self):
        got = []
        self.panel.search_requested.connect(got.append)
        idx = self.panel._filter_type.findData("real_person")
        self.panel._filter_type.setCurrentIndex(idx)
        self.app.processEvents()
        self.assertEqual(self.panel.filters()["type_filter"], "real_person")
        self.assertEqual(got, [""], "筛选变化应立即用当前输入重跑")

        got.clear()
        self.panel._filter_fav.setChecked(True)
        self.app.processEvents()
        self.assertTrue(self.panel.filters()["favorite_only"])
        self.assertEqual(got, [""])

    def test_type_filter_limits_roles_section(self):
        from ui.main_window_v3 import MainWindow
        win = MainWindow()
        win._ui_ready = True
        try:
            groups = [_group("c1", "小白狼", "fursuit_character"),
                      _group("c2", "张三", "real_person")]
            stub = _StubMgr(groups, [])
            win._global_search._filter_type.setCurrentIndex(
                win._global_search._filter_type.findData("real_person"))
            with mock.patch("core.identity.get_reader", return_value=stub), \
                    mock.patch("core.visual_search.get_index",
                               return_value=_FakeIndex([])), \
                    mock.patch("core.visual_search.get_encoder",
                               return_value=_FakeLoadedEncoder()):
                # 非空查询：空查询在 handler 里会短路（只展示最近搜索）
                win._on_global_search_query("c")   # 两个组 id 均含 "c"
            items = win._global_search._items
            titles = [i["title"] for i in items if i.get("badge") == "角色"]
            self.assertEqual(len(titles), 1, titles)
            self.assertIn("张三", titles[0])
        finally:
            win.close()

    def test_favorite_only_skips_photo_section_and_filters_semantic(self):
        from ui.main_window_v3 import MainWindow
        win = MainWindow()
        win._ui_ready = True
        try:
            photos = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "photos")
            names = sorted(n for n in os.listdir(photos)
                           if os.path.splitext(n)[1].lower() in
                           (".jpg", ".jpeg", ".png", ".webp"))
            self.assertTrue(names)
            fav_path = os.path.join(photos, names[0]).replace("\\", "/")
            other_path = os.path.join(photos, names[1]).replace("\\", "/")
            stub = _StubMgr([], [fav_path])
            idx = _FakeIndex([{"path": fav_path, "similarity": 0.9},
                              {"path": other_path, "similarity": 0.8}])
            win._global_search._filter_fav.setChecked(True)
            stem = os.path.splitext(names[0])[0]
            with mock.patch("core.identity.get_reader", return_value=stub), \
                    mock.patch("core.visual_search.get_index",
                               return_value=idx), \
                    mock.patch("core.visual_search.get_encoder",
                               return_value=_FakeLoadedEncoder()):
                win._on_global_search_query(stem)
            badges = [i.get("badge") for i in win._global_search._items]
            self.assertNotIn("照片", badges, "只看收藏时不重复列出照片分区")
            sem_paths = [i["payload"]["path"] for i in win._global_search._items
                         if i.get("badge") == "语义"]
            self.assertEqual(sem_paths, [fav_path], "语义结果按收藏过滤")
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main()
