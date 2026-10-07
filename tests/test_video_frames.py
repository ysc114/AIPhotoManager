"""Video extraction uses tiny synthetic clips and never loads vision models."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from core.video_frames import (
    VideoFrameError, extract_video_frames, video_source_for_frame,
)


class VideoFrameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.photos = self.root / "photos"
        self.manifest = self.root / "cache" / "video_frames.json"

    def make_video(self, name="clip.avi", count=30, codec="MJPG"):
        path = self.root / name
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*codec),
                                 10, (64, 48))
        if not writer.isOpened():
            self.skipTest(f"OpenCV encoder unavailable: {codec}")
        for i in range(count):
            writer.write(np.full((48, 64, 3), i % 255, dtype=np.uint8))
        writer.release()
        return path

    def extract(self, source, interval=1, **kwargs):
        return extract_video_frames(source, interval, self.photos,
                                    self.manifest, **kwargs)

    def test_timed_frames_and_source_are_preserved(self):
        source = self.make_video()
        before = hashlib.sha256(source.read_bytes()).digest()
        result = self.extract(source, interval=1)
        self.assertEqual(len(result["frames"]), 3)
        self.assertEqual(result["new"], 3)
        self.assertFalse(result["cancelled"])
        self.assertTrue(all(Path(p).parent == self.photos for p in result["frames"]))
        self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)
        provenance = video_source_for_frame(result["frames"][1], self.manifest)
        self.assertEqual(provenance["source"], str(source))
        self.assertEqual(provenance["time_ms"], 1000)
        self.assertIsNone(video_source_for_frame(self.root / "other.jpg", self.manifest))

    def test_short_video_and_interval(self):
        source = self.make_video(count=4)
        result = self.extract(source, interval=5)
        self.assertEqual(len(result["frames"]), 1)

    def test_repeated_video_skips_and_missing_frame_is_rebuilt(self):
        source = self.make_video()
        first = self.extract(source)
        repeat = self.extract(source)
        self.assertTrue(repeat["skipped"])
        self.assertEqual(repeat["new"], 0)
        Path(first["frames"][1]).unlink()
        repaired = self.extract(source)
        self.assertFalse(repaired["skipped"])
        self.assertEqual(repaired["new"], 1)
        self.assertEqual(repaired["reused"], 2)

    def test_large_clip_progress_and_cancel_then_resume(self):
        source = self.make_video(count=180)
        reached = []
        result = self.extract(
            source, interval=1,
            progress=lambda done, total, saved: reached.append(done),
            cancelled=lambda: bool(reached and reached[-1] >= 30))
        self.assertTrue(result["cancelled"])
        self.assertLess(len(result["frames"]), 18)
        resumed = self.extract(source, interval=1)
        self.assertEqual(len(resumed["frames"]), 18)
        self.assertGreater(resumed["reused"], 0)

    def test_different_common_container_when_encoder_available(self):
        source = self.make_video("clip.mp4", count=10, codec="mp4v")
        result = self.extract(source, interval=1)
        self.assertEqual(len(result["frames"]), 1)

    def test_unicode_source_and_output_paths(self):
        source = self.make_video("猫咪.avi", count=10)
        photos = self.root / "抽帧结果"
        manifest = self.root / "记录" / "视频.json"
        result = extract_video_frames(source, 1, photos, manifest)
        self.assertEqual(len(result["frames"]), 1)
        self.assertEqual(video_source_for_frame(result["frames"][0], manifest)[
            "source"], str(source))

    def test_corrupt_video_and_invalid_output(self):
        source = self.root / "broken.avi"
        source.write_bytes(b"not a video")
        with self.assertRaisesRegex(VideoFrameError, "无法打开视频|没有可读取"):
            self.extract(source)
        good = self.make_video()
        blocked = self.root / "blocked"
        blocked.write_text("not a directory")
        with self.assertRaisesRegex(VideoFrameError, "无法创建抽帧输出目录"):
            extract_video_frames(good, 1, blocked, self.manifest)

    def test_manifest_records_partial_and_rejects_corruption(self):
        source = self.make_video(count=40)
        result = self.extract(source, cancelled=lambda: True)
        self.assertTrue(result["cancelled"])
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertFalse(next(iter(data["jobs"].values()))["complete"])
        self.manifest.write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(VideoFrameError, "记录"):
            self.extract(source)


if __name__ == "__main__":
    unittest.main()
