"""ThumbnailCache 缩略图缓存系统单元测试（temp 隔离，不碰生产缓存/数据库）。"""

import os
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QColor, QPainter, QFont
from PySide6.QtCore import QTimer

from core.thumbnail_cache import ThumbnailCache, SUPPORTED_SIZES


def _make_image(path, w, h, color, text):
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(*color))
    p = QPainter(img)
    p.setPen(QColor(255, 255, 255))
    p.setFont(QFont("Arial", 40))
    p.drawText(img.rect(), 0x84, text)
    p.end()
    return img.save(path)


def _settle(app, frames=80, dt=0.02):
    for _ in range(frames):
        t0 = time.time()
        app.processEvents()
        time.sleep(max(0.0, dt - (time.time() - t0)))


class ThumbnailCacheTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="thumbtest_")
        self.cache_dir = os.path.join(self.tmp, "thumbs")
        self.tc = ThumbnailCache(cache_dir=self.cache_dir, enabled=True)
        self.src = os.path.join(self.tmp, "src.jpg")
        _make_image(self.src, 1200, 800, (60, 90, 160), "SRC")

    def tearDown(self):
        self.tc.shutdown()

    # 1. 首次生成：256/512 缓存文件存在、webp、尺寸正确
    def test_generate(self):
        got = []
        self.tc.request(self.src, 256, on_ready=lambda p: got.append(p))
        self.tc.request(self.src, 512, on_ready=lambda p: got.append(p))
        _settle(self.app)
        self.assertEqual(len(got), 2)
        for p in got:
            self.assertTrue(os.path.isfile(p), p)
            self.assertTrue(p.endswith(".webp"), p)
        im = QImage(got[0])
        self.assertLessEqual(max(im.width(), im.height()), 256)
        im5 = QImage(got[1])
        self.assertLessEqual(max(im5.width(), im5.height()), 512)
        self.assertNotEqual(got[0], got[1])

    # 2. 二次命中：同步返回、不重新入队
    def test_hit_second_time(self):
        got = []
        self.tc.request(self.src, 256, on_ready=lambda p: got.append(p))
        _settle(self.app)
        q0 = self.tc._q.qsize()
        cp = self.tc.get_cached(self.src, 256)
        self.assertIsNotNone(cp)
        self.assertTrue(os.path.isfile(cp))
        self.assertEqual(self.tc._q.qsize(), q0)

    # 3. 原图内容修改 → 键失效 → 重新生成
    def test_regenerate_after_change(self):
        got = []
        self.tc.request(self.src, 256, on_ready=lambda p: got.append(p))
        _settle(self.app)
        old = got[0]
        self.assertIsNotNone(self.tc.get_cached(self.src, 256), "生成后应可命中")
        # 修改原图
        _make_image(self.src, 1200, 800, (30, 160, 90), "CHANGED")
        self.assertIsNone(self.tc.get_cached(self.src, 256), "修改后不应命中旧缓存")
        got.clear()
        self.tc.request(self.src, 256, on_ready=lambda p: got.append(p))
        _settle(self.app)
        self.assertEqual(len(got), 1)
        self.assertTrue(os.path.isfile(got[0]))
        self.assertNotEqual(got[0], old, "应生成新键缓存")

    # 4. bbox 裁剪
    def test_bbox_crop(self):
        src2 = os.path.join(self.tmp, "src2.png")
        _make_image(src2, 800, 1200, (140, 80, 60), "B2")
        got = []
        self.tc.request(src2, 256, bbox="[0.1, 0.1, 0.5, 0.6]",
                        on_ready=lambda p: got.append(p))
        _settle(self.app)
        self.assertTrue(got and os.path.isfile(got[0]))
        # 不同 bbox → 不同键
        got2 = []
        self.tc.request(src2, 256, bbox="[0.2, 0.2, 0.8, 0.8]",
                        on_ready=lambda p: got2.append(p))
        _settle(self.app)
        self.assertNotEqual(got[0], got2[0])

    # 5. 关闭缓存 → 同步回调 None，回退原图
    def test_disable_fallback(self):
        self.tc.set_enabled(False)
        got = []
        r = self.tc.request(self.src, 256, on_ready=lambda p: got.append(p))
        self.assertIsNone(r)
        self.assertEqual(got, [None])
        self.assertIsNone(self.tc.get_cached(self.src, 256))
        # 重新开启后可命中
        self.tc.set_enabled(True)
        got.clear()
        self.tc.request(self.src, 256, on_ready=lambda p: got.append(p))
        _settle(self.app)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0] and os.path.isfile(got[0]))

    # 6. 缺失文件 → 安全回退 None
    def test_missing_file(self):
        got = []
        self.tc.request(os.path.join(self.tmp, "nope.jpg"), 256,
                        on_ready=lambda p: got.append(p))
        _settle(self.app, 5)
        self.assertEqual(got, [None])

    # 7. 多请求不冻结 GUI（后台生成，主线程 QTimer 持续触发）
    def test_no_gui_freeze(self):
        ticks = []
        t = QTimer()
        t.setInterval(20)
        t.timeout.connect(lambda: ticks.append(1))
        t.start()
        got = []
        for i in range(20):
            self.tc.request(self.src, 256, bbox=f"[0.{i % 8}, 0.1, 0.5, 0.5]" if i % 3 else None,
                            on_ready=lambda p: got.append(p))
        _settle(self.app, 120)
        t.stop()
        self.assertGreaterEqual(len(got), 18, "并发请求应基本全部完成")
        self.assertGreaterEqual(len(ticks), 80, f"主线程疑似冻结（QTimer {len(ticks)} 次）")

    # 8. 尺寸支持
    def test_sizes(self):
        self.assertEqual(SUPPORTED_SIZES, (256, 512))
        self.assertEqual(self.tc._normalize_size(300), 256)
        self.assertEqual(self.tc._normalize_size(512), 512)


if __name__ == "__main__":
    unittest.main()
