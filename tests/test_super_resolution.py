"""Small-image super-resolution tests; production model is loaded in one test."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from core.super_resolution import (
    SuperResolutionError, load_upscaler, upscale_images,
)


class SuperResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "photos"
        self.manifest = self.root / "cache" / "super_resolution.json"
        self.source = self.root / "original.png"
        Image.new("RGB", (13, 11), (100, 130, 170)).save(self.source)
        self.before = hashlib.sha256(self.source.read_bytes()).digest()

    def process(self, sources=None, scale=2, **kwargs):
        return upscale_images(sources or [self.source], scale, self.output,
                              self.manifest, **kwargs)

    def test_real_model_single_x2_and_x3_preserve_original(self):
        model = load_upscaler()
        for scale in (2, 3):
            result = self.process(scale=scale, model=model)
            self.assertEqual(result["new"], 1)
            with Image.open(result["outputs"][0]) as image:
                self.assertEqual(image.size, (13 * scale, 11 * scale))
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(), self.before)

    def test_batch_reuses_outputs_after_restart_and_continues_past_damage(self):
        import torch
        second = self.root / "second.png"
        Image.new("RGB", (8, 7), (5, 6, 7)).save(second)
        broken = self.root / "broken.png"
        broken.write_bytes(b"broken")
        model = torch.nn.Upsample(scale_factor=3, mode="nearest")
        first = self.process([self.source, broken, second], model=model)
        self.assertEqual(first["new"], 2)
        self.assertEqual(len(first["errors"]), 1)
        repeated = self.process([self.source, second], model=model)
        self.assertEqual(repeated["new"], 0)
        self.assertEqual(repeated["reused"], 2)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(), self.before)

    def test_existing_output_is_recovered_but_never_overwritten(self):
        import torch
        model = torch.nn.Upsample(scale_factor=3, mode="nearest")
        first = self.process(model=model)
        target = Path(first["outputs"][0])
        before = hashlib.sha256(target.read_bytes()).digest()
        self.manifest.unlink()
        recovered = self.process(model=model)
        self.assertEqual(recovered["reused"], 1)
        self.assertEqual(hashlib.sha256(target.read_bytes()).digest(), before)
        target.write_bytes(b"damaged")
        damaged = self.process(model=model)
        self.assertEqual(damaged["new"], 0)
        self.assertIn("未覆盖", damaged["errors"][0][1])
        self.assertEqual(target.read_bytes(), b"damaged")

    def test_cancel_during_tiles_leaves_no_partial_output(self):
        import torch
        Image.new("RGB", (260, 150), (10, 20, 30)).save(self.source)
        flag = []
        result = self.process(
            model=torch.nn.Upsample(scale_factor=3, mode="nearest"),
            progress=lambda *_: flag.append(True), cancelled=lambda: bool(flag))
        self.assertTrue(result["cancelled"])
        self.assertFalse(list(self.output.iterdir()))
        resumed = self.process(model=torch.nn.Upsample(scale_factor=3, mode="nearest"))
        self.assertEqual(resumed["new"], 1)

    def test_model_failure_is_reported_per_image_without_crash(self):
        with patch("core.model_hub.get_model_hub", side_effect=SuperResolutionError("模型不可用")):
            result = self.process()
        self.assertEqual(result["new"], 0)
        self.assertIn("模型不可用", result["errors"][0][1])

    def test_resource_failure_does_not_abort_batch(self):
        import torch
        second = self.root / "second.png"
        Image.new("RGB", (8, 8), "red").save(second)
        with patch("core.super_resolution._upscale_image", side_effect=[MemoryError("OOM"), None]):
            result = self.process([self.source, second], model=torch.nn.Identity())
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("OOM", result["errors"][0][1])
        self.assertEqual(result["new"], 1)

    def test_invalid_output_and_missing_model_are_explicit(self):
        self.output.write_text("blocked")
        with self.assertRaisesRegex(SuperResolutionError, "无法创建"):
            self.process()
        with patch("core.super_resolution.MODEL_PATH", self.root / "missing.pth"):
            with self.assertRaisesRegex(SuperResolutionError, "模型不存在"):
                load_upscaler()


if __name__ == "__main__":
    unittest.main()
