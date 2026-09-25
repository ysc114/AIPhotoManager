# core/visual_search/embedding.py
"""
视觉搜索 Embedding（OpenCLIP image encoder）—— 智能搜索第 2 层 · 第一阶段

与角色识别系统完全独立（不 import core.identity / model_hub / Fursee）：

- 模型默认复用与本地已缓存权重一致的 open_clip 架构
  （ViT-L-14 / datacomp_xl_s13b_b90k，离线可加载，零下载）；
  可用环境变量覆盖：VISUAL_SEARCH_MODEL / VISUAL_SEARCH_PRETRAINED
- 设备：VISUAL_SEARCH_DEVICE=cpu|cuda 强制；否则 GPU 可用走 GPU、
  不可用自动 CPU（不影响现有 Fursee/CLIP 分类模型实例）
- 输出 L2 归一化 float32 向量
- 单例 + 懒加载：模型只加载一次，绝不每张照片重新加载
"""

import os
import threading

import numpy as np

# 默认模型（与项目本地缓存的 open_clip 权重一致，离线可加载）
DEFAULT_MODEL_NAME = os.environ.get("VISUAL_SEARCH_MODEL", "ViT-L-14")
DEFAULT_PRETRAINED = os.environ.get(
    "VISUAL_SEARCH_PRETRAINED", "datacomp_xl_s13b_b90k")


def _prefer_offline_mode():
    """离线优先：权重已本地缓存，避免每次加载都向 HF 发 HEAD 请求。

    网络受限环境（本机）下在线检查会重试 15s+ 才回落本地缓存；
    需要联网下载新模型时设置 VISUAL_SEARCH_ALLOW_DOWNLOAD=1。
    """
    if os.environ.get("VISUAL_SEARCH_ALLOW_DOWNLOAD"):
        return
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    try:
        import huggingface_hub.constants as hf_const
        hf_const.HF_HUB_OFFLINE = True   # 兼容「hf 已先于本模块导入」的情况
    except Exception:
        pass


_prefer_offline_mode()

# 支持的后端设备
ALLOWED_DEVICES = ("cpu", "cuda")


def _cuda_available():
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def resolve_device(requested=None):
    """解析运行设备："cuda" 可用则默认 GPU，否则 CPU；支持 env 强制。"""
    if requested is None:
        requested = os.environ.get("VISUAL_SEARCH_DEVICE", "")
    requested = (requested or "").strip().lower()
    if requested in ALLOWED_DEVICES:
        return requested
    return "cuda" if _cuda_available() else "cpu"


class ClipImageEncoder:
    """OpenCLIP 图像编码器（懒加载 + 归一化 + 单例复用）。"""

    def __init__(self, model_name=None, pretrained=None, device=None):
        self.model_name = model_name or DEFAULT_MODEL_NAME
        self.pretrained = pretrained or DEFAULT_PRETRAINED
        self.requested_device = device
        self.device = None          # 加载后确定
        self._model = None
        self._preprocess = None
        self._dim = None
        self._lock = threading.Lock()

    # --------------------------------------------------------
    # 加载
    # --------------------------------------------------------
    def _ensure_loaded(self):
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            import open_clip
            self.device = resolve_device(self.requested_device)
            model, _, preprocess = open_clip.create_model_and_transforms(
                self.model_name, pretrained=self.pretrained)
            model.to(self.device)
            model.eval()
            self._model = model
            self._preprocess = preprocess
            self._dim = int(model.visual.output_dim)
            print(f"[visual_search] OpenCLIP 加载成功 "
                  f"(model={self.model_name}, device={self.device}, dim={self._dim})")

    def is_loaded(self):
        return self._model is not None

    @property
    def dim(self):
        self._ensure_loaded()
        return self._dim

    def model_info(self):
        """模型版本信息（用于索引一致性校验 / 升级追踪）。"""
        self._ensure_loaded()
        import open_clip
        return {
            "model_name": self.model_name,
            "pretrained": self.pretrained,
            "embedding_dimension": self._dim,
            "model_version": getattr(open_clip, "__version__", "unknown"),
            "device": self.device,
        }

    # --------------------------------------------------------
    # 编码
    # --------------------------------------------------------
    @staticmethod
    def _load_image(image):
        if isinstance(image, str):
            from PIL import Image
            return Image.open(image).convert("RGB")
        return image.convert("RGB")

    def encode(self, image, normalize=True):
        """单张图片 → L2 归一化 float32 向量（dim,）。失败抛异常。"""
        self._ensure_loaded()
        import torch
        img = self._preprocess(self._load_image(image)).unsqueeze(0).to(self.device)
        with torch.no_grad():
            feat = self._model.encode_image(img)
            if normalize:
                feat = feat / feat.norm(dim=-1, keepdim=True)
        vec = feat[0].cpu().numpy().astype(np.float32)
        return vec

    def encode_batch(self, paths, normalize=True, progress_cb=None):
        """批量编码（单次前向，比逐张调用快）。失败项返回 None。"""
        self._ensure_loaded()
        out = []
        for i, p in enumerate(paths):
            try:
                out.append(self.encode(p, normalize=normalize))
            except Exception as e:
                print(f"[visual_search] 编码失败 {p}: {e}")
                out.append(None)
            if progress_cb:
                progress_cb(i + 1, len(paths))
        return out

    def encode_text(self, texts, normalize=True):
        """文本 → L2 归一化向量（自然语言搜索，与图像同一 CLIP 空间）。

        texts: str 或 [str]。返回 (n, dim) numpy 或单条 (dim,)。
        """
        self._ensure_loaded()
        import torch
        import open_clip
        single = isinstance(texts, str)
        items = [texts] if single else list(texts)
        tokenizer = open_clip.get_tokenizer(self.model_name)
        tokens = tokenizer(items).to(self.device)
        with torch.no_grad():
            feat = self._model.encode_text(tokens)
            if normalize:
                feat = feat / feat.norm(dim=-1, keepdim=True)
        arr = feat.cpu().numpy().astype(np.float32)
        return arr[0] if single else arr

    def close(self):
        """释放模型（测试/进程退出用）。"""
        if self._model is not None:
            self._model.cpu()
            self._model = None
            self._preprocess = None
            self._dim = None


# 模块级单例
_encoder_instance = None
_encoder_lock = threading.Lock()


def get_encoder(force_new=False):
    """进程级共享编码器（模型只加载一次）。"""
    global _encoder_instance
    if force_new:
        return ClipImageEncoder()
    if _encoder_instance is None:
        with _encoder_lock:
            if _encoder_instance is None:
                _encoder_instance = ClipImageEncoder()
    return _encoder_instance


def reset_encoder():
    """释放单例（测试用）。"""
    global _encoder_instance
    with _encoder_lock:
        if _encoder_instance is not None:
            _encoder_instance.close()
        _encoder_instance = None
