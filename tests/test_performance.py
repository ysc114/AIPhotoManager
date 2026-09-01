"""性能回归护栏：关键路径耗时上限（宽松阈值，防灾难性回退）。

基准（2026-09-01 实测，offscreen，Windows 10）：
- 角色页纯构建 ~0.76s（占位优先后）
- 启动可交互（构造+show+首帧）~1.7s
- 搜索索引构建 ~0.12s；MD5 全库扫描 ~0.4s
阈值取实测 3~5 倍：性能明显回退（如重新引入同步封面解码/重复 MD5）
时触发，正常波动不误报。
"""

import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

# 阈值（秒）
LIMIT_ROLE_PAGE_LOAD = 3.0      # 纯构建（无事件循环）
LIMIT_STARTUP_INTERACTIVE = 4.0  # 构造 + show + 首帧
LIMIT_SEARCH_INDEX = 1.5        # 搜索索引构建
LIMIT_MD5_SCAN = 3.0            # 全库 MD5 扫描


def settle(app, frames=8, dt=0.02):
    for _ in range(frames):
        t0 = time.time()
        app.processEvents()
        time.sleep(max(0.0, dt - (time.time() - t0)))


class PerformanceGuardTests(unittest.TestCase):
    """性能护栏（宽松阈值；未达标即视为性能回退）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_role_page_load_within_budget(self):
        """角色页纯构建（数据+排序+226 卡占位渲染）≤ 3s。"""
        from ui.main_window_v3 import MainWindow
        win = MainWindow()
        win._ui_ready = True
        win.show()
        settle(self.app)
        t0 = time.perf_counter()
        win._on_bottom_nav_changed(5)   # 角色页
        elapsed = time.perf_counter() - t0
        win.close()
        self.assertLess(
            elapsed, LIMIT_ROLE_PAGE_LOAD,
            f"角色页加载 {elapsed:.2f}s 超限（{LIMIT_ROLE_PAGE_LOAD}s）——疑似封面同步解码/重复 MD5 回退")

    def test_startup_interactive_within_budget(self):
        """可交互启动（构造+show+首帧）≤ 4s。"""
        from ui.main_window_v3 import MainWindow
        t0 = time.perf_counter()
        win = MainWindow()
        win._ui_ready = True
        win.show()
        settle(self.app)
        elapsed = time.perf_counter() - t0
        win.close()
        self.assertLess(
            elapsed, LIMIT_STARTUP_INTERACTIVE,
            f"启动 {elapsed:.2f}s 超限（{LIMIT_STARTUP_INTERACTIVE}s）")

    def test_search_index_within_budget(self):
        """搜索索引构建 ≤ 1.5s。"""
        from core.search_index import SearchIndex
        idx = SearchIndex(project_root=os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        t0 = time.perf_counter()
        idx.refresh()
        elapsed = time.perf_counter() - t0
        self.assertLess(
            elapsed, LIMIT_SEARCH_INDEX,
            f"搜索索引 {elapsed:.2f}s 超限（{LIMIT_SEARCH_INDEX}s）")

    def test_md5_scan_within_budget(self):
        """全库 MD5 重复扫描 ≤ 3s。"""
        from core.duplicates import DuplicateScanner
        t0 = time.perf_counter()
        DuplicateScanner().scan()
        elapsed = time.perf_counter() - t0
        self.assertLess(
            elapsed, LIMIT_MD5_SCAN,
            f"MD5 扫描 {elapsed:.2f}s 超限（{LIMIT_MD5_SCAN}s）")


if __name__ == "__main__":
    unittest.main()
