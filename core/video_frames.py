"""Non-destructive, resumable video frame extraction for the photo library."""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path


VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv"}


class VideoFrameError(RuntimeError):
    pass


def _read_manifest(path):
    if not path.is_file():
        return {"version": 1, "jobs": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VideoFrameError(f"无法读取视频抽帧记录 {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
        raise VideoFrameError(f"视频抽帧记录格式无效: {path}")
    return data


def _save_manifest(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        raise VideoFrameError(f"无法保存视频抽帧记录 {path}: {exc}") from exc
    finally:
        if tmp.exists():
            tmp.unlink()


def _write_jpeg(path, frame, cv2):
    tmp = path.with_name(path.name + ".tmp")
    try:
        ok, encoded = cv2.imencode(".jpg", frame,
                                   [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            raise VideoFrameError(f"无法编码视频帧: {path.name}")
        with open(tmp, "wb") as stream:
            stream.write(encoded.tobytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        raise VideoFrameError(f"写入视频帧失败（请检查磁盘空间和权限）: {exc}") from exc
    finally:
        if tmp.exists():
            tmp.unlink()


def extract_video_frames(source, interval_seconds, output_dir, manifest_path,
                         progress=None, cancelled=None):
    """Extract timed JPEGs into an existing photo directory.

    ``progress(done_frames, total_frames, saved_frames)`` and ``cancelled()``
    are optional worker callbacks. A cancelled run records its partial outputs
    and can resume without overwriting them. The source file is only read.
    """
    try:
        import cv2
    except ImportError as exc:
        raise VideoFrameError("视频抽帧需要 OpenCV (cv2)") from exc

    video = Path(source).resolve()
    output = Path(output_dir).resolve()
    manifest_file = Path(manifest_path).resolve()
    if video.suffix.lower() not in VIDEO_EXTENSIONS:
        raise VideoFrameError(f"不支持的视频格式: {video.suffix or video.name}")
    if not video.is_file():
        raise VideoFrameError(f"视频不存在: {video}")
    interval = float(interval_seconds)
    if not 0.1 <= interval <= 3600:
        raise VideoFrameError("抽帧间隔必须在 0.1 到 3600 秒之间")
    try:
        output.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise VideoFrameError(f"无法创建抽帧输出目录 {output}: {exc}") from exc

    stat = video.stat()
    signature = {"source": str(video), "size": stat.st_size,
                 "mtime_ns": stat.st_mtime_ns, "interval": interval}
    key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:20]
    manifest = _read_manifest(manifest_file)
    prior = manifest["jobs"].get(key) or {}
    prior_paths = [str(p) for p in prior.get("frames", [])]
    if prior.get("complete") and prior_paths and all(
            Path(p).is_file() and Path(p).stat().st_size > 0 for p in prior_paths):
        return {"frames": prior_paths, "new": 0, "reused": len(prior_paths),
                "skipped": True, "cancelled": False, "output_dir": str(output)}

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        capture.release()
        raise VideoFrameError(f"无法打开视频（格式或编码可能不受支持）: {video}")
    fps = capture.get(cv2.CAP_PROP_FPS)
    total = max(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
    if not 0 < fps < 1000:
        capture.release()
        raise VideoFrameError(f"无法读取视频帧率: {video}")

    frames = []
    new_count = reused = frame_index = 0
    next_time = 0.0
    stopped = False
    try:
        while True:
            if cancelled and cancelled():
                stopped = True
                break
            ok, frame = capture.read()
            if not ok:
                break
            second = frame_index / fps
            if second + 0.5 / fps >= next_time:
                millis = round(second * 1000)
                name = f"video_{key}_{millis:012d}.jpg"
                dest = output / name
                if dest.exists():
                    if dest.stat().st_size == 0:
                        raise VideoFrameError(f"已有空文件阻止抽帧: {dest}")
                    reused += 1
                else:
                    _write_jpeg(dest, frame, cv2)
                    new_count += 1
                frames.append(str(dest))
                next_time += interval
            frame_index += 1
            if progress and (frame_index % 30 == 0 or frame_index == total):
                progress(frame_index, total, len(frames))
    finally:
        capture.release()

    if not frames and not stopped:
        raise VideoFrameError(f"视频没有可读取的帧（文件可能已损坏）: {video}")
    manifest["jobs"][key] = {
        **signature, "frames": frames, "times_ms": [
            int(Path(p).stem.rsplit("_", 1)[1]) for p in frames],
        "complete": not stopped, "output_dir": str(output),
    }
    _save_manifest(manifest_file, manifest)
    if progress:
        progress(frame_index, total, len(frames))
    return {"frames": frames, "new": new_count, "reused": reused,
            "skipped": False, "cancelled": stopped, "output_dir": str(output)}


def video_source_for_frame(frame_path, manifest_path):
    """Return persisted source video and timestamp for an extracted frame."""
    frame = Path(frame_path).resolve()
    parts = frame.stem.split("_")
    if len(parts) != 3 or parts[0] != "video" or len(parts[1]) != 20:
        return None
    try:
        millis = int(parts[2])
    except ValueError:
        return None
    manifest = Path(manifest_path).resolve()
    if not manifest.is_file():
        return None
    stat = manifest.stat()
    job = _cached_manifest(str(manifest), stat.st_mtime_ns, stat.st_size)[
        "jobs"].get(parts[1])
    if job and frame.parent == Path(job["output_dir"]).resolve():
        return {"source": job["source"], "time_ms": millis}
    return None


@lru_cache(maxsize=2)
def _cached_manifest(path, mtime_ns, size):
    return _read_manifest(Path(path))
