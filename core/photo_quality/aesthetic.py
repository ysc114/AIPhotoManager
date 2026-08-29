"""aesthetic.py —— 美学评分。

第一版实现「技术启发式美学分」（纯 CPU、零模型、线程安全）：
把清晰度 / 曝光 / 对比度 / 饱和度 / 边缘能量合成一个 0~1 的美学估计。

预留 CLIP 可选通道（参考 photo-sorter 用 CLIP 文本相似度打 aesthetic）：
当 use_clip=True 且 ModelHub 的 CLIP 可用时，用一组美学提示词对整图
编码做余弦相似度加权；加载/推理失败自动降级回技术启发式，绝不抛异常。

说明：不加载任何新模型（复用项目已有 CLIP 实例），避免新增重量级依赖。
"""

import threading

_CLIP_LOCK = threading.Lock()

# 美学提示词（与 photo-sorter 的 aesthetic 思路同源）
_AESTHETIC_PROMPTS = [
    "a high quality professional photograph, well composed, sharp focus",
    "a beautiful aesthetic photo with pleasing colors and lighting",
    "an ordinary snapshot, slightly dull",
    "a blurry low quality photo",
]


def _technical_aesthetic(metrics):
    """技术启发式美学：重点看清晰度/曝光/边缘，饱和度轻权重。"""
    w = {"sharpness": 0.40, "exposure": 0.25, "edge": 0.20,
         "contrast": 0.10, "saturation": 0.05}
    return round(sum(w[k] * float(metrics.get(k, 0.0)) for k in w), 4)


def _clip_aesthetic(img):
    """CLIP 美学近似（可选）。返回 0~1；任何失败返回 None。

    在 _CLIP_LOCK 保护下串行推理（ModelHub 的 CLIP 共享实例非线程安全）。
    """
    try:
        from core.model_hub import model_hub
        clip = model_hub.get_clip()
        if clip is None:
            return None
        import torch
        with _CLIP_LOCK:
            feats = clip.encode_image(img).cpu()
            n = feats.norm(dim=-1, keepdim=True)
            feats = feats / n.clamp(min=1e-6)
            # 用 clip 的 tokenizer 编码提示词
            tok = clip.tokenizer(_AESTHETIC_PROMPTS)
            with torch.no_grad():
                txt = clip.model.encode_text(tok.to(clip.device))
                txt = txt / txt.norm(dim=-1, keepdim=True)
            sim = (feats @ txt.T)[0].cpu().numpy()  # 4 个提示词相似度
        # 正向提示 - 反向提示
        pos = 0.5 * (sim[0] + sim[1])
        neg = 0.5 * (sim[2] + sim[3])
        return float(max(0.0, min(1.0, 0.5 + 0.5 * (pos - neg))))
    except Exception as e:
        print(f"[photo_quality] CLIP 美学降级为技术启发式: {e}")
        return None


def aesthetic_score(img, metrics=None, use_clip=False):
    """美学评分入口。

    img: PIL.Image（已 RGB）
    metrics: 可选，避免重复计算技术指标；None 时内部计算
    use_clip: True 时尝试 CLIP 通道（失败自动降级）
    返回 0~1。
    """
    if metrics is None:
        from .technical import technical_metrics
        metrics = technical_metrics(img)

    tech = _technical_aesthetic(metrics)

    if use_clip:
        clip_v = _clip_aesthetic(img)
        if clip_v is not None:
            # CLIP 与技术的加权融合，两者都重要
            return round(0.55 * clip_v + 0.45 * tech, 4)
    return round(tech, 4)
