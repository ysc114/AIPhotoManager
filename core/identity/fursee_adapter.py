# -*- coding: utf-8 -*-
"""
FurseeAdapter —— Fursee persistent Worker 的进程内封装（生产版，P-C4-C1/S1 平移）
=================================================================================
位置：core/identity/fursee_adapter.py（自 .scratch_5b2/fursee_adapter.py 平移，
     P-C3 28/28 检查点验证版本；API 与行为保持一致）。

职责：
  把 P-C2/P-C3 验证过的 NDJSON persistent worker 封装为稳定 Python API：
    start() / wait_ready() / health_check()
    analyze(image_path) / analyze_batch(image_paths)
    restart() / shutdown()

设计要点（均来自 P-C1/P-C2/P-C3 实测结论）：
  - persistent worker（oneshot 每图 14.3s vs persistent 409ms/图，35 倍差距）
  - 启动/请求/关闭三类超时，永不无限等待
  - worker 崩溃自动检测；auto_restart 仅在"单次请求内"重试一次，绝不无限重启
  - 响应按 id 对齐：迟到/错位响应自动丢弃（防 timeout 后流错位）
  - python_exe 走配置项（默认 expanduser 解析，不写死用户名）
  - B2：worker 使用独立 YOLO_CONFIG_DIR（启动前预初始化，仅注入
    子进程 env，主进程环境零改动——根治 ultralytics settings
    reset 警告污染 stdout 协议通道导致的 FurseeProtocolError 熔断）
  - Adapter 日志走自身 logger；不污染 worker stdout（协议通道只过 JSON）

本模块与 Fursee 源码零耦合：所有推理在子进程 fursee_worker.py
（运行于独立 conda 环境 fursee_test，Python 3.12 / torch cu128）内完成，
主进程（AIPhotoManager，Python 3.10）只经 stdin/stdout JSON 通信。

异常层级（对齐 core/storage/exceptions.py 的 Base+子类风格）：
  FurseeError
  ├── FurseeStartupError        启动/模型加载失败或超时
  ├── FurseeTimeoutError        请求超时（worker 仍存活）
  ├── FurseeWorkerCrashedError  worker 进程死亡
  ├── FurseeInvalidRequestError 调用方输入非法（路径不存在/非图片由 worker 判定）
  ├── FurseeInferenceError      推理失败（含 cuda_oom、图片损坏）
  └── FurseeProtocolError       协议层异常（非 JSON、结构非法、维度/norm 校验失败）
"""
from __future__ import annotations

import json
import logging
import os
import queue
import struct
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("fursee.adapter")


# ============================================================
# 异常层级
# ============================================================
class FurseeError(Exception):
    """Base exception for Fursee adapter operations."""


class FurseeStartupError(FurseeError):
    """Worker 启动失败 / 模型加载失败 / 启动超时。"""


class FurseeTimeoutError(FurseeError):
    """请求在 timeout 内未获得响应（worker 仍存活）。"""


class FurseeWorkerCrashedError(FurseeError):
    """Worker 进程已死亡（pipe 断 / stdout EOF / 异常退出码）。"""


class FurseeInvalidRequestError(FurseeError):
    """调用方请求非法（bad_request / file_not_found）。"""


class FurseeInferenceError(FurseeError):
    """推理失败（unreadable_image / cuda_oom / inference_error）。"""


class FurseeProtocolError(FurseeError):
    """协议异常（非 JSON 响应、id 错位不可恢复、结果结构校验失败）。"""


#: worker error_type -> Adapter 异常类
_ERROR_TYPE_MAP = {
    "bad_request": FurseeInvalidRequestError,
    "file_not_found": FurseeInvalidRequestError,
    "unreadable_image": FurseeInferenceError,
    "cuda_oom": FurseeInferenceError,
    "inference_error": FurseeInferenceError,
}


# ============================================================
# 配置
# ============================================================
def _default_worker_path() -> str:
    # S1 平移适配：worker 与本模块同目录（core/identity/fursee_worker.py）
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "fursee_worker.py")


def _default_python_exe() -> str:
    # 不写死用户名：用 expanduser 动态解析 fursee_test 环境解释器
    return os.path.join(os.path.expanduser("~"), ".conda", "envs", "fursee_test", "python.exe")


def _default_yolo_config_dir() -> str:
    # B2：worker 专用 Ultralytics 配置目录（与主进程及其他 conda 环境
    # 的默认 %AppData%/Roaming/Ultralytics 完全隔离，杜绝 settings 乒乓 reset）
    return os.path.join(os.path.expanduser("~"), ".aipm", "fursee_yolo_cfg")


@dataclass
class FurseeAdapterConfig:
    python_exe: str = field(default_factory=_default_python_exe)
    worker_path: str = field(default_factory=_default_worker_path)
    startup_timeout: float = 240.0    # P-C2 实测 boot+load ≈ 13.1s，留足余量
    request_timeout: float = 120.0    # P-C2 实测单图 <1s；大批图留余量
    shutdown_timeout: float = 30.0
    warmup: bool = True               # READY 后用一张合成小图预热（吸收首图 ~500ms 热身）
    auto_restart: bool = True         # 请求中发现 worker 死亡时：重启一次并重试一次
    yolo_config_dir: str = field(default_factory=_default_yolo_config_dir)  # B2 worker 隔离配置目录
    preinit_timeout: float = 90.0     # B2 预初始化 import ultralytics（含 torch 冷启动）超时


# ============================================================
# 合成预热图（纯 Python 生成 16x16 黑色 BMP，避免主进程依赖 PIL/cv2）
# ============================================================
def _write_tiny_bmp(path: str) -> None:
    w = h = 16
    row_size = w * 3  # 48 字节，已是 4 的倍数
    pixel_data = b"\x00" * (row_size * h)
    file_size = 54 + len(pixel_data)
    bmp_header = b"BM" + struct.pack("<IHHI", file_size, 0, 0, 54)
    dib_header = struct.pack(
        "<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(pixel_data), 2835, 2835, 0, 0
    )
    with open(path, "wb") as f:
        f.write(bmp_header + dib_header + pixel_data)


# ============================================================
# FurseeAdapter
# ============================================================
class FurseeAdapter:
    """P-C2 persistent Fursee Worker 的进程内封装（线程安全：请求串行）。"""

    def __init__(self, config: FurseeAdapterConfig | None = None):
        self.cfg = config or FurseeAdapterConfig()
        self._proc: subprocess.Popen | None = None
        self._ready = threading.Event()
        self._resp_q: "queue.Queue[bytes | None]" = queue.Queue()
        self._stderr_tail: list[str] = []          # 当前 worker 世代 stderr（诊断用）
        self._lock = threading.RLock()             # 串行化请求/生命周期操作
        self._state = "created"                    # created/starting/ready/stopped
        self._req_counter = 0
        self.last_boot_s: float | None = None
        self.stats = {
            "starts": 0, "restarts": 0, "requests": 0, "responses": 0,
            "timeouts": 0, "crashes_detected": 0, "protocol_errors": 0,
            "discarded_responses": 0, "auto_restart_retries": 0,
        }

    # ---------------- 生命周期 ----------------
    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def state(self) -> str:
        return self._state

    def start(self) -> "FurseeAdapter":
        """启动 worker 并等待 READY（含模型加载）。幂等：已 ready 则直接返回。"""
        with self._lock:
            if self._state == "ready" and self._proc and self._proc.poll() is None:
                return self
            self._spawn()
            return self

    def wait_ready(self, timeout: float | None = None) -> float:
        """等待 worker READY，返回等待秒数。已 ready 时立即返回 0。"""
        if self._state == "ready" and self._proc and self._proc.poll() is None:
            return 0.0
        timeout = timeout if timeout is not None else self.cfg.startup_timeout
        t0 = time.time()
        if not self._ready.wait(timeout=timeout):
            rc = self._proc.poll() if self._proc else None
            raise (FurseeWorkerCrashedError if rc is not None else FurseeStartupError)(
                f"worker not ready in {timeout}s (rc={rc}), stderr tail: {self._stderr_tail[-6:]}"
            )
        return time.time() - t0

    def health_check(self) -> dict:
        """探测 worker。存活返回 {alive: True, ...worker health}；死亡返回 {alive: False,...}（不抛错）。"""
        if self._proc is None or self._proc.poll() is not None:
            return {"alive": False, "returncode": (self._proc.poll() if self._proc else None)}
        try:
            resp = self._request({"op": "health"}, timeout=min(self.cfg.request_timeout, 60.0))
        except FurseeError as e:
            return {"alive": False, "error": f"{type(e).__name__}: {e}"}
        resp.pop("id", None)
        return {"alive": True, **resp}

    def restart(self) -> "FurseeAdapter":
        """强制重启 worker（kill 旧进程 -> 重新加载模型 -> READY）。"""
        with self._lock:
            log.info("RESTART")
            self.stats["restarts"] += 1
            self._kill_proc()
            self._spawn()
            return self

    def shutdown(self, timeout: float | None = None) -> int | None:
        """优雅关闭 worker（shutdown 协议 -> 等待退出 -> 超时 kill）。返回退出码。"""
        timeout = timeout if timeout is not None else self.cfg.shutdown_timeout
        with self._lock:
            if self._proc is None:
                self._state = "stopped"
                return None
            rc = self._proc.poll()
            if rc is None:
                try:
                    self._request({"op": "shutdown"}, timeout=timeout)
                except FurseeError as e:
                    log.warning("SHUTDOWN: shutdown request failed: %s", e)
                try:
                    rc = self._proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    log.warning("SHUTDOWN: graceful exit timeout, killing")
                    self._kill_proc()
                    rc = self._proc.poll()
            self._state = "stopped"
            log.info("SHUTDOWN rc=%s", rc)
            return rc

    # ---------------- 推理 ----------------
    def analyze(self, image_path: str, timeout: float | None = None) -> dict:
        """单图分析。返回 worker 响应 dict（已校验）：
        {"image_path", "detection_count", "timing_ms",
         "detections": [{"bbox":[x1,y1,x2,y2], "confidence": float,
                         "embedding": [512 floats], "embedding_dim": 512,
                         "embedding_norm": ~1.0}]}
        一图可含多个 detection（多角色）。worker 崩溃时自动重启并重试一次。
        """
        if not isinstance(image_path, str) or not image_path:
            raise FurseeInvalidRequestError(f"image_path must be a non-empty str, got: {image_path!r}")
        timeout = timeout if timeout is not None else self.cfg.request_timeout
        payload = {"op": "analyze", "image_path": image_path}
        try:
            resp = self._request(payload, timeout=timeout)
        except FurseeWorkerCrashedError:
            if not self.cfg.auto_restart:
                raise
            # 单次自动重启 + 单次重试，绝不无限循环
            self.stats["auto_restart_retries"] += 1
            log.warning("CRASH during analyze -> restart + retry once (path=%s)", image_path)
            self.restart()
            resp = self._request(payload, timeout=timeout)
        return self._validate_result(resp, image_path)

    def analyze_batch(self, image_paths, timeout: float | None = None) -> list[dict]:
        """批量分析：同一 persistent worker 上串行请求，不逐图建进程。
        返回按输入顺序的结果列表：
          [{"ok": True,  "image_path": ..., "result": {...analyze 返回...}},
           {"ok": False, "image_path": ..., "error_type": ..., "error": ...}]
        图级错误（路径不存在/损坏图）记入对应条目，不中断整批；
        worker 级错误（崩溃后重启仍失败/超时/协议错误）向上抛出。
        """
        results = []
        for p in image_paths:
            try:
                r = self.analyze(p, timeout=timeout)
                results.append({"ok": True, "image_path": p, "result": r})
            except (FurseeInvalidRequestError, FurseeInferenceError) as e:
                results.append({
                    "ok": False, "image_path": p,
                    "error_type": getattr(e, "error_type", type(e).__name__),
                    "error": str(e),
                })
        return results

    # ---------------- 内部：进程与 IO ----------------
    def _spawn(self) -> None:
        if not os.path.isfile(self.cfg.python_exe):
            raise FurseeStartupError(f"python_exe not found: {self.cfg.python_exe}")
        if not os.path.isfile(self.cfg.worker_path):
            raise FurseeStartupError(f"worker_path not found: {self.cfg.worker_path}")

        # B2：确保 worker 独立配置目录已预初始化（settings.json 存在），
        # 避免 worker 首次 import ultralytics 时向 stdout 打印
        # "Creating new Ultralytics Settings" 污染 NDJSON 协议通道
        self._ensure_yolo_cfg()

        # 清理可能残留的旧进程，重建本世代的通信资源
        if self._proc is not None and self._proc.poll() is None:
            self._kill_proc()
        self._ready.clear()
        self._resp_q = queue.Queue()
        self._stderr_tail = []

        # B2：仅向子进程注入 YOLO_CONFIG_DIR（os.environ 副本，主进程环境不变）
        worker_env = dict(os.environ)
        worker_env["YOLO_CONFIG_DIR"] = self.cfg.yolo_config_dir

        try:
            self._proc = subprocess.Popen(
                [self.cfg.python_exe, self.cfg.worker_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=worker_env,
            )
        except OSError as e:
            self._state = "stopped"
            raise FurseeStartupError(f"failed to spawn worker: {e}") from e

        self.stats["starts"] += 1
        self._state = "starting"
        log.info("START pid=%s python=%s", self._proc.pid, self.cfg.python_exe)

        # 本世代绑定（旧世代线程写旧队列/旧列表，互不串扰）
        proc, resp_q, stderr_tail = self._proc, self._resp_q, self._stderr_tail
        threading.Thread(target=self._stdout_reader, args=(proc, resp_q), daemon=True).start()
        threading.Thread(target=self._stderr_drain, args=(proc, stderr_tail, self._ready), daemon=True).start()

        try:
            self._wait_ready(self.cfg.startup_timeout)
        except FurseeError:
            # 启动失败必须清理半启动进程，避免僵尸/显存残留
            self._kill_proc()
            self._state = "stopped"
            raise

        if self.cfg.warmup:
            self._warmup()

    def _ensure_yolo_cfg(self) -> None:
        """B2：预初始化 worker 独立 YOLO 配置目录。

        settings.json 已存在则直接返回（零开销）；缺失时用 worker 同一
        解释器跑一次 `import ultralytics`（stdout/stderr 丢弃），让其在
        该目录生成 settings.json。此后 worker 启动 import 时为静默加载，
        不再打印 "Creating new Ultralytics Settings" 到 stdout 协议通道。
        失败时抛 FurseeStartupError（含 stderr 摘要）。
        """
        settings_path = os.path.join(self.cfg.yolo_config_dir, "Ultralytics", "settings.json")
        if os.path.isfile(settings_path):
            return
        try:
            os.makedirs(self.cfg.yolo_config_dir, exist_ok=True)
        except OSError as e:
            raise FurseeStartupError(
                f"cannot create yolo_config_dir: {self.cfg.yolo_config_dir} ({e})") from e
        env = dict(os.environ)
        env["YOLO_CONFIG_DIR"] = self.cfg.yolo_config_dir
        try:
            r = subprocess.run(
                [self.cfg.python_exe, "-c", "import ultralytics"],
                capture_output=True, timeout=self.cfg.preinit_timeout, env=env,
            )
        except subprocess.TimeoutExpired as e:
            raise FurseeStartupError(
                f"ultralytics pre-init timeout ({self.cfg.preinit_timeout}s)") from e
        except OSError as e:
            raise FurseeStartupError(f"pre-init spawn failed: {e}") from e
        if not os.path.isfile(settings_path):
            tail = r.stderr.decode("utf-8", "replace")[-300:] if r.stderr else ""
            raise FurseeStartupError(
                f"pre-init finished but settings.json not created at {settings_path}; "
                f"rc={r.returncode}, stderr tail: {tail}")

    def _stdout_reader(self, proc, resp_q) -> None:
        """读 worker stdout（纯 JSON 行）；EOF 放入 None 哨兵。"""
        try:
            for line in iter(proc.stdout.readline, b""):
                resp_q.put(line)
        except OSError:
            pass
        finally:
            resp_q.put(None)

    def _stderr_drain(self, proc, lines, ready_event) -> None:
        for line in iter(proc.stderr.readline, b""):
            text = line.decode("utf-8", "replace").rstrip()
            lines.append(text)
            if "READY" in text:
                ready_event.set()

    def _wait_ready(self, timeout: float) -> None:
        t0 = time.time()
        while True:
            remaining = timeout - (time.time() - t0)
            if remaining <= 0:
                rc = self._proc.poll()
                if rc is not None:
                    raise FurseeWorkerCrashedError(
                        f"worker exited rc={rc} during startup, stderr tail: {self._stderr_tail[-8:]}")
                raise FurseeStartupError(
                    f"worker not READY within {timeout}s, stderr tail: {self._stderr_tail[-8:]}")
            if self._ready.wait(timeout=min(remaining, 0.5)):
                self.last_boot_s = time.time() - t0
                self._state = "ready"
                log.info("READY in %.1fs (pid=%s)", self.last_boot_s, self._proc.pid)
                return
            if self._proc.poll() is not None:
                raise FurseeWorkerCrashedError(
                    f"worker exited rc={self._proc.poll()} during startup, "
                    f"stderr tail: {self._stderr_tail[-8:]}")

    def _warmup(self) -> None:
        """READY 后用一张合成 BMP 预热（吸收首图 ~500ms CUDA 热身）。失败不影响可用性。"""
        tmp = tempfile.NamedTemporaryFile(prefix="fursee_warmup_", suffix=".bmp", delete=False)
        tmp.close()
        try:
            _write_tiny_bmp(tmp.name)
            t0 = time.perf_counter()
            self._request({"op": "analyze", "image_path": tmp.name},
                          timeout=min(self.cfg.request_timeout, 60.0), _lifecycle="WARMUP")
            log.info("WARMUP done in %.0fms", (time.perf_counter() - t0) * 1000)
        except FurseeError as e:
            log.warning("WARMUP skipped: %s", e)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    def _kill_proc(self) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.kill()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        for stream in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass

    # ---------------- 内部：协议 ----------------
    def _request(self, payload: dict, timeout: float, _lifecycle: str = "REQUEST") -> dict:
        """发送一个请求并等待 id 匹配的响应。迟到/错位响应丢弃；EOF/断管=崩溃；到期=超时。"""
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self.stats["crashes_detected"] += 1
                raise FurseeWorkerCrashedError(
                    f"worker not running (rc={self._proc.poll() if self._proc else None})")

            self._req_counter += 1
            rid = f"req-{self._req_counter}"
            payload = dict(payload, id=rid)
            line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            log.debug("%s id=%s op=%s", _lifecycle, rid, payload.get("op", "analyze"))
            try:
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                self.stats["crashes_detected"] += 1
                raise FurseeWorkerCrashedError(
                    f"worker pipe broken during request: {e}, "
                    f"stderr tail: {self._stderr_tail[-6:]}") from e

            self.stats["requests"] += 1
            deadline = time.time() + timeout
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    if self._proc.poll() is not None:
                        self.stats["crashes_detected"] += 1
                        raise FurseeWorkerCrashedError(
                            f"worker died rc={self._proc.poll()} (no response for {rid})")
                    self.stats["timeouts"] += 1
                    log.warning("TIMEOUT id=%s after %.1fs (worker alive, pid=%s)",
                                rid, timeout, self._proc.pid)
                    raise FurseeTimeoutError(f"no response for {rid} within {timeout}s (worker alive)")
                try:
                    item = self._resp_q.get(timeout=min(remaining, 0.5))
                except queue.Empty:
                    if self._proc.poll() is not None:
                        self.stats["crashes_detected"] += 1
                        raise FurseeWorkerCrashedError(
                            f"worker died rc={self._proc.poll()} during {rid}")
                    continue
                if item is None:  # stdout EOF
                    self.stats["crashes_detected"] += 1
                    raise FurseeWorkerCrashedError(
                        f"worker stdout closed (rc={self._proc.poll()}), "
                        f"stderr tail: {self._stderr_tail[-6:]}")
                try:
                    resp = json.loads(item.decode("utf-8", "replace").strip())
                    if not isinstance(resp, dict):
                        raise ValueError("response is not a JSON object")
                except (json.JSONDecodeError, ValueError) as e:
                    self.stats["protocol_errors"] += 1
                    raise FurseeProtocolError(f"non-JSON stdout line: {item[:120]!r} ({e})") from e
                if resp.get("id") != rid:
                    # 迟到响应（前一次 timeout 的结果等）：丢弃并继续等待本请求响应
                    self.stats["discarded_responses"] += 1
                    log.warning("RESPONSE discarded: id mismatch got=%s want=%s",
                                resp.get("id"), rid)
                    continue
                self.stats["responses"] += 1
                log.debug("RESPONSE id=%s ok=%s", rid, resp.get("ok"))
                return resp

    def _validate_result(self, resp: dict, image_path: str) -> dict:
        """校验 analyze 成功响应的结构与数值（bbox/confidence/embedding/norm/finite）。"""
        if not resp.get("ok"):
            error_type = resp.get("error_type", "unknown")
            cls = _ERROR_TYPE_MAP.get(error_type, FurseeInferenceError)
            exc = cls(f"[{error_type}] {resp.get('error', '')}")
            exc.error_type = error_type
            raise exc

        dets = resp.get("detections")
        if not isinstance(dets, list):
            raise FurseeProtocolError(f"missing detections list for {image_path}: {resp.keys()}")
        for i, d in enumerate(dets):
            bbox = d.get("bbox")
            if (not isinstance(bbox, list) or len(bbox) != 4
                    or not all(isinstance(v, int) for v in bbox)
                    or not (bbox[0] < bbox[2] and bbox[1] < bbox[3])):
                raise FurseeProtocolError(f"detection[{i}] bad bbox: {bbox!r}")
            conf = d.get("confidence")
            if not isinstance(conf, (int, float)) or not (0.0 <= float(conf) <= 1.0):
                raise FurseeProtocolError(f"detection[{i}] bad confidence: {conf!r}")
            emb = d.get("embedding")
            if not isinstance(emb, list) or len(emb) != 512:
                raise FurseeProtocolError(
                    f"detection[{i}] embedding dim != 512: {len(emb) if isinstance(emb, list) else type(emb)}")
            if any(v is None or v != v or v in (float("inf"), float("-inf")) for v in emb):
                raise FurseeProtocolError(f"detection[{i}] embedding has non-finite values")
            norm = d.get("embedding_norm")
            if not isinstance(norm, (int, float)) or abs(float(norm) - 1.0) > 1e-3:
                raise FurseeProtocolError(f"detection[{i}] embedding not L2-normalized: {norm!r}")
        return resp

    def __enter__(self) -> "FurseeAdapter":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.shutdown()
        except Exception:  # pragma: no cover - 关闭失败不掩盖业务异常
            log.exception("shutdown during context exit failed")
