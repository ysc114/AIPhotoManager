# core/visual_duplicates.py
"""
疑似重复照片（视觉相似度检测）—— 角色中心之外的新能力（路线图 ②）

与 MD5（duplicates.py）的差别：
- MD5 只能发现"内容完全一致"；本模块用**视觉指纹**找出：
  连拍、构图几乎一致、轻微模糊、曝光不同等"疑似重复"
- **绝不自动删除**：只输出"候选组"供人工确认，人工决策
  （保留某张 → 其余标记待清理；忽略该组）持久化在 JSON sidecar

视觉指纹（纯 PIL + numpy，无新依赖，线程安全）：
1. dHash（9x8 灰度差分哈希，64 位）：对缩放/曝光/压缩鲁棒
2. RGB 直方图（每通道 16 bins，L1 归一化 48 维）：区分"构图相似但
   颜色/主体不同"的误报
3. 技术指标（复用 core.photo_quality.technical）：清晰度/曝光差距
   → 给出"轻微糊/曝光不同"的理由提示
4. 同时流式计算 MD5：内容完全一致的副本不在视觉层重复推荐
   （由 MD5 页处理），避免两处重复显示

相似度规则（经验阈值，DEFAULT 常量便于校准）：
- combined = 0.45*dhash + 0.25*color + 0.30*gray（灰度结构相关性，曝光鲁棒）
- combined >= 0.86 或 dhash_sim >= 0.90 → 疑似同一"场景/连拍"候选组

路线图 ③：search_similar(query, top_k) —— 给定一张照片，返回库内
视觉最相似的 N 张（同场景/同角色/连拍），只读、不改任何数据。

缓存与决策：
- visual_similarity.json（项目根，gitignore）：
    { version, files: {path: fp}, ignored: [[p1,p2],...],
      resolved: {group_key: {kept, candidates: [...]}} }
- 只读照片、不写 identity_db、不删除任何文件
"""

import hashlib
import io
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from core.photo_quality.technical import technical_metrics

# 支持的图片扩展（与照片库一致）
_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# 相似度阈值（经验值；同场景连拍通常 dhash_sim >= 0.9）
DEFAULT_MIN_COMBINED = 0.86
DEFAULT_MIN_DHASH = 0.90

# 合并权重：dHash（结构）+ 灰度相关性（曝光鲁棒）+ 颜色（防误判）
_W_DHASH = 0.45
_W_COLOR = 0.25
_W_GRAY = 0.30

# 技术指标差异阈值（用于理由提示）
_SHARP_GAP = 0.12   # 清晰度差距 > 此值 → "轻微模糊差异"
_EXPO_GAP = 0.15    # 曝光差距 > 此值 → "曝光差异"

_INDEX_PATH = str(Path(__file__).resolve().parents[1] / "visual_similarity.json")


def norm_path(p):
    return str(Path(p).resolve()).replace("\\", "/")


# ============================================================
# 视觉指纹
# ============================================================

def _dhash(img, bits=8):
    """差分哈希：9x8 灰度 → bits² 位（水平相邻像素比较）。

    返回 Python int（低 bits 位为右端 bit）。对缩放/亮度变化鲁棒。
    """
    small = img.convert("L").resize((bits + 1, bits), Image.LANCZOS)
    arr = np.asarray(small, dtype=np.int32)
    diffs = arr[:, 1:] > arr[:, :-1]
    value = 0
    for y in range(bits):
        for x in range(bits):
            if diffs[y, x]:
                value |= (1 << (int(y) * bits + int(x)))
    return value


def _gray_vec(img, dim=32):
    """32x32 灰度中心化单位向量（结构相关性，对曝光/缩放鲁棒）。"""
    g = np.asarray(
        img.convert("L").resize((dim, dim), Image.LANCZOS), dtype=np.float64
    ).ravel()
    g = g - g.mean()
    n = np.linalg.norm(g)
    if n <= 0:
        return [0.0] * (dim * dim)
    return [round(float(v), 4) for v in (g / n)]


def _color_hist(img, bins=16):
    """RGB 三通道直方图，L1 归一化（48 维）→ 防主体颜色差异误判。"""
    arr = np.asarray(img.convert("RGB").resize((64, 64)), dtype=np.float32)
    hist = []
    for ch in range(3):
        h, _ = np.histogram(arr[..., ch], bins=bins, range=(0, 256))
        hist.extend(h.tolist())
    total = sum(hist)
    if total <= 0:
        return [0.0] * (bins * 3)
    return [round(v / total, 6) for v in hist]


def _bits_64(value):
    return bin(value).count("1")


def dhash_sim(a, b):
    """两个 64 位 dHash 的相似度（1 - 汉明距离/64）。"""
    return 1.0 - _bits_64(a ^ b) / 64.0


def color_sim(a, b):
    """直方图 L1 距离 → 相似度（1 - dist/2）。"""
    if len(a) != len(b) or not a:
        return 0.0
    dist = sum(abs(x - y) for x, y in zip(a, b))
    return max(0.0, min(1.0, 1.0 - dist / 2.0))


def gray_sim(a, b):
    """灰度结构向量余弦（32x32 中心化单位向量，对曝光差异稳健）。"""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return max(0.0, min(1.0, dot))


def combined_sim(a_dh, a_c, b_dh, b_c, a_g=None, b_g=None):
    """综合相似度：dHash 结构 + 色彩 + 灰度相关性。

    未提供灰度向量时退回旧的 dHash 主 + 颜色辅 权重（兼容旧调用）。
    """
    if a_g is not None and b_g is not None:
        return (_W_DHASH * dhash_sim(a_dh, b_dh)
                + _W_COLOR * color_sim(a_c, b_c)
                + _W_GRAY * gray_sim(a_g, b_g))
    return (0.70 * dhash_sim(a_dh, b_dh) + 0.30 * color_sim(a_c, b_c))


def compute_fingerprint(path):
    """计算单张照片指纹：MD5 + dHash + 直方图 + 技术指标 + 尺寸。

    path: 绝对路径。读取失败返回 None（调用方跳过）。
    返回 dict（可 JSON 序列化）。
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        md5 = hashlib.md5(data).hexdigest()
        img = Image.open(io.BytesIO(data))
        img.load()
    except (OSError, ValueError):
        return None
    try:
        tech = technical_metrics(img)
    except Exception:
        tech = {}
    return {
        "md5": md5,
        "dhash": _dhash(img),
        "hist": _color_hist(img),
        "gray": _gray_vec(img),
        "tech": tech,
        "w": img.width,
        "h": img.height,
    }


# ============================================================
# 索引 / 分组 / 决策持久化
# ============================================================

class VisualDuplicateIndex:
    """视觉指纹索引：计算/缓存/分组 + 人工决策（忽略组、保留选择）。

    线程安全：compute 在任意线程；save/load 用临时文件 + os.replace。
    """

    def __init__(self, photos_dir=None, index_path=None):
        self.photos_dir = str(photos_dir) if photos_dir else None
        self.index_path = index_path or _INDEX_PATH
        self._files = {}          # path -> fp dict
        self._ignored = []        # [ [p1, p2], ... ] 人工忽略的组对
        self._resolved = {}       # group_key -> {kept, candidates:[...]}
        self._load()

    # ---------- 持久化 ----------

    def _data(self):
        return {
            "version": 1,
            "files": self._files,
            "ignored": self._ignored,
            "resolved": self._resolved,
        }

    def _load(self):
        try:
            with open(self.index_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        self._files = data.get("files") or {}
        self._ignored = data.get("ignored") or []
        self._resolved = data.get("resolved") or {}

    def stale_count(self):
        """指纹记录中指向已不存在文件的数量（只读）。"""
        return sum(1 for k in self._files if not os.path.exists(k))

    def prune_missing(self):
        """删除指向已删除照片的指纹记录（保留人工忽略/保留判定）。

        只清理缓存，不动任何照片文件；重新出现的新文件会重新计算指纹。
        """
        gone = [k for k in list(self._files) if not os.path.exists(k)]
        for k in gone:
            self._files.pop(k, None)
        if gone:
            self.save()
        return {"missing": len(gone), "kept": len(self._files)}

    def save(self):
        data = self._data()
        directory = os.path.dirname(os.path.abspath(self.index_path)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=".visual_tmp_", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            os.replace(tmp, self.index_path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    # ---------- 目录 ----------

    def _resolve_dir(self):
        if self.photos_dir:
            return str(self.photos_dir)
        return norm_path(Path(__file__).resolve().parent.parent / "photos")

    def photo_files(self):
        d = self._resolve_dir()
        if not os.path.isdir(d):
            return []
        return sorted(
            os.path.join(d, n) for n in os.listdir(d)
            if os.path.splitext(n)[1].lower() in _PHOTO_EXTS
        )

    def stale_files(self):
        """需要重算指纹的文件：新出现 / 尺寸时间变化 / 从未索引。"""
        out = []
        for p in self.photo_files():
            try:
                st = os.stat(p)
            except OSError:
                continue
            rec = self._files.get(norm_path(p))
            if rec is None or rec.get("size") != st.st_size \
                    or rec.get("mtime_ns") != st.st_mtime_ns:
                out.append(p)
        return out

    # ---------- 计算 ----------

    def compute_all(self, progress_cb=None):
        """增量计算指纹（跳过 up-to-date 文件），完成后保存并返回统计。"""
        todo = self.stale_files()
        total = len(todo)
        done = 0
        failed = 0
        for p in todo:
            try:
                st = os.stat(p)
                fp = compute_fingerprint(p)
            except OSError:
                st = None
                fp = None
            if fp is not None:
                fp["size"] = st.st_size
                fp["mtime_ns"] = st.st_mtime_ns
                self._files[norm_path(p)] = fp
            else:
                # 损坏/不可读：记录墓碑（mtime+size），避免每次刷新反复重算
                if st is not None:
                    self._files[norm_path(p)] = {
                        "failed": True, "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                    }
                failed += 1
            done += 1
            if progress_cb and (done % 10 == 0 or done == total):
                progress_cb(done, total)
        self.save()
        return {"total": total, "computed": total - failed, "failed": failed}

    # ---------- 分组 ----------

    def _md5_groups(self):
        """md5 -> [path...]（排除内容完全一致的副本：只留条目第一张参与视觉层）。"""
        groups = {}
        for p in self._files:
            groups.setdefault(self._files[p].get("md5"), []).append(p)
        primary = {}
        for md5, paths in groups.items():
            for p in sorted(paths)[1:]:
                primary[p] = True   # 副本 → 视觉层不参与（MD5 页处理）
        return primary

    def _ignored_pairs(self):
        return set(frozenset((a, b)) for a, b in self._ignored)

    def groups(self, min_combined=DEFAULT_MIN_COMBINED,
               min_dhash=DEFAULT_MIN_DHASH, max_group=8):
        """返回疑似重复候选组（已排除：忽略组、已解决组、MD5 相同副本）。

        每个组: {
          "score": float,          # 组内两两最大综合相似度
          "reason": str,           # 连拍/构图相似/清晰度差异/曝光差异
          "photos": [ {path,name,size,sharp,exposure,kept,candidate,w,h}, ... ],
        }
        已"保留某张"的组带 kept/candidate 标记（仍展示，供查看）。
        """
        # 只保留参与视觉层的文件（MD5 相同副本归 MD5 页处理；损坏墓碑跳过）
        excluded_md5 = self._md5_groups()
        ignored = self._ignored_pairs()
        paths = sorted(
            p for p in self._files
            if p not in excluded_md5 and self._files[p].get("dhash") is not None
        )
        n = len(paths)
        if n < 2:
            return []

        # 两两候选（阈值剪枝：先算 dhash，快速拒绝明显不同的）
        raw_pairs = []
        for i in range(n):
            a = paths[i]
            fa = self._files[a]
            for j in range(i + 1, n):
                b = paths[j]
                pair = frozenset((a, b))
                if pair in ignored:
                    continue
                fb = self._files[b]
                ds = dhash_sim(fa["dhash"], fb["dhash"])
                if ds < 0.78:          # 快速拒绝（dHash 明显不同）
                    continue
                sim = combined_sim(fa["dhash"], fa["hist"], fb["dhash"], fb["hist"],
                                   fa.get("gray"), fb.get("gray"))
                if sim >= min_combined or ds >= min_dhash:
                    raw_pairs.append((sim, ds, a, b))

        # 并查集聚合成分组（仅参与文件）
        parent = {p: p for p in paths}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x, y):
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[ry] = rx

        pair_max = {}
        for sim, ds, a, b in raw_pairs:
            union(a, b)
            key = frozenset((a, b))
            if key not in pair_max or sim > pair_max[key][0]:
                pair_max[key] = (sim, ds, a, b)

        clusters = {}
        for p in paths:
            clusters.setdefault(find(p), []).append(p)

        groups = []
        for root, members in clusters.items():
            if len(members) < 2:
                continue
            nodes = sorted(members)[:max_group]
            # 组内最高分 + 理由
            best = (0.0, 0.0)
            for x in range(len(nodes)):
                for y in range(x + 1, len(nodes)):
                    k = frozenset((nodes[x], nodes[y]))
                    if k in pair_max:
                        best = max(best, pair_max[k][:2])
            score, _ds = best
            groups.append({
                "score": round(score, 4),
                "reason": self._group_reason(nodes),
                "photos": [self._photo_item(p) for p in nodes],
            })
        groups.sort(key=lambda g: -g["score"])
        return groups

    def _photo_item(self, p):
        fp = self._files[p]
        try:
            size = os.path.getsize(p)
        except OSError:
            size = 0
        tech = fp.get("tech") or {}
        return {
            "path": p,
            "name": os.path.basename(p),
            "size": size,
            "sharp": float(tech.get("sharpness", 0.0)),
            "exposure": float(tech.get("exposure", 0.0)),
            "w": int(fp.get("w", 0)),
            "h": int(fp.get("h", 0)),
        }

    def _group_reason(self, nodes):
        """根据组内技术指标差给出理由提示。"""
        fp = [self._files[p].get("tech") or {} for p in nodes]
        sharps = [float(f.get("sharpness", 0.0)) for f in fp]
        exposures = [float(f.get("exposure", 0.0)) for f in fp]
        if sharps and (max(sharps) - min(sharps)) > _SHARP_GAP:
            return "清晰度差异（可能有轻微糊片）"
        if exposures and (max(exposures) - min(exposures)) > _EXPO_GAP:
            return "曝光差异"
        return "连拍/构图相似"

    # ---------- 人工决策 ----------

    def resolve(self, keep_path, candidate_paths):
        """用户保留 keep_path：其余标记为待清理候选（不删除）。

        安全约束（防误删）：
        - 候选里若含「其它组已保留」的照片，一律跳过（保留优先于待清理）
        - 本次保留的路径会从其它记录的候选里移除，避免「既保留又待清理」
        """
        key = norm_path(keep_path)
        kept_elsewhere = {k for k in self._resolved if k != key}
        item = self._resolved.get(key) or {"kept": key, "candidates": []}
        merged = [c for c in item["candidates"]
                  if c != key and c not in kept_elsewhere]
        for p in candidate_paths:
            np = norm_path(p)
            if np != key and np not in kept_elsewhere and np not in merged:
                merged.append(np)
        self._resolved[key] = {"kept": key, "candidates": merged,
                               "at": datetime.now().isoformat(timespec="seconds")}
        for other_key, rec in self._resolved.items():
            if other_key == key:
                continue
            cands = rec.get("candidates", [])
            if key in cands:
                rec["candidates"] = [c for c in cands if c != key]
        self.save()

    def is_resolved(self, path):
        """该路径是否已被标记为"保留"。"""
        return norm_path(path) in self._resolved

    def candidate_mark(self, path):
        """是否为待清理候选（被保留的路径永远返回 False）。"""
        np_ = norm_path(path)
        if np_ in self._resolved:
            return False
        for rec in self._resolved.values():
            if np_ in rec.get("candidates", []):
                return True
        return False

    def resolve_info(self, path):
        """保留路径对应的决策信息 dict 或 None。"""
        return self._resolved.get(norm_path(path))

    def ignore_group(self, any_path_a, any_path_b):
        """人工忽略该组：组对记录为忽略（后续不再推荐）。"""
        a = norm_path(any_path_a)
        b = norm_path(any_path_b)
        key = sorted([a, b])
        if key not in self._ignored:
            self._ignored.append(key)
            self.save()

    def is_ignored(self, a, b):
        return sorted([norm_path(a), norm_path(b)]) in self._ignored

    def pending_cleanup(self):
        """全部已标记「待清理候选」路径（去重）。

        安全约束：永远排除任何被保留的路径；保留照片已不在磁盘的记录整条跳过
        （否则可能把该组剩余副本也清空）。
        """
        kept = {k for k in self._resolved if os.path.exists(k)}
        out = set()
        for key, rec in self._resolved.items():
            if key not in kept:
                continue
            for c in rec.get("candidates", []):
                if c not in kept:
                    out.add(c)
        return sorted(out)

    def remove_entries(self, paths):
        """从索引移除已删除文件的条目（文件删除后调用）。

        本类为指纹索引（_files dict）；移除后落盘，
        同时清理 resolved 中已消失的候选路径。返回移除条数。
        """
        gone = {norm_path(p) for p in paths}
        removed = 0
        for p in list(self._files):
            if p in gone:
                del self._files[p]
                removed += 1
        if removed:
            self.save()
        # resolved：已消失的候选路径移除（保留照片不动）
        changed = False
        for key, rec in list(self._resolved.items()):
            cands = [c for c in rec.get("candidates", []) if os.path.exists(c)]
            if len(cands) != len(rec.get("candidates", [])):
                rec["candidates"] = cands
                changed = True
        if changed:
            self.save()
        return removed


    # ---------- ③ 相似照片搜索（给定一张 → 找库内最相似） ----------

    def search_similar(self, query_path, top_k=12, min_sim=0.55):
        """检索与 query_path 视觉最相似的照片（纯只读，不修改任何数据）。

        query 未入索引时现场计算指纹（不落盘索引）。
        返回: [ {path,name,size,sharp,exposure,w,h,score}, ... ]
        按相似度降序，最多 top_k 张。
        """
        qp = norm_path(query_path)
        q = self._files.get(qp) or compute_fingerprint(query_path)
        if not q or q.get("dhash") is None:
            return []
        results = []
        for p, fp in self._files.items():
            if p == qp or fp.get("dhash") is None:
                continue
            sim = combined_sim(q["dhash"], q["hist"], fp["dhash"], fp["hist"],
                               q.get("gray"), fp.get("gray"))
            if sim < min_sim:
                continue
            item = self._photo_item(p)
            item["score"] = round(sim, 4)
            results.append(item)
        results.sort(key=lambda r: -r["score"])
        return results[:max(0, int(top_k))]


# 模块级默认索引（项目根）
default_index = VisualDuplicateIndex()
