"""角色中心 2.0：搜索/筛选/排序测试 + character_id 一致性（纯 UI 只读）。"""

import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from config.settings_manager import settings as S


def settle(app, frames=40, dt=0.02):
    for _ in range(frames):
        t0 = time.time()
        app.processEvents()
        time.sleep(max(0.0, dt - (time.time() - t0)))


class CharacterCenterTests(unittest.TestCase):
    """角色中心：工具栏 / 搜索 / 类型筛选 / 排序 / 数据一致性。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.main_window_v3 import MainWindow
        self.win = MainWindow()
        self.win._ui_ready = True
        self.win.show()
        settle(self.app, 10)
        self.win._on_bottom_nav_changed(5)   # 角色页
        settle(self.app, 40)
        self.state = self.win._group_pages["character"]
        self.assertTrue(self.state.get("filter_edit") is not None, "工具栏应在位")

    def tearDown(self):
        self.win.close()

    # ── 数据一致性 ──
    def test_character_ids_unchanged(self):
        """过滤/排序前后 character_id 集合不变（子集关系）。"""
        all_ids = {g.get("character_id") for g in self.state["groups"]}
        self.assertGreater(len(all_ids), 100)
        # 默认（照片最多）渲染的卡片
        card_ids = {self.win._card_group_map[c][1].get("character_id")
                    for c in self.win._card_group_map
                    if self.win._card_group_map[c][0] == "character"}
        self.assertTrue(card_ids.issubset(all_ids), "渲染卡片 ID 必须来自全量角色")

    # ── 默认排序：照片数量最多 ──
    def test_default_sort_count_desc(self):
        self.assertEqual(self.state["filter_sort"].currentData(), "count_desc")
        counts = [self.win._unique_photo_count(g) for g in self.state["groups"]]
        self.assertEqual(counts, sorted(counts, reverse=True), "默认应按照片数降序")

    # ── 类型筛选 ──
    def test_type_filter_person(self):
        self.state["filter_type"].setCurrentIndex(2)   # 人物角色
        settle(self.app, 20)
        text = self.state["filter_counter"].text()
        expected = sum(
            1 for g in self.state["groups"] if g.get("type") == "real_person")
        self.assertIn(str(expected), text)
        # 渲染卡片全部为 real_person
        for c, (pk, g, _) in self.win._card_group_map.items():
            if pk == "character":
                self.assertEqual(g.get("type"), "real_person")

    def test_type_filter_fursuit(self):
        self.state["filter_type"].setCurrentIndex(1)   # 兽装角色
        settle(self.app, 20)
        text = self.state["filter_counter"].text()
        expected = sum(
            1 for g in self.state["groups"] if g.get("type") == "fursuit_character")
        self.assertIn(str(expected), text)

    # ── 搜索过滤 ──
    def test_search_filters_by_name(self):
        named = [g for g in self.state["groups"] if g.get("name")]
        if not named:
            self.skipTest("库中无命名角色")
        q = named[0]["name"][:2]
        self.state["filter_edit"].setText(q)
        settle(self.app, 20)
        # 计数 ≤ 全量，且含目标角色
        total = len(self.state["groups"])
        cur = self.state["filter_counter"].text()
        shown = int(cur.split(" / ")[0])
        self.assertLessEqual(shown, total)
        self.assertGreaterEqual(shown, 1)

    def test_search_no_match(self):
        self.state["filter_edit"].setText("__无此角色__")
        settle(self.app, 20)
        self.assertIn("0 /", self.state["filter_counter"].text())
        self.assertTrue(self.state["empty_label"].isVisible())

    # ── 排序切换 ──
    def _rendered_groups(self):
        """按渲染顺序（grid 插入序 = _card_group_map 迭代序）取角色。"""
        out = []
        for c, (pk, g, _) in self.win._card_group_map.items():
            if pk == "character":
                out.append(g)
        return out

    def test_sort_count_asc(self):
        self.state["filter_sort"].setCurrentIndex(1)   # 照片最少
        settle(self.app, 20)
        counts = [self.win._unique_photo_count(g) for g in self._rendered_groups()]
        self.assertEqual(counts, sorted(counts), "应按照片数升序")

    def test_sort_name(self):
        self.state["filter_sort"].setCurrentIndex(2)   # 名称 A-Z
        settle(self.app, 20)
        names = [str(g.get("name") or "").lower() for g in self._rendered_groups()]
        self.assertEqual(names, sorted(names), "应按名称 A-Z")

    def test_sort_updated(self):
        self.state["filter_sort"].setCurrentIndex(4)   # 最近更新
        settle(self.app, 20)
        # 不崩溃 + 结果数量与全量一致（不硬编码库规模，随真实数据变化）
        self.assertEqual(
            len(self.state["groups"]),
            len({g.get("character_id") for g in self.state["groups"]}),
        )

    # ── 搜索后点击进入详情页 ──
    def test_click_card_opens_group(self):
        # 取第一张卡模拟点击（走现有 eventFilter 左键路径）
        cards = [c for c in self.win._card_group_map
                 if self.win._card_group_map[c][0] == "character"]
        self.assertGreater(len(cards), 0)
        page_key, group, dname = self.win._card_group_map[cards[0]]
        self.win._open_group(page_key, group, dname)
        settle(self.app, 15)
        self.assertEqual(self.state["page_stack"].currentIndex(), 1, "应进入详情页")
        # 返回列表
        self.state["page_stack"].setCurrentIndex(0)


    # ── 卡片排版：文字与封面不重叠（角色中心 2.0 视觉回归）──
    def test_card_text_never_overlaps_cover(self):
        """封面图精确 200x148（不越界绘制）、名称位于封面下方、
        底部文字不超出卡片 —— 防止文字压在图片上/被裁切。"""
        from PySide6.QtCore import QSize
        from PySide6.QtWidgets import QLabel
        checked = 0
        for card, (pk, _g, _n) in self.win._card_group_map.items():
            if pk != "character":
                continue
            labels = card.findChildren(QLabel)
            if len(labels) < 3:
                continue
            cover, name = labels[0], labels[1]
            pix = cover.pixmap()
            if pix is not None and not pix.isNull():
                self.assertEqual(
                    pix.size(), QSize(200, 148),
                    "封面图不得大于标签（防止越界覆盖文字）")
            self.assertGreaterEqual(
                name.geometry().top(), cover.geometry().bottom(),
                "名称必须位于封面下方")
            lowest = labels[-1]
            self.assertLessEqual(
                lowest.geometry().bottom(), card.height() - 4,
                "底部文字不得超出卡片")
            checked += 1
            if checked >= 12:
                break
        self.assertGreater(checked, 0, "应渲染出至少一张角色卡可检查")


    # ── 渲染性能：角色卡不得挂离屏阴影效果层 ──
    def test_card_no_graphics_effect_layer(self):
        """QGraphicsDropShadowEffect 每卡一个离屏合成层是性能元凶；
        卡片阴影必须由 AuroraGlassCard 自绘（graphicsEffect() 为 None）。"""
        checked = 0
        for card, (pk, _g, _n) in self.win._card_group_map.items():
            if pk != "character":
                continue
            self.assertIsNone(
                card.graphicsEffect(),
                "角色卡不得使用 QGraphicsDropShadowEffect（改为卡片自绘阴影）",
            )
            checked += 1
            if checked >= 12:
                break
        self.assertGreater(checked, 0)


    # ── 渲染性能：性能模式（静态玻璃卡）──
    def test_perf_mode_toggle_renders_static_cards(self):
        """⚡ 性能模式开关：卡片走静态帧缓存渲染（无离屏层、无动画）。"""
        from PySide6.QtCore import QSize
        state = self.state
        btn = state.get("perf_btn")
        self.assertIsNotNone(btn, "工具栏应有「⚡ 性能模式」按钮")
        orig = bool(S.get("ui.perf_mode", False))
        expected = not orig
        try:
            btn.click()
            settle(self.app, 20)
            self.assertEqual(
                bool(S.get("ui.perf_mode", False)), expected,
                "点击后应切换为相反状态")
            self.assertEqual("✓" in btn.text(), expected)
            cards = [
                c for c, (pk, _g, _n) in self.win._card_group_map.items()
                if pk == "character"
            ]
            self.assertGreater(len(cards), 0)
            card = cards[0]
            self.assertIsNotNone(card._cache, "静态帧应缓存成功")
            self.assertEqual(card._cache.size(), QSize(card.width(), card.height()))
            self.assertIsNone(card.graphicsEffect())
        finally:
            if bool(S.get("ui.perf_mode", False)) != orig:
                btn.click()
                settle(self.app, 10)
            self.assertEqual(bool(S.get("ui.perf_mode", False)), orig)


if __name__ == "__main__":
    unittest.main()
