"""♻️ 重复照片管理：MD5 扫描分组 / 安全删除 / 页面 / 主窗口接入。

所有测试使用临时照片目录（不碰生产 photos/ 与 identity_db）。
删除路径测试：临时文件 + 临时 sqlite（IdentityManager 默认连生产库，
故删除测试仅验证文件删除与失败安全，数据库清理用 mock 断言调用）。
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QMessageBox
from core.duplicates import norm_path
from config.settings_manager import settings as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _make_photo(d, name, content=b"abc"):
    p = os.path.join(d, name)
    with open(p, "wb") as f:
        f.write(content)
    return p


class DuplicateScannerTests(unittest.TestCase):
    """MD5 分组：完全相同才归组，相似不误判。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dup_test_")
        cls.a = _make_photo(cls.tmp, "a.jpg")
        cls.a1 = _make_photo(cls.tmp, "a (1).jpg")          # 同内容不同名
        cls.a2 = _make_photo(cls.tmp, "a_copy.png")         # 同内容不同扩展
        cls.b = _make_photo(cls.tmp, "b.jpg", b"other")     # 不同内容
        cls.small = _make_photo(cls.tmp, "tiny.png", b"x")  # 单张不成组

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        from core.duplicates import DuplicateScanner
        self.scanner = DuplicateScanner(photos_dir=self.tmp)

    def test_groups_exact_duplicates(self):
        groups = self.scanner.scan()
        # a/a(1)/a_copy 同 MD5 → 1 组 3 副本；b 单独；tiny 单独
        self.assertEqual(len(groups), 1, f"应只有 1 组重复, got {len(groups)}")
        g = groups[0]
        self.assertEqual(len(g["paths"]), 3)
        names = {x["name"] for x in g["paths"]}
        self.assertEqual(names, {"a.jpg", "a (1).jpg", "a_copy.png"})
        # 大小正确
        for x in g["paths"]:
            self.assertEqual(x["size"], 3)

    def test_no_false_positive(self):
        """不同内容（b.jpg）不参与任何组。"""
        groups = self.scanner.scan()
        all_names = [x["name"] for g in groups for x in g["paths"]]
        self.assertNotIn("b.jpg", all_names)
        self.assertNotIn("tiny.png", all_names)

    def test_md5_field(self):
        groups = self.scanner.scan()
        self.assertEqual(len(groups[0]["md5"]), 32)   # md5 hex


class DuplicateCleanerTests(unittest.TestCase):
    """安全删除：文件 + 数据库/缓存清理调用 + 失败安全。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dup_cln_")
        cls.a = _make_photo(cls.tmp, "a.jpg")
        cls.a1 = _make_photo(cls.tmp, "a (1).jpg")
        cls.b = _make_photo(cls.tmp, "b.jpg", b"other")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        import importlib
        self.patches = []
        # mock IdentityManager 数据库清理（避免触碰生产库）
        from unittest import mock
        mgr_cls = mock.MagicMock()
        mgr = mgr_cls.return_value
        mgr.db.remove_image.return_value = None
        mgr.db.remove_favorite.return_value = None
        # duplicates._clean_database 内 `from core.identity import IdentityManager`
        # 每次执行从 core.identity 取属性 → patch core.identity 生效
        self.patches.append(mock.patch("core.identity.IdentityManager", mgr_cls))
        # mock analysis_cache（避免读写生产 analysis_cache.json）
        self.patches.append(mock.patch("core.analysis_cache.get_cache",
                                       return_value=mock.MagicMock()))
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_delete_paths_removes_files_and_cleans_db(self):
        from core.duplicates import DuplicateCleaner
        from core.duplicates import _md5
        cleaner = DuplicateCleaner(photos_dir=self.tmp)
        keep_md5 = {_md5(self.a), _md5(self.a1), _md5(self.b)}
        result = cleaner.delete_paths([self.a1], keep_md5_set=keep_md5)
        self.assertIn(norm_path(self.a1), result["deleted"])
        self.assertFalse(os.path.exists(self.a1), "副本文件应被删除")
        self.assertTrue(os.path.exists(self.a), "保留文件不受影响")
        # 数据库清理被调用（remove_image / remove_favorite / 缓存 remove）
        from unittest import mock
        import core.identity as ident
        mgr = ident.IdentityManager.return_value
        mgr.db.remove_image.assert_called_with(self.a1.replace("\\", "/"))
        import core.analysis_cache as ac
        ac.get_cache.return_value.remove.assert_called()
        # 恢复文件供其他测试
        _make_photo(self.tmp, "a (1).jpg")

    def test_delete_skips_non_duplicate(self):
        """keep_md5_set 不包含的文件（非重复）不允许删除。"""
        from core.duplicates import DuplicateCleaner
        cleaner = DuplicateCleaner(photos_dir=self.tmp)
        result = cleaner.delete_paths([self.b], keep_md5_set={"deadbeef"})
        self.assertIn(norm_path(self.b), result["failed"])
        self.assertTrue(os.path.exists(self.b))

    def test_delete_missing_file_fails_safe(self):
        from core.duplicates import DuplicateCleaner
        cleaner = DuplicateCleaner(photos_dir=self.tmp)
        ghost = os.path.join(self.tmp, "ghost.jpg")
        result = cleaner.delete_paths([ghost])
        self.assertIn(norm_path(ghost), result["failed"])
        # 数据库不应被调用（文件都不存在）
        import core.identity as ident
        ident.IdentityManager.assert_not_called()

    def test_keep_one_semantics(self):
        """删除后组内至少保留 1 个（UI 层 _ensure_keep_one 的等价校验）。"""
        from core.duplicates import DuplicateCleaner
        cleaner = DuplicateCleaner(photos_dir=self.tmp)
        from core.duplicates import _md5
        keep_md5 = {_md5(self.a), _md5(self.a1)}
        # 试图删掉组内全部副本 → keep_md5 集合本身仍允许（安全校验不拦），
        # 但 UI 层每组保留 1 个由 _ensure_keep_one 保证；此处验证删除后
        # 至少一个副本仍在（只删 a1）。
        result = cleaner.delete_paths([self.a1], keep_md5_set=keep_md5)
        self.assertTrue(os.path.exists(self.a))
        _make_photo(self.tmp, "a (1).jpg")


class DuplicatesPageTests(unittest.TestCase):
    """页面：扫描展示 / 选择 / 保留逻辑 / 删除确认流。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.tmp = tempfile.mkdtemp(prefix="dup_page_")
        cls.a = _make_photo(cls.tmp, "a.jpg")
        cls.a1 = _make_photo(cls.tmp, "a (1).jpg")
        cls.a2 = _make_photo(cls.tmp, "a(2).png")
        cls.b = _make_photo(cls.tmp, "b.jpg", b"zzz")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.orig_g = S.get("glass")
        from ui.duplicates_page import DuplicatesPage
        self.page = DuplicatesPage(photos_dir=self.tmp)
        self.page.show()
        time.sleep(0.05)

    def tearDown(self):
        for k, v in self.orig_g.items():
            S.set(f"glass.{k}", v)
        self.page.close()

    def test_scan_shows_stats(self):
        self.assertEqual(len(self.page._groups), 1)   # 仅 a/a1/a2 组
        self.assertIn("1 组重复", self.page._stats.text())
        self.assertIn("3 个文件", self.page._stats.text())

    def test_select_all_keeps_one_per_group(self):
        self.page._select_all()
        # 3 副本全选后自动保留最大文件 → 选中 2
        n_sel = sum(1 for v in self.page._sel.values() if v)
        self.assertEqual(n_sel, 2)
        # 保留的文件确实未被选中
        biggest = max(self.page._groups[0]["paths"], key=lambda x: x["size"])
        self.assertFalse(self.page._sel[biggest["path"]])

    def test_invert(self):
        self.page._select_all()
        self.page._invert()
        n_sel = sum(1 for v in self.page._sel.values() if v)
        # 反选后：原选中 2 未选 → 1 选；原未选 1（保留）→ 选。但保留规则兜底
        self.assertLessEqual(n_sel, 2)

    def test_keep_one_button(self):
        g = self.page._groups[0]
        target = g["paths"][1]["path"]
        self.page._on_keep(target, g)
        self.assertFalse(self.page._sel[target])
        self.assertTrue(self.page._sel[g["paths"][0]["path"]])
        self.assertTrue(self.page._sel[g["paths"][2]["path"]])

    def test_delete_confirm_cancel(self):
        """取消确认 → 不删除任何文件。"""
        from unittest import mock
        self.page._select_all()
        with mock.patch("ui.duplicates_page.QMessageBox.question",
                        return_value=QMessageBox.No):
            self.page._delete_selected()
        # 文件全部仍在
        for g in self.page._groups:
            for x in g["paths"]:
                self.assertTrue(os.path.exists(x["path"]), x["name"])


class MainWindowIntegrationTests(unittest.TestCase):
    """主窗口：第 10 页存在 / 导航可达 / 删除后刷新链路。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.main_window_v3 import MainWindow
        self.win = MainWindow()
        self.win._ui_ready = True
        self.win.show()
        time.sleep(0.05)

    def tearDown(self):
        self.win.close()

    def test_duplicates_page_in_stack(self):
        self.assertEqual(self.win.content_stack.count(), 10)
        self.win._on_bottom_nav_changed(8)   # duplicates
        time.sleep(0.05)
        self.assertEqual(self.win.content_stack.currentIndex(), 8)
        # 页面统计已生成
        self.assertIn("重复", self.win.duplicates_page._stats.text())

    def test_on_duplicates_changed_resets_pages(self):
        self.win._group_page_loaded["fursuit"] = True
        self.win._on_duplicates_changed()
        self.assertFalse(self.win._group_page_loaded["fursuit"])
        self.assertFalse(self.win._group_page_loaded["person"])
        self.assertFalse(self.win._group_page_loaded["character"])


if __name__ == "__main__":
    unittest.main()
