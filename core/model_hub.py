# core/model_hub.py
"""
GPU 模型统一管理器（Stage 4B / ModelHub）。

全局单例（通过 ``get_model_hub()`` 获取，与 ``core.analysis_cache.get_cache()``
同一模式），解决此前一次整理流程中 CLIP / YOLO / InsightFace 被重复加载的问题：

    重构前（缓存命中场景）: 2 x CLIP + 2 x YOLO + 1 x InsightFace
    重构后:               1 x CLIP + 1 x YOLO + 1 x InsightFace（共享实例）

设计要点（Stage 4A 报告确认）：
    - 懒加载：程序启动不加载任何模型，首次 get_xxx() 才加载
    - 统一 device：首次使用时决定一次 "cuda"/"cpu"，三个模型共用
    - 线程安全：每个模型一把 threading.Lock（当前单线程，为未来预留）
    - 检测缓存：进程内存级 dict[path -> list[Detection]]，key 与
      analysis_cache 相同规则归一化（反斜杠 -> 正斜杠），避免
      AIClassifier 与 IdentityEmbedding 对同一图片重复 YOLO 推理
    - reset()：清空全部实例与缓存，仅供测试使用

不改变任何模型的名称、权重与推理逻辑。
"""

import threading

# YOLO-World 检测类别（与 Stage 4B 之前两处 set_classes 完全一致，勿改）
_YOLO_CLASSES = [
    "fursuit", "furry character", "person",
    "anthropomorphic animal", "cartoon character", "furry",
]

# AIClassifier / IdentityEmbedding 共用的检测置信度阈值（原值保持不变）
DETECT_CONFIDENCE_THRESHOLD = 0.15


def _normalize_path(path):
    """与 core.analysis_cache 相同的 key 归一化规则。"""
    return str(path).replace("\\", "/")


class ModelHub:
    """GPU 模型统一管理器。"""

    def __init__(self):
        self._device = None
        self._clip = None
        self._yolo = None
        self._insightface = None
        # insightface 加载失败时置 False，避免每次重试（沿用 embedding.py 原策略）
        self._insightface_failed = False
        # 进程内存级检测缓存：normalized path -> list[Detection]
        self._detection_cache = {}
        # 每类资源一把锁
        self._device_lock = threading.Lock()
        self._clip_lock = threading.Lock()
        self._yolo_lock = threading.Lock()
        self._insightface_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        # 可注入的工厂（测试时替换为 fake，不加载真实权重）
        self._factories = {
            "clip": self._factory_clip,
            "yolo": self._factory_yolo,
            "insightface": self._factory_insightface,
        }

    # ============================================================
    # device
    # ============================================================

    def get_device(self):
        """返回统一设备标识 "cuda" / "cpu"。只决策一次。"""
        if self._device is None:
            with self._device_lock:
                if self._device is None:
                    self._device = "cuda" if self._cuda_available() else "cpu"
        return self._device

    @staticmethod
    def _cuda_available():
        try:
            import torch
            return bool(torch.cuda.is_available())
        except Exception:
            return False

    # 允许测试替换 CUDA 探测
    _torch_cuda_available = staticmethod(_cuda_available)

    # ============================================================
    # 模型工厂（真实实现，懒加载）
    # ============================================================

    def _factory_clip(self, device):
        from core.clip_model import CLIPModel
        return CLIPModel(device=device)

    def _factory_yolo(self, device):
        from core.yolo_detector import YOLODetector
        detector = YOLODetector(model_version="world", device=device)
        detector.set_classes(_YOLO_CLASSES)
        return detector

    def _factory_insightface(self, device):
        import insightface
        if device == "cuda":
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        else:
            providers = ["CPUExecutionProvider"]
        app = insightface.app.FaceAnalysis(name="buffalo_l", providers=providers)
        app.prepare(ctx_id=0, det_size=(640, 640))
        return app

    # ============================================================
    # 公共访问接口
    # ============================================================

    def get_clip(self):
        """共享 CLIPModel 实例（懒加载）。"""
        if self._clip is None:
            with self._clip_lock:
                if self._clip is None:
                    device = self.get_device()
                    self._clip = self._factories["clip"](device)
                    print(f"[ModelHub] CLIP 加载成功 (device={device})")
        return self._clip

    def get_yolo(self):
        """共享 YOLODetector 实例（懒加载，classes 已预设）。"""
        if self._yolo is None:
            with self._yolo_lock:
                if self._yolo is None:
                    device = self.get_device()
                    self._yolo = self._factories["yolo"](device)
                    print(f"[ModelHub] YOLO-World 加载成功 (device={device})")
        return self._yolo

    def get_insightface(self):
        """共享 InsightFace 实例（懒加载）。不可用时返回 None。"""
        if self._insightface_failed:
            return None
        if self._insightface is None:
            with self._insightface_lock:
                if self._insightface is None and not self._insightface_failed:
                    try:
                        device = self.get_device()
                        self._insightface = self._factories["insightface"](device)
                        print(f"[ModelHub] InsightFace 加载成功 (device={device})")
                    except ImportError:
                        raise ImportError(
                            "请安装 insightface：pip install insightface onnxruntime"
                        )
                    except Exception as e:
                        # 沿用 embedding.py 原降级策略：失败不致命，返回 None
                        print(f"[ModelHub] InsightFace 加载失败: {e}")
                        self._insightface_failed = True
                        return None
        return self._insightface

    # ============================================================
    # 检测缓存（消除 AIClassifier 与 IdentityEmbedding 重复检测）
    # ============================================================

    def get_detections(self, image_path, image=None):
        """获取图片的 YOLO 检测结果（带内存缓存）。

        - 缓存命中：直接返回（不发起新推理）
        - 缓存未命中：用共享 YOLO 检测（confidence=0.15，与原两处调用一致），
          存入缓存后返回
        - key 使用与 analysis_cache 相同的路径归一化规则
        """
        key = _normalize_path(image_path)
        with self._cache_lock:
            if key in self._detection_cache:
                return self._detection_cache[key]

        if image is None:
            from PIL import Image
            image = Image.open(image_path).convert("RGB")

        detections = self.get_yolo().detect_persons(
            image, confidence_threshold=DETECT_CONFIDENCE_THRESHOLD
        )
        with self._cache_lock:
            self._detection_cache[key] = detections
        return detections

    def clear_detection_cache(self):
        """清空检测缓存。"""
        with self._cache_lock:
            self._detection_cache.clear()

    # ============================================================
    # 生命周期
    # ============================================================

    def reset(self):
        """清空所有模型实例与缓存。仅供测试使用。"""
        with self._clip_lock:
            self._clip = None
        with self._yolo_lock:
            self._yolo = None
        with self._insightface_lock:
            self._insightface = None
            self._insightface_failed = False
        with self._cache_lock:
            self._detection_cache = {}
        with self._device_lock:
            self._device = None


# ============================================================
# 模块级单例（与 get_cache() 同模式）
# ============================================================

_hub_instance = None
_hub_lock = threading.Lock()


def get_model_hub():
    """返回全局 ModelHub 单例。"""
    global _hub_instance
    if _hub_instance is None:
        with _hub_lock:
            if _hub_instance is None:
                _hub_instance = ModelHub()
    return _hub_instance


def reset_model_hub():
    """重置全局单例（测试用）。"""
    global _hub_instance
    with _hub_lock:
        if _hub_instance is not None:
            _hub_instance.reset()
        _hub_instance = None
