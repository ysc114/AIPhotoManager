"""Opt-in real-model release smoke test; writes only inside a temporary directory.

Usage: python -m tools.release_acceptance_flow --source <fursuit-photo>
The source is read-only. Models must already exist locally; this does not mock
AI classification, Fursee, FAISS, or super-resolution inference.
"""

import argparse
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from unittest import mock

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("VISUAL_SEARCH_DEVICE", "cpu")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    original_hash = digest(args.source)
    checks = []
    started = time.monotonic()

    def passed(name):
        checks.append(name)
        print("PASS " + name, flush=True)

    import cv2
    import numpy as np
    from PIL import Image, ImageOps
    from core.analysis_cache import AnalysisCache
    from core.identity import IdentityManager
    from core.identity.database import IdentityDatabase
    from core.model_hub import get_model_hub
    from core.video_frames import extract_video_frames, video_source_for_frame
    from core.super_resolution import upscale_images
    from core.visual_search import ClipImageEncoder, VisualSearchIndex
    from core.image_loader import load_images_from_folder

    hub = get_model_hub()
    # This run measures the supported CPU route; GPU/OOM tests are separate.
    hub._device = "cpu"
    try:
        with tempfile.TemporaryDirectory(prefix="aiphotomanager_release_") as tmp:
            root = Path(tmp)
            source = root / "source.avi"
            with Image.open(args.source) as opened:
                frame = np.asarray(ImageOps.pad(opened.convert("RGB"), (640, 480), color=(12, 12, 12)))
            writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"MJPG"), 10, (640, 480))
            if not writer.isOpened():
                raise RuntimeError("MJPG encoder unavailable")
            try:
                for _ in range(5):
                    writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
            finally:
                writer.release()
            video_hash = digest(source)
            manifest = root / "video.json"
            result = extract_video_frames(source, 1, root / "photos", manifest)
            assert result["new"] == 1
            photo = result["frames"][0]
            assert photo in load_images_from_folder(str(root / "photos"))
            assert video_source_for_frame(photo, manifest)
            photo_hash = digest(photo)
            passed("real video extraction, provenance and photo scan")
            duplicate = extract_video_frames(source, 1, root / "photos", manifest)
            assert duplicate["skipped"] and duplicate["new"] == 0
            passed("repeat video extraction reuses output")

            cache = AnalysisCache(root / "analysis.json")
            db_path = root / "identity.sqlite"
            with mock.patch("core.analysis_cache._cache_instance", cache):
                manager = IdentityManager(str(db_path))
                try:
                    result = manager.analyze_paths([photo], cancelled=lambda: False)
                    assert result["failed"] == 0 and result["completed"] == 1, result
                    assert cache.get(photo), "AI result was not cached"
                    groups = manager.get_groups()
                    print("AI route counts", result, flush=True)
                    if manager._fursee_adapter is not None:
                        print("Fursee adapter stats", manager._fursee_adapter.stats, flush=True)
                    manager.db.add_favorite(photo)
                    passed("real AI classification and favorite")
                    if any(g["type"] == "fursuit_character" for g in groups):
                        passed("real sample produced a Fursee identity group")
                    else:
                        print("LIMITATION sample produced no Fursee identity group", flush=True)
                    cached = cache.get(photo)
                    repeated = manager.analyze_paths([photo])
                    assert repeated["failed"] == 0
                    assert cache.get(photo) == cached
                    assert manager.get_groups() == groups
                    passed("repeated analysis reuses cache without changing identity data")
                finally:
                    manager.close()

            reopened = IdentityDatabase(str(db_path))
            try:
                assert reopened.list_favorites() == [photo]
                persisted_ids = {str(group["id"]) for group in reopened.get_all_groups()}
                assert persisted_ids == {str(group["character_id"]) for group in groups}
            finally:
                reopened.close()
            assert AnalysisCache(root / "analysis.json").get(photo) == cache.get(photo)
            passed("identity, favorite and analysis cache survive reopen")
            encoder = ClipImageEncoder(device="cpu")
            try:
                index = VisualSearchIndex(cache_dir=str(root / "search"))
                assert index.add_images([photo], encoder)["new"] == 1
                hits = index.search_by_text("a person wearing a fursuit", encoder, top_k=5)
                assert any(Path(hit["path"]) == Path(photo) for hit in hits)
                reopened_index = VisualSearchIndex(cache_dir=str(root / "search"))
                assert reopened_index.count() == 1
                passed("real semantic search and index reopen")
            finally:
                encoder.close()

            output, upscale_record = root / "upscaled", root / "upscale.json"
            upscaled = upscale_images([photo], 2, output, upscale_record)
            assert upscaled["new"] == 1 and not upscaled["errors"], upscaled
            with Image.open(upscaled["outputs"][0]) as image:
                assert image.size == (1280, 960)
            assert upscale_images([photo], 2, output, upscale_record)["reused"] == 1
            passed("real 2x upscale and repeat reuse")
            assert digest(source) == video_hash
            assert digest(photo) == photo_hash
            passed("test video and extracted original image remain unchanged")
    finally:
        assert digest(args.source) == original_hash, "Original source was modified"
        hub.reset()
    passed("user source remains byte-identical")
    print(json.dumps({"passed": len(checks), "failed": 0, "checks": checks,
                      "seconds": round(time.monotonic() - started, 2)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
