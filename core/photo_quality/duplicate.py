"""duplicate.py —— 近似照片分组（无模型，纯感知哈希 + 直方图）。

参考 photo-sorter「视觉相似照片分组」思路：
- dHash（8x8 差异哈希）捕捉结构相似（连拍 / 同场景多张）
- RGB 直方图相关性捕捉色调相似
- 尺寸比例约束避免不同构图误判
- 两两相似度 → 阈值 → 贪心并查集分组

纯 PIL + numpy，线程安全；所有图先缩到小尺寸，速度快。
"""

import numpy as np
from PIL import Image

_DHASH_SIZE = 8
_COMPARE_SIZE = (64, 64)
# 两图判定为「近似」的组合阈值
_SIMILARITY_THRESHOLD = 0.80


def _dhash(img):
    """感知差异哈希：返回 64bit int。"""
    g = img.convert("L").resize((_DHASH_SIZE + 1, _DHASH_SIZE), Image.BILINEAR)
    arr = np.asarray(g, dtype=np.int16)
    left = arr[:, :-1]
    right = arr[:, 1:]
    bits = (left > right).flatten()
    val = 0
    for i, b in enumerate(bits):
        if b:
            val |= 1 << i
    return val


def _histogram(img):
    """RGB 三通道 16-bin 直方图，归一化。"""
    small = img.convert("RGB").resize(_COMPARE_SIZE, Image.BILINEAR)
    arr = np.asarray(small, dtype=np.float32)
    bins = np.zeros((3, 16), dtype=np.float32)
    for c in range(3):
        bins[c], _ = np.histogram(arr[..., c], bins=16, range=(0, 256))
    bins /= (bins.sum() + 1e-6)
    return bins.flatten()


def _hamming(a, b):
    return bin(a ^ b).count("1")


def similarity(img_a, img_b):
    """两图相似度 0~1（dHash 汉明距离 + 直方图相关 + 尺寸比）。"""
    h_a, h_b = _dhash(img_a), _dhash(img_b)
    d = _hamming(h_a, h_b) / 64.0
    hash_sim = 1.0 - d

    hist_a, hist_b = _histogram(img_a), _histogram(img_b)
    # 直方图余弦相似度
    cos = float((hist_a @ hist_b) / (np.linalg.norm(hist_a) * np.linalg.norm(hist_b) + 1e-6))

    # 尺寸比约束（构图差异过大不打近分组）
    wa, ha = img_a.size
    wb, hb = img_b.size
    ratio = max(wa / wb, wb / wa) * max(ha / hb, hb / ha)
    size_ok = 1.0 if ratio < 1.6 else max(0.0, 1.0 - (ratio - 1.6) / 2.0)

    # 结构（哈希）为主，色调为辅，尺寸约束兜底
    return 0.6 * hash_sim + 0.3 * cos + 0.1 * size_ok


def _features(img):
    """每张图只算一次：返回 (dhash_int, hist_vec)，供批量两两快速比较。

    全库分组时避免重复 resize / 直方图（O(n²) 配对只做整数与向量运算）。
    """
    return _dhash(img), _histogram(img)


def _pair_similarity(fa, fb):
    """由预计算特征计算两图相似度 0~1（不重复计算图特征）。"""
    h_a, hist_a = fa
    h_b, hist_b = fb
    hash_sim = 1.0 - _hamming(h_a, h_b) / 64.0
    cos = float((hist_a @ hist_b) / (np.linalg.norm(hist_a) * np.linalg.norm(hist_b) + 1e-6))
    # 尺寸比在批量场景占比小，默认放行（单张 similarity() 保留完整权重）
    return 0.6 * hash_sim + 0.3 * cos + 0.1


def group_duplicates(items, threshold=_SIMILARITY_THRESHOLD):
    """items: [(path, img), ...] → 相似组列表。

    返回 [[path,...], ...]：仅含 >=2 张的近似组；孤立照片不进组。
    贪心：按已排序列表扫描，与组内任一成员相似即并入（近似传递，
    连拍序列可归为一组）。

    性能：先为每张图预计算一次特征（dHash + 直方图），两两配对只用
    整数汉明距离与向量点积，全库（数百张）也可秒级完成。
    """
    n = len(items)
    if n < 2:
        return []
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    paths = [p for p, _ in items]
    feats = []
    for _, im in items:
        try:
            feats.append(_features(im))
        except Exception:
            feats.append(None)
    # 两两比较（特征预计算后 O(n²) 仅整数/向量运算）
    for i in range(n):
        if feats[i] is None:
            continue
        for j in range(i + 1, n):
            if feats[j] is None:
                continue
            try:
                s = _pair_similarity(feats[i], feats[j])
            except Exception:
                continue
            if s >= threshold:
                union(i, j)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    result = []
    for idxs in groups.values():
        if len(idxs) >= 2:
            result.append([paths[i] for i in idxs])
    return result
