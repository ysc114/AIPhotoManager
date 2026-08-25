# core/identity/cluster.py
"""
身份识别与聚合模块 - 聚类层 V4
按 embedding 类型分别执行 DBSCAN 聚类。

职责：只做数学聚类，不直接操作数据库。

P-C4-C2：新增 fursuit_fursee 分支。
    - Fursee 512D L2 归一化 embedding，euclidean 距离
    - eps=0.6481 为 P-C4-C3 定稿生产参数（人眼终审确认，
      对应 cosine_threshold=0.79：L2 归一化下
      eps = sqrt(2*(1-0.79)) = 0.6481）
    - face / fursuit_visual 参数与数据完全不动（D7 冻结）
    - fursuit_fursee 行含同照片多 detection（复合键
      path+detection_index），每行独立参与聚类
"""

import numpy as np
from sklearn.cluster import DBSCAN


class IdentityCluster:
    """身份特征聚类器"""

    def __init__(self, database):
        self.db = database

    def run(self, embedding_type=None):
        if embedding_type is None:
            raise ValueError(
                "禁止无参全量聚类：会重建 face/fursuit_visual/fursuit_fursee "
                "全部角色组并拆散人工合并关系。请显式指定 embedding_type，"
                "或使用 incremental_assign() 增量分配。"
            )
        stats = {}
        if embedding_type == "face":
            stats["face"] = self._cluster_by_type("face", eps=0.4, min_samples=1)
        if embedding_type == "fursuit_visual":
            stats["fursuit_visual"] = self._cluster_by_type("fursuit_visual", eps=0.3, min_samples=1)
        if embedding_type == "fursuit_fursee":
            stats["fursuit_fursee"] = self._cluster_by_type(
                "fursuit_fursee", eps=0.6481, min_samples=1, metric="euclidean"
            )
        return stats

    def incremental_assign(self, embedding_type="fursuit_fursee",
                           threshold=0.79, margin=0.02):
        """增量分配：未分配 detection 只与已有角色组比较，加入或新建。

        Incremental Assignment（P-C4-C5 定稿，2026-08-25）：
        - 绝不对全部数据重跑 DBSCAN；已有行的 group_id **零改动**
          （人工「合并角色」确认的关系永久保留）
        - 只处理 embedding_type 匹配且 group_id='' 的"未分配"行
          （新照片分析写入的新 detection，每行独立判定）
        - 每个新 det：
            max_cos ≥ threshold          → 加入最高相似度已有组
            最高与次高差距 < margin      → 保守不合并（保持未分配，
                                            等待人工确认，避免错误合并）
            全部 < threshold             → 创建新角色组
        - 幂等：重复调用时已分配行不再处理；冲突行保持未分配不重复建组
        - 阈值 0.79 = P-C4-C3 人眼终审（cosine_threshold，与
          DBSCAN eps=0.6481 等价：L2 归一化下 cos = 1 - L2²/2）

        返回 {"joined": int, "created": int,
              "conflicts": [dict], "pending": int}
        """
        records = self.db.get_all_embeddings(
            embedding_type=embedding_type, include_detection_index=True
        )
        # records: (image_path, detection_index, embedding, embedding_type, group_id)
        existing = [r for r in records if r[4]]
        pending = [r for r in records if not r[4]]
        if not pending:
            return {"joined": 0, "created": 0, "conflicts": [], "pending": 0}

        # 已有角色组代表 = 组内成员 embedding 的归一化 centroid
        groups = {}
        for path, det, emb, etype, gid in existing:
            groups.setdefault(gid, []).append(np.asarray(emb, dtype=np.float64))
        representatives = {}
        for gid, vecs in groups.items():
            centroid = np.mean(vecs, axis=0)
            norm = np.linalg.norm(centroid)
            if norm > 0:
                representatives[gid] = centroid / norm

        def _cos(a, b):
            a = np.asarray(a, dtype=np.float64)
            b = np.asarray(b, dtype=np.float64)
            na, nb = np.linalg.norm(a), np.linalg.norm(b)
            if na == 0 or nb == 0:
                return 0.0
            return float(np.dot(a, b) / (na * nb))

        joined = 0
        created = 0
        conflicts = []
        group_type = "real_person" if embedding_type == "face" else "fursuit_character"

        for path, det, emb, etype, gid in pending:
            scores = [
                (g, _cos(emb, rep)) for g, rep in representatives.items()
            ]
            if not scores:
                # 没有任何已有组 → 创建新角色组
                new_gid = self.db.create_group(group_type=group_type)
                self._assign_group(new_gid, path, det, embedding_type)
                created += 1
                continue

            scores.sort(key=lambda x: -x[1])
            top, second = scores[0], scores[1] if len(scores) > 1 else None

            if top[1] >= threshold:
                if second is not None and (top[1] - second[1]) < margin:
                    # 最高/次高几乎并列 → 保守不自动合并，保持未分配待人工
                    conflicts.append({
                        "image_path": path,
                        "detection_index": det,
                        "top_group": top[0],
                        "top_cos": round(top[1], 4),
                        "second_group": second[0],
                        "second_cos": round(second[1], 4),
                    })
                    continue
                self._assign_group(top[0], path, det, embedding_type)
                joined += 1
            else:
                new_gid = self.db.create_group(group_type=group_type)
                self._assign_group(new_gid, path, det, embedding_type)
                created += 1

        return {
            "joined": joined,
            "created": created,
            "conflicts": conflicts,
            "pending": len(pending),
        }

    def _assign_group(self, group_id, image_path, detection_index, embedding_type):
        """仅按复合键精确更新单行 group_id（不动其他行/其他字段）。"""
        self.db.conn.execute(
            "UPDATE identity_image SET group_id = ? "
            "WHERE image_path = ? AND detection_index = ? AND embedding_type = ?",
            (group_id, image_path, detection_index, embedding_type),
        )
        self.db.conn.commit()

    def _cluster_by_type(self, emb_type, eps, min_samples, metric="cosine"):
        # fursuit_fursee 行按复合键 (path, detection_index) 唯一，
        # 需带 detection_index 读回，否则同照片多 detection 会因
        # path 重复而互相覆盖/错位（旧 4 元组按 path 索引不安全）
        include_det = (emb_type == "fursuit_fursee")
        records = self.db.get_all_embeddings(
            embedding_type=emb_type, include_detection_index=include_det
        )
        if include_det:
            valid = [r for r in records if r[2] is not None]
        else:
            valid = [r for r in records if r[1] is not None]
        if not valid:
            print(f"[IdentityCluster] {emb_type} 无有效数据，跳过")
            return 0

        if include_det:
            # (path, det_idx, emb, etype, gid)
            paths = [r[0] for r in valid]
            keys = [(r[0], r[1]) for r in valid]
            embeddings = np.array([r[2] for r in valid])
        else:
            paths = [r[0] for r in valid]
            keys = None
            embeddings = np.array([r[1] for r in valid])

        clustering = DBSCAN(eps=eps, min_samples=min_samples, metric=metric).fit(embeddings)
        labels = clustering.labels_

        group_count = 0

        for label in set(labels):
            if label == -1:
                continue
            indices = np.where(labels == label)[0]
            group_id = self.db.create_group(
                group_type="real_person" if emb_type == "face" else "fursuit_character"
            )
            for idx in indices:
                self._write_back(group_id, emb_type, keys, paths, embeddings, int(idx))
            group_count += 1

        noise_indices = np.where(labels == -1)[0]
        for idx in noise_indices:
            group_id = self.db.create_group(
                group_type="real_person" if emb_type == "face" else "fursuit_character"
            )
            self._write_back(group_id, emb_type, keys, paths, embeddings, int(idx))
            group_count += 1

        self._clean_empty_groups()
        print(f"[IdentityCluster] {emb_type} 聚类完成，共 {group_count} 组")
        return group_count

    def _write_back(self, group_id, emb_type, keys, paths, embeddings, idx):
        """把第 idx 条记录写回其 cluster group（保持 embedding 原值）。

        fursuit_fursee（keys 非 None）按复合键查 bbox 并带
        detection_index 写回，同照片多 detection 各自归组；
        其余类型保持旧 4 元组行为（detection_index=0）。

        P-C4-C2 数据保真修复：回读时一并带出 confidence /
        layer1_category，避免 upsert 把它们覆写为默认值 0.0/''
        （此前旧管线回写后全库 confidence 均被抹成 0.0 的根因）。
        纯元数据透传，不参与聚类数学，聚类结果不变。
        """
        import json
        if keys is not None:
            path, det_idx = keys[idx]
            bbox_row = self.db.conn.execute(
                "SELECT bbox, confidence, layer1_category FROM identity_image"
                " WHERE image_path = ? AND detection_index = ?",
                (path, det_idx)
            ).fetchone()
            bbox = json.loads(bbox_row[0]) if bbox_row and bbox_row[0] else None
            conf = bbox_row[1] if bbox_row else 0.0
            l1 = bbox_row[2] if bbox_row else ""
            self.db.add_image(group_id=group_id, image_path=path,
                              embedding=embeddings[idx], embedding_type=emb_type,
                              bbox=bbox, confidence=conf,
                              layer1_category=l1, detection_index=det_idx)
        else:
            img_path = paths[idx]
            bbox_row = self.db.conn.execute(
                "SELECT bbox, confidence, layer1_category FROM identity_image"
                " WHERE image_path = ?",
                (img_path,)
            ).fetchone()
            bbox = json.loads(bbox_row[0]) if bbox_row and bbox_row[0] else None
            conf = bbox_row[1] if bbox_row else 0.0
            l1 = bbox_row[2] if bbox_row else ""
            self.db.add_image(group_id=group_id, image_path=img_path,
                              embedding=embeddings[idx], embedding_type=emb_type,
                              bbox=bbox, confidence=conf, layer1_category=l1)

    def _clean_empty_groups(self):
        for group in self.db.get_all_groups():
            if self.db.get_group_image_count(group["id"]) == 0:
                self.db.delete_group(group["id"])