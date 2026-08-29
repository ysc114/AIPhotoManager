"""selector.py —— 从相似照片组中选择最佳照片 + 生成推荐理由。

规则：
- 相似组内：选 score 最高者（同分比清晰度）为该组代表 → 入选「AI精选」；
  组内其余照片折叠进该组的「相似照片」列表；
- 非相似组的孤立高分照片（score 高于阈值）同样入选；
- 精选列表按 score 降序；
- 推荐理由由技术指标 + 分组信息生成中文说明。
"""

# 孤立照片入选精选的最低分
_PICK_THRESHOLD = 0.55
# 相似组代表的最低分（组内最佳也不达标则不入选）
_GROUP_PICK_THRESHOLD = 0.50


def _reason(metrics, in_group, group_size):
    parts = []
    sh = float(metrics.get("sharpness", 0.0))
    if sh >= 0.6:
        parts.append("清晰度高")
    elif sh < 0.3:
        parts.append("清晰度一般")
    ex = float(metrics.get("exposure", 0.0))
    if ex >= 0.7:
        parts.append("曝光良好")
    elif ex < 0.3:
        parts.append("曝光偏差")
    co = float(metrics.get("contrast", 0.0))
    if co >= 0.6:
        parts.append("对比度佳")
    sat = float(metrics.get("saturation", 0.0))
    if sat >= 0.5:
        parts.append("色彩饱满")
    if in_group:
        parts.append(f"相似组 {group_size} 张中最佳")
    return "；".join(parts) if parts else "综合表现均衡"


def select_best(photos, groups):
    """从照片分析结果与相似组中产出 AI 精选。

    photos: {path: {"score", "technical": {...}, "aesthetic", "group_id"}}
    groups: [[path, ...], ...]（相似组，来自 duplicate.group_duplicates）

    返回 dict:
        {
          "picks": [ {path, score, technical, aesthetic, reason, group, group_size}, ...],
          "groups": [ {group_id, members:[path...], best:path, best_score}, ...],
          "total": N
        }
    """
    # 组 → 代表
    group_id_of = {}
    group_info = []
    for gi, gpaths in enumerate(groups):
        gid = f"g{gi}"
        scored = [(p, photos[p]) for p in gpaths if p in photos]
        if len(scored) < 2:
            continue
        best_p, best_rec = max(scored, key=lambda x: (x[1].get("score", 0.0),
                                                      x[1].get("technical", {}).get("sharpness", 0.0)))
        for p in gpaths:
            group_id_of[p] = gid
        group_info.append({
            "group_id": gid,
            "members": gpaths,
            "best": best_p,
            "best_score": best_rec.get("score", 0.0),
        })

    picks = []
    picked_paths = set()
    for info in group_info:
        rec = photos[info["best"]]
        if rec.get("score", 0.0) >= _GROUP_PICK_THRESHOLD:
            picks.append({
                "path": info["best"],
                "score": rec.get("score", 0.0),
                "technical": rec.get("technical", {}),
                "aesthetic": rec.get("aesthetic", 0.0),
                "reason": _reason(rec.get("technical", {}), True, len(info["members"])),
                "group": info["group_id"],
                "group_size": len(info["members"]),
            })
            picked_paths.add(info["best"])

    # 孤立高分照片
    for p, rec in photos.items():
        if p in picked_paths or p in group_id_of:
            continue
        s = rec.get("score", 0.0)
        if s >= _PICK_THRESHOLD:
            picks.append({
                "path": p,
                "score": s,
                "technical": rec.get("technical", {}),
                "aesthetic": rec.get("aesthetic", 0.0),
                "reason": _reason(rec.get("technical", {}), False, 1),
                "group": "",
                "group_size": 1,
            })

    picks.sort(key=lambda x: x["score"], reverse=True)
    return {"picks": picks, "groups": group_info, "total": len(picks)}
