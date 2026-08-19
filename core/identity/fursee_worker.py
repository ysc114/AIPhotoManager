# -*- coding: utf-8 -*-
"""
Fursee Worker（生产版，P-C4-C1/S1 平移）
=========================================
用途：AIPhotoManager 主进程 -> 本 Worker 子进程 -> 512D Fursee embedding。
     运行于独立 conda 环境 fursee_test（Python 3.12 / torch 2.9.1+cu128 / transformers 5.14.1）。

约束（S1 平移自 .scratch_5b2/worker_main.py，P-C2/P-C3 验证版本）：
- 不修改 Fursee 源码；通过 import 复用 utils.fursee_models.FurseeModel
- 处理链路与 P-C1 gpu_infer.py 保持一致（det.to('cuda') + predict 不传 quantize，
  crop 走临时 jpg 与 Fursee buffer pipeline 一致）
- 推理逻辑零改动；仅 FURSEE_ROOT 支持 FURSEE_ROOT 环境变量覆盖（默认值不变）

通信协议（NDJSON，一行一个请求/响应）：
  请求  : {"id": "...", "op": "analyze"|省略, "image_path": "C:/x/y.png"}
          {"id": "...", "op": "health"}
          {"id": "...", "op": "shutdown"}
  响应  : {"id": "...", "ok": true,  "image_path": "...", "detection_count": N,
            "timing_ms": 123.4, "detections": [{"bbox":[x1,y1,x2,y2], "confidence":0.57,
            "embedding":[...512 floats...], "embedding_dim":512, "embedding_norm":1.0}]}
          或 {"id": "...", "ok": false, "error_type": "...", "error": "..."}
  stdout 只输出 JSON 行；所有日志走 stderr。
"""
import contextlib
import io
import json
import logging
import os
import sys
import tempfile
import time
import uuid

FURSEE_ROOT = os.environ.get("FURSEE_ROOT", r"C:\Users\33466\Desktop\fursee")
MODELS = os.path.join(FURSEE_ROOT, "fursee_models")
CUT_PT = os.path.join(MODELS, "cut.pt")
CONF = 0.5
IOU = 0.45
IMGSZ = 1280


def log(msg):
    print(f"[worker] {msg}", file=sys.stderr, flush=True)


def quiet_call(fn, *args, **kwargs):
    """吞掉调用期间一切 stdout 输出（transformers/ultralytics 横幅等），保持协议纯净。"""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


class CropPipelineError(Exception):
    """图片存在但无法读取/解码。"""


class FurseeWorker:
    def __init__(self):
        self.device = None
        self.det = None
        self.model = None
        self.processor = None
        self.tmpdir = tempfile.TemporaryDirectory(prefix="fursee_worker_")
        self.stats = {"requests": 0, "analyzed": 0, "detections": 0, "failures": 0, "total_infer_ms": 0.0}
        self._started = time.time()

    # ---------------- 模型加载（一次） ----------------
    def load(self):
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable in worker env")
        self.device = "cuda:0"
        log(f"GPU: {torch.cuda.get_device_name(0)} | torch {torch.__version__}")

        t0 = time.perf_counter()
        from ultralytics import YOLO
        self.det = quiet_call(YOLO, CUT_PT, verbose=False)
        self.det.to(self.device)
        log(f"cut.pt (YOLO) loaded in {time.perf_counter() - t0:.2f}s")

        t1 = time.perf_counter()
        import torch.nn as nn
        from safetensors.torch import load_file
        from transformers import AutoImageProcessor, AutoModel
        logging.getLogger("transformers").setLevel(logging.ERROR)
        self.processor = quiet_call(AutoImageProcessor.from_pretrained, MODELS)
        backbone = quiet_call(AutoModel.from_pretrained, MODELS, trust_remote_code=True)

        sys.path.insert(0, FURSEE_ROOT)
        from utils.fursee_models import FurseeModel
        self.model = FurseeModel(backbone=backbone, input_dim=1024, embedding_dim=512, dropout=0.1)

        st = load_file(os.path.join(MODELS, "model.safetensors"), device="cpu")
        st_keys = set(st.keys())
        sd_keys = set(self.model.state_dict().keys())
        missing = sorted(sd_keys - st_keys)
        extra = sorted(st_keys - sd_keys)
        if missing or extra:
            raise RuntimeError(
                f"weight key mismatch: missing={len(missing)} extra={len(extra)} "
                f"(st={len(st_keys)} model={len(sd_keys)})"
            )
        self.model.load_state_dict(st, strict=True)
        self.model.to(self.device).eval()
        log(f"DINOv3+projection loaded in {time.perf_counter() - t1:.2f}s ({len(st_keys)} keys strict match)")
        log("READY")

    # ---------------- 单图处理 ----------------
    def _process_image(self, image_path):
        import cv2
        import numpy as np
        import torch
        from PIL import Image

        if not os.path.exists(image_path):
            raise FileNotFoundError(f"image not found: {image_path}")
        if os.path.isdir(image_path):
            raise CropPipelineError(f"path is a directory: {image_path}")

        img_bgr = cv2.imread(image_path)
        if img_bgr is None:
            raise CropPipelineError(f"cannot decode image: {image_path}")
        h, w, _ = img_bgr.shape

        # 1) YOLO 检测（调用方式与 P-C1 gpu_infer.py 一致）
        res = quiet_call(
            self.det.predict,
            source=image_path,
            conf=CONF,
            iou=IOU,
            imgsz=IMGSZ,
            verbose=False,
        )
        boxes = res[0].boxes
        names = res[0].names

        # 2) 逐 box crop + embedding
        detections = []
        for i, box in enumerate(boxes):
            cls_id = int(box.cls[0])
            if names[cls_id] != "furry":
                continue
            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            x1 = max(0, min(x1, w - 1))
            y1 = max(0, min(y1, h - 1))
            x2 = max(0, min(x2, w - 1))
            y2 = max(0, min(y2, h - 1))
            crop = img_bgr[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            conf = float(box.conf[0])

            # 临时 jpg（与 Fursee buffer pipeline 一致：cv2.imwrite -> PIL 打开）
            tmp_path = os.path.join(self.tmpdir.name, f"{uuid.uuid4().hex}.jpg")
            cv2.imwrite(tmp_path, crop)
            try:
                pil_img = Image.open(tmp_path).convert("RGB")
                inputs = self.processor(images=[pil_img], return_tensors="pt")
                pixel = inputs["pixel_values"].to(self.device)
                with torch.inference_mode():
                    vec = self.model(pixel)[0].cpu().numpy()
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

            detections.append({
                "bbox": [x1, y1, x2, y2],
                "confidence": conf,
                "embedding": vec.tolist(),
                "embedding_dim": int(vec.shape[0]),
                "embedding_norm": float(np.linalg.norm(vec)),
            })
        return detections

    # ---------------- 协议主循环 ----------------
    def _respond(self, obj):
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def _error_type(self, exc):
        if isinstance(exc, FileNotFoundError):
            return "file_not_found"
        if isinstance(exc, CropPipelineError):
            return "unreadable_image"
        if exc.__class__.__name__ == "OutOfMemoryError" or "out of memory" in str(exc).lower():
            return "cuda_oom"
        return "inference_error"

    def run(self):
        import torch
        for raw in sys.stdin.buffer:
            if not raw or not raw.strip():
                continue
            text = raw.decode("utf-8", "replace").strip()
            rid = None
            try:
                req = json.loads(text)
            except json.JSONDecodeError as e:
                self._respond({"id": None, "ok": False, "error_type": "bad_request", "error": f"invalid JSON: {e}"})
                continue

            rid = req.get("id")
            op = req.get("op", "analyze")

            if op == "shutdown":
                self._respond({"id": rid, "ok": True, "op": "shutdown"})
                log("shutdown requested, exiting")
                return

            if op == "health":
                self._respond({
                    "id": rid, "ok": True, "op": "health",
                    "cuda_available": torch.cuda.is_available(),
                    "gpu": torch.cuda.get_device_name(0),
                    "models_loaded": self.det is not None and self.model is not None,
                    "python": sys.version.split()[0],
                    "executable": sys.executable,
                    "uptime_s": round(time.time() - self._started, 2),
                    "stats": self.stats,
                })
                continue

            if op != "analyze":
                self._respond({"id": rid, "ok": False, "error_type": "bad_request", "error": f"unknown op: {op}"})
                continue

            image_path = req.get("image_path")
            if not image_path or not isinstance(image_path, str):
                self._respond({"id": rid, "ok": False, "error_type": "bad_request", "error": "missing/invalid image_path"})
                continue

            t0 = time.perf_counter()
            try:
                detections = self._process_image(image_path)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                self.stats["requests"] += 1
                self.stats["analyzed"] += 1
                self.stats["detections"] += len(detections)
                self.stats["total_infer_ms"] += elapsed_ms
                self._respond({
                    "id": rid, "ok": True,
                    "image_path": image_path,
                    "detection_count": len(detections),
                    "timing_ms": round(elapsed_ms, 2),
                    "detections": detections,
                })
            except Exception as e:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                self.stats["requests"] += 1
                self.stats["failures"] += 1
                log(f"request {rid} failed: {type(e).__name__}: {e}")
                self._respond({
                    "id": rid, "ok": False,
                    "error_type": self._error_type(e),
                    "error": str(e),
                    "timing_ms": round(elapsed_ms, 2),
                })

        log("stdin EOF, exiting")


def main():
    try:
        worker = FurseeWorker()
        worker.load()
    except Exception as e:
        import traceback
        log(f"FATAL: worker failed to start: {type(e).__name__}: {e}")
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
    try:
        worker.run()
    except KeyboardInterrupt:
        log("interrupted, exiting")
    finally:
        worker.tmpdir.cleanup()
        sys.exit(0)


if __name__ == "__main__":
    main()
