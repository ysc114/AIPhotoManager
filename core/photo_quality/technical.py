"""technical.py —— 照片基础技术指标（清晰度 / 曝光 / 对比度 / 饱和度 / 边缘能量）。

纯 PIL + numpy 实现，无模型、线程安全，可在后台线程直接调用。
所有指标归一化到 0~1（越高越好），供 aesthetic / selector 合成。
"""

import numpy as np


def _to_gray_array(img):
    """转 0~255 灰度 numpy 数组。"""
    return np.asarray(img.convert("L"), dtype=np.float32)


def _to_rgb_array(img):
    return np.asarray(img.convert("RGB"), dtype=np.float32)


def _clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def sharpness_score(img):
    """清晰度：Laplacian 方差归一化（模糊图低、清晰图高）。"""
    gray = _to_gray_array(img)
    if gray.size == 0:
        return 0.0
    # 手动 3x3 Laplacian 卷积（比 PIL 内置更可控）
    kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)
    h, w = gray.shape
    if h < 3 or w < 3:
        return 0.0
    # 用 pad + 滑窗近似卷积
    padded = np.pad(gray, 1, mode="edge")
    acc = np.zeros_like(gray)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            acc += kernel[dy + 1, dx + 1] * padded[1 + dy:1 + dy + h, 1 + dx:1 + dx + w]
    var = float(acc.var())
    # 经验映射：方差 0~3000 映射到 0~1（对数压缩更平滑）
    if var <= 0:
        return 0.0
    return _clamp(np.log1p(var) / np.log1p(3000))


def exposure_score(img):
    """曝光：灰度均值接近中灰（~118）时最佳，过暗/过亮惩罚。"""
    gray = _to_gray_array(img)
    if gray.size == 0:
        return 0.0
    mean = float(gray.mean())
    # 118 为中心，±90 为容忍区，超出线性衰减
    target = 118.0
    dev = abs(mean - target)
    if dev <= 70:
        return _clamp(1.0 - dev / 90.0)
    return _clamp(max(0.0, 0.25 - (dev - 70) / 800.0))


def contrast_score(img):
    """对比度：灰度标准差（适度对比最佳，过大过小惩罚）。"""
    gray = _to_gray_array(img)
    if gray.size == 0:
        return 0.0
    std = float(gray.std())
    # 目标 ~55（8bit 图的典型良好对比）
    dev = abs(std - 55.0)
    if dev <= 35:
        return _clamp(1.0 - dev / 45.0)
    return _clamp(0.25 - (dev - 35) / 600.0)


def saturation_score(img):
    """饱和度：HSV S 通道均值（适中偏上最佳）。"""
    arr = _to_rgb_array(img)
    if arr.size == 0:
        return 0.0
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    denom = mx + 1e-6
    sat = np.where(mx > 0, (mx - mn) / denom, 0.0)
    s = float(sat.mean())
    # 0.25~0.55 为舒适区
    if s <= 0.45:
        return _clamp(s / 0.45)
    return _clamp(1.0 - (s - 0.45) / 0.55)


def edge_energy_score(img):
    """边缘能量：Sobel 梯度幅值均值归一化（细节丰富度）。"""
    gray = _to_gray_array(img)
    if gray.size == 0:
        return 0.0
    h, w = gray.shape
    if h < 3 or w < 3:
        return 0.0
    padded = np.pad(gray, 1, mode="edge")
    gx = padded[2:, 1:-1] - padded[:-2, 1:-1]
    gy = padded[1:-1, 2:] - padded[1:-1, :-2]
    mag = np.sqrt(gx.astype(np.float32) ** 2 + gy.astype(np.float32) ** 2)
    mean_mag = float(mag.mean())
    # 0~80 映射（对数压缩）
    return _clamp(np.log1p(mean_mag) / np.log1p(80))


def technical_metrics(img):
    """计算全部技术指标，返回 dict（均为 0~1，越高越好）。"""
    return {
        "sharpness": round(sharpness_score(img), 4),
        "exposure": round(exposure_score(img), 4),
        "contrast": round(contrast_score(img), 4),
        "saturation": round(saturation_score(img), 4),
        "edge": round(edge_energy_score(img), 4),
    }


# 技术综合分（0~1）
_TECH_WEIGHTS = {
    "sharpness": 0.35,
    "exposure": 0.25,
    "contrast": 0.15,
    "saturation": 0.10,
    "edge": 0.15,
}


def technical_score(metrics):
    """由指标字典合成技术分。"""
    return round(sum(_TECH_WEIGHTS[k] * float(metrics.get(k, 0.0))
                     for k in _TECH_WEIGHTS), 4)
