"""Non-destructive, resumable image super-resolution using a tiled x3 model."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "models" / "superres_epoch100-44c6958e.pth"
MODEL_SHA256 = "44c6958ea9df3886ef120bad1fc827ffb913e4e6f667b24dc7aea9b032b6df75"
TILE = 128
OVERLAP = 8


class SuperResolutionError(RuntimeError):
    pass


class _Cancelled(Exception):
    pass


def load_upscaler():
    """Load the official PyTorch x3 example model on CPU, only when requested."""
    if not MODEL_PATH.is_file():
        raise SuperResolutionError(f"超分模型不存在：{MODEL_PATH}")
    if hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() != MODEL_SHA256:
        raise SuperResolutionError(f"超分模型校验失败：{MODEL_PATH}")
    try:
        import torch
        from torch import nn

        class Net(nn.Module):
            def __init__(self):
                super().__init__()
                self.conv1 = nn.Conv2d(1, 64, 5, padding=2)
                self.conv2 = nn.Conv2d(64, 64, 3, padding=1)
                self.conv3 = nn.Conv2d(64, 32, 3, padding=1)
                self.conv4 = nn.Conv2d(32, 9, 3, padding=1)
                self.pixel_shuffle = nn.PixelShuffle(3)

            def forward(self, image):
                image = torch.relu(self.conv1(image))
                image = torch.relu(self.conv2(image))
                image = torch.relu(self.conv3(image))
                return self.pixel_shuffle(self.conv4(image))

        model = Net()
        model.load_state_dict(torch.load(
            MODEL_PATH, map_location="cpu", weights_only=True), strict=True)
        return model.eval()
    except Exception as exc:
        raise SuperResolutionError(f"超分模型加载失败：{exc}") from exc


def _read_manifest(path):
    if not path.exists():
        return {"version": 1, "jobs": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SuperResolutionError(f"无法读取超分记录：{exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
        raise SuperResolutionError(f"超分记录格式无效：{path}")
    return data


def _save_manifest(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise SuperResolutionError(f"无法保存超分记录（磁盘空间或权限）：{exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _upscale_image(source, target, scale, model, cancelled, progress):
    import numpy as np
    import torch

    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened).convert("YCbCr")
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise SuperResolutionError(f"无法读取图片：{exc}") from exc
    width, height = image.size
    pixels = width * height * scale * scale
    if pixels > 200_000_000:
        raise SuperResolutionError("输出超过 2 亿像素，请降低倍率；原图未修改")
    needed = max(64 * 1024 * 1024, pixels * 5)
    try:
        if shutil.disk_usage(target.parent).free < needed:
            raise SuperResolutionError("输出目录磁盘空间不足；原图未修改")
    except OSError as exc:
        raise SuperResolutionError(f"无法检查输出目录空间：{exc}") from exc

    result = Image.new("RGB", (width * scale, height * scale))
    tiles_x = (width + TILE - 1) // TILE
    tiles_y = (height + TILE - 1) // TILE
    total_tiles = tiles_x * tiles_y
    done = 0
    for top in range(0, height, TILE):
        for left in range(0, width, TILE):
            if cancelled and cancelled():
                raise _Cancelled()
            right, bottom = min(left + TILE, width), min(top + TILE, height)
            box = (max(0, left - OVERLAP), max(0, top - OVERLAP),
                   min(width, right + OVERLAP), min(height, bottom + OVERLAP))
            y, cb, cr = image.crop(box).split()
            tensor = torch.from_numpy(np.asarray(y).copy()).float().div_(255).unsqueeze(0).unsqueeze(0)
            with torch.inference_mode():
                enhanced = model(tensor).squeeze().clamp(0, 1).mul(255).byte().numpy()
            enhanced_y = Image.fromarray(enhanced, "L")
            patch_size = enhanced_y.size
            rgb = Image.merge("YCbCr", (
                enhanced_y, cb.resize(patch_size, Image.Resampling.BICUBIC),
                cr.resize(patch_size, Image.Resampling.BICUBIC))).convert("RGB")
            core = ((left - box[0]) * 3, (top - box[1]) * 3,
                    (right - box[0]) * 3, (bottom - box[1]) * 3)
            rgb = rgb.crop(core)
            if scale == 2:
                rgb = rgb.resize(((right - left) * 2, (bottom - top) * 2),
                                 Image.Resampling.LANCZOS)
            result.paste(rgb, (left * scale, top * scale))
            done += 1
            if progress:
                progress(done, total_tiles)
    if cancelled and cancelled():
        raise _Cancelled()
    temporary = target.with_name(target.name + ".tmp")
    try:
        result.save(temporary, format="PNG")
        if cancelled and cancelled():
            raise _Cancelled()
        os.replace(temporary, target)
    except OSError as exc:
        raise SuperResolutionError(f"保存超分图片失败（磁盘空间或权限）：{exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def upscale_images(sources, scale, output_dir, manifest_path, progress=None,
                   cancelled=None, model=None):
    """Process a batch; image errors are reported individually and do not abort it."""
    if scale not in (2, 3):
        raise SuperResolutionError("仅支持 2 倍或 3 倍超分")
    output = Path(output_dir).resolve()
    try:
        output.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SuperResolutionError(f"无法创建超分输出目录：{exc}") from exc
    record = Path(manifest_path).resolve()
    manifest = _read_manifest(record)
    source_list = [Path(path).resolve() for path in sources]
    report = {"outputs": [], "new": 0, "reused": 0, "errors": [],
              "cancelled": False, "output_dir": str(output)}
    model_error = None
    for index, source in enumerate(source_list):
        if cancelled and cancelled():
            report["cancelled"] = True
            break
        try:
            if not source.is_file():
                raise SuperResolutionError(f"原图不存在：{source}")
            stat = source.stat()
            signature = {"source": str(source), "size": stat.st_size,
                         "mtime_ns": stat.st_mtime_ns, "scale": scale,
                         "model": MODEL_SHA256}
            key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:20]
            target = output / f"upscaled_{key}_x{scale}.png"
            prior = manifest["jobs"].get(key)
            if prior and prior.get("output") != str(target):
                raise SuperResolutionError(f"超分记录与输出位置不一致，未覆盖：{target}")
            if target.exists():
                if not target.is_file() or target.stat().st_size == 0:
                    raise SuperResolutionError(f"已有无效输出文件，未覆盖：{target}")
                try:
                    with Image.open(target) as existing:
                        existing.verify()
                except (OSError, ValueError, UnidentifiedImageError) as exc:
                    raise SuperResolutionError(f"已有损坏输出文件，未覆盖：{target}") from exc
                if not prior:
                    manifest["jobs"][key] = {**signature, "output": str(target)}
                    _save_manifest(record, manifest)
            if target.is_file():
                report["outputs"].append(str(target))
                report["reused"] += 1
                continue
            if model is None:
                if model_error is not None:
                    raise model_error
                from core.model_hub import get_model_hub
                try:
                    model = get_model_hub().get_upscaler()
                except Exception as exc:
                    model_error = SuperResolutionError(f"超分模型不可用：{exc}")
                    raise model_error from exc

            def on_tile(done, total):
                if progress:
                    progress(index, len(source_list), done / total, source.name)

            _upscale_image(source, target, scale, model, cancelled, on_tile)
            manifest["jobs"][key] = {**signature, "output": str(target)}
            _save_manifest(record, manifest)
            report["outputs"].append(str(target))
            report["new"] += 1
        except _Cancelled:
            report["cancelled"] = True
            break
        except Exception as exc:
            report["errors"].append((source.name, str(exc)))
        if progress:
            progress(index + 1, len(source_list), 0, source.name)
    return report
