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
        stats = {}
        if embedding_type is None or embedding_type == "face":
            stats["face"] = self._cluster_by_type("face", eps=0.4, min_samples=1)
        if embedding_type is None or embedding_type == "fursuit_visual":
            stats["fursuit_visual"] = self._cluster_by_type("fursuit_visual", eps=0.3, min_samples=1)
        if embedding_type is None or embedding_type == "fursuit_fursee":
            stats["fursuit_fursee"] = self._cluster_by_type(
                "fursuit_fursee", eps=0.6481, min_samples=1, metric="euclidean"
            )
        return stats

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