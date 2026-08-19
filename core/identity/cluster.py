# core/identity/cluster.py
"""
身份识别与聚合模块 - 聚类层 V4
按 embedding 类型分别执行 DBSCAN 聚类。

职责：只做数学聚类，不直接操作数据库。
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
        return stats

    def _cluster_by_type(self, emb_type, eps, min_samples):
        records = self.db.get_all_embeddings(embedding_type=emb_type)
        valid = [r for r in records if r[1] is not None]
        if not valid:
            print(f"[IdentityCluster] {emb_type} 无有效数据，跳过")
            return 0

        paths = [r[0] for r in valid]
        embeddings = np.array([r[1] for r in valid])

        clustering = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine").fit(embeddings)
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
                img_path = paths[idx]
                emb = embeddings[idx]
                bbox_row = self.db.conn.execute(
                    "SELECT bbox FROM identity_image WHERE image_path = ?", (img_path,)
                ).fetchone()
                import json
                bbox = json.loads(bbox_row[0]) if bbox_row and bbox_row[0] else None
                self.db.add_image(group_id=group_id, image_path=img_path,
                                  embedding=emb, embedding_type=emb_type, bbox=bbox)
            group_count += 1

        noise_indices = np.where(labels == -1)[0]
        for idx in noise_indices:
            group_id = self.db.create_group(
                group_type="real_person" if emb_type == "face" else "fursuit_character"
            )
            img_path = paths[idx]
            emb = embeddings[idx]
            bbox_row = self.db.conn.execute(
                "SELECT bbox FROM identity_image WHERE image_path = ?", (img_path,)
            ).fetchone()
            import json
            bbox = json.loads(bbox_row[0]) if bbox_row and bbox_row[0] else None
            self.db.add_image(group_id=group_id, image_path=img_path,
                              embedding=emb, embedding_type=emb_type, bbox=bbox)
            group_count += 1

        self._clean_empty_groups()
        print(f"[IdentityCluster] {emb_type} 聚类完成，共 {group_count} 组")
        return group_count

    def _clean_empty_groups(self):
        for group in self.db.get_all_groups():
            if self.db.get_group_image_count(group["id"]) == 0:
                self.db.delete_group(group["id"])