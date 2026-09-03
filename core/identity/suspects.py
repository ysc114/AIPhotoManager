# core/identity/suspects.py
"""
角色中心 2.0 · 第二阶段：疑似同一角色（候选生成 + 人工决策持久化）。

设计约束（与 README「开发铁律」一致）：
- 不修改 Fursee 聚类阈值 0.79 / eps 0.6481，不重跑 DBSCAN，不改动
  identity_image 里任何已有行的 group_id（人工合并关系永久保留）。
- 候选生成是**纯只读**计算：对既有 Fursee 角色组取归一化 centroid
  （口径与 cluster.incremental_assign 的组代表完全一致：
  mean(成员 embedding) 后 L2 归一化），两两比较 cosine。
- 人工决策（"不是同一角色" / 最近一次合并快照）持久化在数据库旁的
  JSON sidecar（默认 identity_db_decisions.json），**不修改数据库 schema**。
- 合并仍走 IdentityDatabase.merge_group_members（检测级、保留全字段），
  IdentityManager.merge_groups 在合并前记录快照，供「撤销最近一次合并」。
"""

import json
import os
import tempfile

import numpy as np

#: 候选相似度下限：低于此值视为"不太可能同一角色"，不进入人工候选列表。
#: 注意 0.79 是 Fursee 自动合并的定稿阈值（勿改），候选线故意更低，
#: 只负责把"够像但没被自动合并"的组对交给人工终审。
DEFAULT_MIN_SIM = 0.60

#: 单次返回候选上限（防组数增长后候选列表失控）
DEFAULT_LIMIT = 200


# ============================================================
# 数学：组代表 + 跨组相似度
# ============================================================

def group_representatives(embeddings_by_group):
    """组代表 = 组内成员 embedding 均值后 L2 归一化（与 incremental_assign 同口径）。

    embeddings_by_group: {group_id: [np.ndarray, ...]}
    返回 {group_id: unit_vector}；无法归一化的组被跳过。
    """
    reps = {}
    for gid, vecs in embeddings_by_group.items():
        if not vecs:
            continue
        centroid = np.mean([np.asarray(v, dtype=np.float64) for v in vecs], axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            reps[gid] = centroid / norm
    return reps


def compute_suspect_pairs(reps, excluded=(), min_sim=DEFAULT_MIN_SIM,
                          limit=DEFAULT_LIMIT):
    """跨组 centroid 两两比较，返回疑似同一角色候选。

    参数:
        reps: {group_id: 单位向量}
        excluded: 已人工判定"不是同一角色"的组对（frozenset 集合），
                  这些组对不再生成候选（决策保留，除非组被合并/删除）。
        min_sim: 候选相似度下限（默认 0.60，仅人工候选线，
                 不影响 Fursee 0.79 自动合并阈值）。
        limit:   返回上限（按相似度降序取前 N）。

    返回: [(group_id_a, group_id_b, similarity), ...] 按相似度降序，
          相同相似度按 group_id 字典序（结果确定性）。
    """
    ids = sorted(reps)
    if len(ids) < 2:
        return []
    excluded = {frozenset(p) for p in (excluded or [])}
    cands = []
    for i in range(len(ids)):
        a = ids[i]
        va = reps[a]
        for b in ids[i + 1:]:
            pair = frozenset((a, b))
            if pair in excluded:
                continue
            sim = float(np.dot(va, reps[b]))
            if sim >= min_sim:
                cands.append((a, b, sim))
    cands.sort(key=lambda t: (-t[2], t[0], t[1]))
    if limit is not None and limit > 0:
        cands = cands[:limit]
    return cands


def pick_merge_direction(group_a, group_b, count_key="count"):
    """决定合并方向：照片更多的组保留为 target（其 character_id/名称保留），
    相同时保留 group_a。返回 (target_group, source_group)。

    合并只 UPDATE 成员行的 group_id，不重跑聚类；direction 纯粹是
    "保留哪个组 id" 的选择，对数据本身无影响。
    """
    count_a = int(group_a.get(count_key) or 0)
    count_b = int(group_b.get(count_key) or 0)
    if count_b > count_a:
        return group_b, group_a
    return group_a, group_b


# ============================================================
# 人工决策持久化（JSON sidecar，不动数据库 schema）
# ============================================================

class SuspectStore:
    """角色组对人工决策 + 最近一次合并快照（JSON sidecar）。

    文件格式（UTF-8）:
        {
          "version": 1,
          "not_same": [["gid_a", "gid_b"], ...],   # 已确认"不是同一角色"
          "last_merge": { ... } | null             # 单槽撤销记录
        }
    写入用临时文件 + os.replace 原子替换；读取失败按空状态处理（不抛）。
    """

    def __init__(self, path):
        self.path = path

    @classmethod
    def default_path(cls, db_path):
        """与数据库同目录同名的 *_decisions.json（随库走，测试天然隔离）。"""
        return os.path.splitext(str(db_path))[0] + "_decisions.json"

    # ---------- 读写 ----------

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return {"version": 1, "not_same": [], "last_merge": None}

    def _save(self, data):
        data = dict(data or {})
        data["version"] = 1
        if not isinstance(data.get("not_same"), list):
            data["not_same"] = []
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        fd, tmp = tempfile.mkstemp(
            prefix=".decisions_tmp_", suffix=".json", dir=directory
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    # ---------- "不是同一角色" 决策 ----------

    def not_same_pairs(self):
        """已确认"不是同一角色"的组对集合（frozenset）。"""
        data = self._load()
        pairs = set()
        for pair in data.get("not_same") or []:
            if isinstance(pair, list) and len(pair) >= 2:
                pairs.add(frozenset((str(pair[0]), str(pair[1]))))
        return pairs

    def add_not_same(self, group_id_a, group_id_b):
        """记录一对"不是同一角色"。已存在时返回 False（幂等）。"""
        a = str(group_id_a or "").strip()
        b = str(group_id_b or "").strip()
        if not a or not b or a == b:
            raise ValueError("不是同一角色：需要两个不同的角色组 ID")
        data = self._load()
        key = sorted([a, b])
        pairs = data.get("not_same") or []
        for pair in pairs:
            if isinstance(pair, list) and len(pair) >= 2 \
                    and sorted([str(pair[0]), str(pair[1])]) == key:
                return False
        pairs.append(key)
        data["not_same"] = pairs
        self._save(data)
        return True

    def remove_pairs_involving(self, group_ids):
        """删除涉及指定组的"不是同一"记录（组被合并/删除后记录失效）。"""
        ids = {str(g or "").strip() for g in (group_ids or []) if g}
        if not ids:
            return False
        data = self._load()
        kept = []
        changed = False
        for pair in data.get("not_same") or []:
            if isinstance(pair, list) and len(pair) >= 2 \
                    and any(str(p) in ids for p in pair):
                changed = True
                continue
            kept.append(pair)
        if changed:
            data["not_same"] = kept
            self._save(data)
        return changed

    def remove_pair(self, group_id_a, group_id_b):
        """删除一对"不是同一"记录（显式合并覆盖旧判定时用）。"""
        a = str(group_id_a or "").strip()
        b = str(group_id_b or "").strip()
        data = self._load()
        key = sorted([a, b])
        kept = []
        changed = False
        for pair in data.get("not_same") or []:
            if isinstance(pair, list) and len(pair) >= 2 \
                    and sorted([str(pair[0]), str(pair[1])]) == key:
                changed = True
                continue
            kept.append(pair)
        if changed:
            data["not_same"] = kept
            self._save(data)
        return changed

    # ---------- 最近一次合并快照（单槽撤销） ----------

    def last_merge(self):
        """最近一次合并快照 dict；无记录返回 None。"""
        rec = (self._load() or {}).get("last_merge")
        if not isinstance(rec, dict):
            return None
        return json.loads(json.dumps(rec))  # 深拷贝，防外部改动

    def set_last_merge(self, record):
        """保存最近一次合并快照（覆盖旧记录 —— 只保留最近一次）。"""
        data = self._load()
        data["last_merge"] = record
        self._save(data)

    def clear_last_merge(self):
        """清空撤销记录（撤销成功 / 记录无效时调用）。"""
        data = self._load()
        if data.get("last_merge") is not None:
            data["last_merge"] = None
            self._save(data)
