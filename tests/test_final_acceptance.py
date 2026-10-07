"""Isolated release checks; every generated file stays in pytest's tmp_path.

Real paths: image decoding/thumbnails, SQLite/cache reopen, OpenCV 1080p decoding,
and production video QThread. Faults are injected at I/O boundaries; the disk is
never filled. Super-resolution recovery uses a tiny torch interpolation model,
so these tests do not claim AI inference quality or GPU coverage.
"""

import builtins
import errno
import hashlib
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.analysis_cache import AnalysisCache
from core.identity.database import IdentityDatabase
from core.image_loader import load_images_from_folder
from core.super_resolution import upscale_images
from core.thumbnail_cache import ThumbnailCache
from core.video_frames import VideoFrameError, extract_video_frames
from ui.media_tasks import _VideoFramesWorker


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate(), "background operation did not finish before timeout"


def make_video(path, size=(64, 48), count=12):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, size)
    if not writer.isOpened():
        pytest.skip("OpenCV MJPG encoder is unavailable")
    try:
        frame = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        for index in range(count):
            frame[:] = (index % 255, 50, 100)
            writer.write(frame)
    finally:
        writer.release()
    return path


@pytest.mark.parametrize("suffix,size", [
    (".jpg", (320, 240)), (".jpeg", (180, 300)),
    (".png", (240, 180)), (".webp", (180, 240)),
    (".png", (1, 1)), (".jpg", (4000, 3000)),
])
def test_formats_scan_preview_thumbnail_preserve_source(tmp_path, qt_app, suffix, size):
    photos = tmp_path / "照片"
    photos.mkdir()
    source = photos / ("猫咪 [试验] #1 + &" + suffix)
    with Image.new("RGB", size, (80, 120, 160)) as image:
        image.save(source)
    before = digest(source)
    first = load_images_from_folder(str(photos))
    assert first == [str(source)]
    assert load_images_from_folder(str(photos)) == first
    preview = QImage(str(source))
    assert not preview.isNull()
    assert (preview.width(), preview.height()) == size
    cache = ThumbnailCache(cache_dir=tmp_path / "thumbnails")
    ready = []
    try:
        cache.request(str(source), 256, on_ready=ready.append)
        wait_until(lambda: bool(ready))
        assert ready[0] and Path(ready[0]).is_file()
        thumbnail = QImage(ready[0])
        assert not thumbnail.isNull()
        assert max(thumbnail.width(), thumbnail.height()) <= 256
        assert cache.get_cached(str(source), 256) == ready[0]
    finally:
        cache.shutdown()
    assert digest(source) == before


def test_corrupt_photo_thumbnail_does_not_stop_next_photo(tmp_path, qt_app):
    broken = tmp_path / "损坏.jpg"
    broken.write_bytes(b"not an image")
    good = tmp_path / "正常.webp"
    with Image.new("RGB", (64, 48), "blue") as image:
        image.save(good)
    before = {path: digest(path) for path in (broken, good)}
    assert set(load_images_from_folder(str(tmp_path))) == {str(broken), str(good)}
    assert QImage(str(broken)).isNull()
    results = {}
    cache = ThumbnailCache(cache_dir=tmp_path / "thumbs")
    try:
        for source in (broken, good):
            cache.request(str(source), 256,
                          on_ready=lambda value, key=source: results.update({key: value}))
        wait_until(lambda: len(results) == 2)
        assert results[broken] is None
        assert results[good] and Path(results[good]).is_file()
    finally:
        cache.shutdown()
    assert {path: digest(path) for path in before} == before


def test_favorites_roles_and_analysis_cache_survive_reopen(tmp_path):
    """Real persistence; stored analysis/embedding values are fixture data."""
    source = tmp_path / "角色合照.png"
    with Image.new("RGB", (16, 16), "green") as image:
        image.save(source)
    before = digest(source)
    db_path, cache_path = tmp_path / "identity.sqlite", tmp_path / "analysis.json"
    path = source.as_posix()
    db = IdentityDatabase(str(db_path))
    ids = []
    try:
        for detection, name in enumerate(("角色甲", "角色乙")):
            group = db.create_group(name=name, group_type="fursuit_character")
            ids.append(group)
            db.add_image(group_id=group, image_path=path,
                         embedding=np.full(8, detection + 1, dtype=np.float32),
                         embedding_type="fursuit_fursee", detection_index=detection,
                         bbox=[0, 0, 8, 8], confidence=0.9)
        db.add_favorite(path)
        db.add_favorite(path)
    finally:
        db.close()
    analysis = {"category": "fursuit", "quality": 0.9, "scores": {"fursuit": 0.9},
                "layer1": {"category": "fursuit", "label_cn": "兽装人物"},
                "layer2": None, "layer3": None}
    cache = AnalysisCache(str(cache_path))
    cache.set(path, analysis)
    cache.set_category_cn(path, "人工分类")
    saved = cache.get(path).copy()
    for _ in range(2):
        reopened = IdentityDatabase(str(db_path))
        try:
            assert reopened.list_favorites() == [path]
            assert [reopened.get_group(group)["name"] for group in ids] == ["角色甲", "角色乙"]
            for detection, group in enumerate(ids):
                rows = reopened.get_images_by_group(group)
                assert len(rows) == 1
                assert rows[0]["image_path"] == path
                assert rows[0]["detection_index"] == detection
                np.testing.assert_array_equal(
                    np.frombuffer(rows[0]["embedding"], dtype=np.float32),
                    np.full(8, detection + 1, dtype=np.float32))
        finally:
            reopened.close()
        assert AnalysisCache(str(cache_path)).get(path) == saved
    assert digest(source) == before


@pytest.mark.parametrize("fault", [OSError(errno.ENOSPC, "disk full"),
                                   PermissionError(errno.EACCES, "access denied")])
def test_video_write_error_can_retry_without_source_damage(tmp_path, fault):
    source = make_video(tmp_path / "source.avi")
    before = digest(source)
    output, manifest = tmp_path / "frames", tmp_path / "video.json"
    real_open = builtins.open

    def failing_open(path, *args, **kwargs):
        if str(path).endswith(".jpg.tmp"):
            raise fault
        return real_open(path, *args, **kwargs)

    with mock.patch("core.video_frames.open", side_effect=failing_open, create=True):
        with pytest.raises(VideoFrameError, match="磁盘空间和权限"):
            extract_video_frames(source, 1, output, manifest)
    assert not list(output.iterdir())
    retried = extract_video_frames(source, 1, output, manifest)
    assert retried["new"] == 2
    assert digest(source) == before


def test_upscale_disk_space_preflight_recovers(tmp_path):
    import torch

    source = tmp_path / "source.png"
    with Image.new("RGB", (16, 12), "red") as image:
        image.save(source)
    before = digest(source)
    output, manifest = tmp_path / "upscaled", tmp_path / "upscale.json"
    model = torch.nn.Upsample(scale_factor=3, mode="nearest")
    with mock.patch("core.super_resolution.shutil.disk_usage",
                    return_value=SimpleNamespace(free=0)):
        failed = upscale_images([source], 2, output, manifest, model=model)
    assert failed["new"] == 0
    assert len(failed["errors"]) == 1 and "磁盘空间不足" in failed["errors"][0][1]
    assert not list(output.iterdir())
    retried = upscale_images([source], 2, output, manifest, model=model)
    assert retried["new"] == 1
    assert digest(source) == before


@pytest.mark.parametrize("fault", [OSError(errno.ENOSPC, "disk full"),
                                   PermissionError(errno.EACCES, "access denied")])
def test_upscale_save_error_can_retry(tmp_path, fault):
    import torch

    source = tmp_path / "source.png"
    with Image.new("RGB", (16, 12), "blue") as image:
        image.save(source)
    before = digest(source)
    output, manifest = tmp_path / "upscaled", tmp_path / "upscale.json"
    model = torch.nn.Upsample(scale_factor=3, mode="nearest")
    with mock.patch.object(Image.Image, "save", side_effect=fault):
        failed = upscale_images([source], 3, output, manifest, model=model)
    assert len(failed["errors"]) == 1 and "磁盘空间或权限" in failed["errors"][0][1]
    assert not list(output.iterdir())
    assert upscale_images([source], 3, output, manifest, model=model)["new"] == 1
    assert digest(source) == before


def test_real_1080p_video_worker_keeps_qt_responsive_and_cancels(tmp_path, qt_app):
    """Real codec/worker, with only destination paths redirected to tempdir."""
    source = make_video(tmp_path / "1080p.avi", size=(1920, 1080), count=120)
    before = digest(source)
    worker = _VideoFramesWorker([source], 1)
    results, failures, ticks = [], [], []
    worker.done.connect(results.append)
    worker.failed.connect(failures.append)
    worker.progress_updated.connect(lambda *_: worker.requestInterruption(), Qt.DirectConnection)
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(worker.isRunning()))
    with mock.patch("ui.media_tasks._PHOTOS", tmp_path / "frames"), \
            mock.patch("ui.media_tasks._VIDEO_MANIFEST", tmp_path / "video.json"):
        timer.start()
        try:
            worker.start()
            wait_until(lambda: bool(results or failures), timeout=20)
        finally:
            worker.requestInterruption()
            worker.wait()
            timer.stop()
            qt_app.processEvents()
    assert not worker.isRunning()
    assert not failures
    assert results[0]["cancelled"]
    assert results[0]["frames"]
    assert any(ticks), "Qt event timer must fire while real video decoding is running"
    with Image.open(results[0]["frames"][0]) as image:
        assert image.size == (1920, 1080)
    assert digest(source) == before
