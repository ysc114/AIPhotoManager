# core/identity/manager.py
"""
身份识别与聚合模块 - 统一入口 V4
串联全流程：分类判断 → 特征提取 → 存储 → 聚类 → 返回分组。

使用方式：
    from core.identity import IdentityManager
    manager = IdentityManager()
    groups = manager.analyze_folder(image_paths)

返回格式：
    [
        {
            "character_id": "abc123",
            "name": "",
            "type": "fursuit_character",
            "images": ["path1.jpg", "path2.jpg"],
            "cover_image": "path1.jpg",
            "count": 2,
        },
        ...
    ]
"""

import os
import numpy as np

from core.identity.database import IdentityDatabase
from core.identity.embedding import IdentityEmbedding
from core.identity.cluster import IdentityCluster


class IdentityManager:
    """身份识别与聚合管理器"""

    #: D9：Fursee worker 连续失败熔断阈值（次）
    FURSEE_FAIL_LIMIT = 3

    def __init__(self, db_path=None, fursee_adapter=None):
        self.db = IdentityDatabase(db_path)
        self.embedder = IdentityEmbedding()
        self.cluster = IdentityCluster(self.db)

        # Fursee 路由（P-C4-C1/S2）：
        #   - 默认 None → 懒加载：首次处理兽装图片时才创建并启动
        #     FurseeAdapter（worker boot + 模型加载约 13s，不应在
        #     import / 构造 / 处理普通图片时发生）
        #   - 可注入实例（测试用 fake adapter / 未来自定义配置）
        self._fursee_adapter = fursee_adapter
        self._fursee_failures = 0      # 连续失败计数（成功一次即清零）
        self._fursee_fuse_open = False # 熔断状态（批作用域，见 analyze_folder）

    # ============================================================
    # 主流程
    # ============================================================

    def analyze_folder(self, image_paths, progress_callback=None):
        """
        批量分析图片，提取身份特征并聚类。

        参数:
            image_paths: 图片路径列表
            progress_callback: callback(current, total, status)

        返回:
            list[dict]: 身份分组列表
        """
        total = len(image_paths)

        # D9：Fursee 熔断按"批"作用域——每批开始时重新计数，
        # 上一批的熔断不会让后续批次永久失效
        self._fursee_failures = 0
        self._fursee_fuse_open = False

        for idx, path in enumerate(image_paths):
            if progress_callback:
                progress_callback(idx + 1, total, f"提取特征：{os.path.basename(path)}")
            self._process_single_image(path)

        if progress_callback:
            progress_callback(total, total, "正在聚类...")
        self.cluster.run()

        groups = self.get_groups()
        if progress_callback:
            progress_callback(total, total, f"完成：{len(groups)} 个分组")
        return groups

    def _process_single_image(self, image_path):
        """处理单张图片：L1 路由 → 提取特征 → 存入数据库。

        路由（P-C4-C1/S2）：
            - L1=兽装   → FurseeAdapter（embedding_type=fursuit_fursee，
                          512D；一图多 detection 时每个 detection 独立
                          detection_index，各自一行）
            - L1=普通人物 → InsightFace（embedding_type=face，行为与
                          旧版完全一致，detection_index=0）
            - 其他      → 不写 identity embedding（保持原有行为）
        """
        path = image_path.replace("\\", "/")
        # 整图 path 查重：一张照片只走一条 L1 路由、只写一次。
        # 由此旧 29 张 fursuit_visual（CLIP 768D，D7 永久冻结）不会被
        # Fursee 路由重新处理或覆盖。
        existing = self.db.conn.execute(
            "SELECT id FROM identity_image WHERE image_path = ?", (path,)
        ).fetchone()
        if existing:
            return

        l1_info = self.embedder.get_l1_info(image_path)
        route = self.embedder.route_l1(l1_info)
        if route is None:
            return

        if route == "fursuit":
            self._process_fursuit_fursee(image_path, path, l1_info)
        else:
            self._process_face(image_path, path)

    def _process_face(self, image_path, path):
        """普通人物路径：原有 InsightFace 行为（detection_index=0）。

        写库从旧版裸 INSERT OR REPLACE 改为 db.add_image()
        复合键 upsert —— 因上方已做整图 path 查重，此处必然走 INSERT，
        落库结果与旧版逐字段一致（embedding float32 tobytes 相同）。
        """
        result = self.embedder.extract(image_path)
        if result is None or result.get("embedding") is None:
            return

        emb_type = result.get("embedding_type")
        if emb_type not in ("face", "fursuit_visual"):
            return

        self.db.add_image(
            group_id="",
            image_path=path,
            embedding=result["embedding"],
            embedding_type=emb_type,
            bbox=result.get("bbox"),
            layer1_category=result.get("layer1_category", ""),
            confidence=result.get("confidence", 0),
            detection_index=0,
        )

    def _process_fursuit_fursee(self, image_path, path, l1_info):
        """兽装路径：FurseeAdapter 逐图分析，每个 detection 独立一行。

        D6（Fursee 不可用时的行为）：
            - 不回退 CLIP、不写 fursuit_visual、不写伪造 embedding
            - 当前图片跳过 identity 写入，记录日志
            - 连续失败达到 FURSEE_FAIL_LIMIT（D9=3）次后熔断，
              本批剩余兽装图片直接跳过（不再尝试）
            - 单张失败不影响其他图片继续处理
        Adapter 自身已具备"崩溃 → 单次 restart + 单次重试"机制，
        本方法不再额外实现重启循环。
        """
        from core.identity.fursee_adapter import FurseeError

        if self._fursee_fuse_open:
            print(f"[IdentityManager] Fursee 已熔断，跳过兽装图片：{path}")
            return

        try:
            adapter = self._get_fursee_adapter()
            resp = adapter.analyze(image_path)
        except FurseeError as e:
            self._fursee_failures += 1
            print(f"[IdentityManager] Fursee 分析失败（{self._fursee_failures}/"
                  f"{self.FURSEE_FAIL_LIMIT}），跳过 {path}：{type(e).__name__}: {e}")
            if self._fursee_failures >= self.FURSEE_FAIL_LIMIT:
                self._fursee_fuse_open = True
                print("[IdentityManager] Fursee 连续失败达到阈值，熔断：本批剩余"
                      "兽装图片跳过 Fursee 身份提取（不回退 CLIP）")
            return

        self._fursee_failures = 0  # 成功一次即清零连续失败计数

        detections = resp.get("detections") or []
        if not detections:
            return

        label_cn = l1_info.get("label_cn", "")
        for det_index, det in enumerate(detections):
            embedding = np.asarray(det.get("embedding"), dtype=np.float32)
            self.db.add_image(
                group_id="",
                image_path=path,
                embedding=embedding,
                embedding_type="fursuit_fursee",
                bbox=det.get("bbox"),
                layer1_category=label_cn,
                confidence=det.get("confidence", 0.0),
                detection_index=det_index,
            )

    def _get_fursee_adapter(self):
        """懒加载并确保 FurseeAdapter 就绪（幂等）。

        首次处理兽装图片时才创建 Adapter 并启动 worker（约 13s）；
        import / 构造 IdentityManager / 处理普通图片均不会触发。
        启动失败抛 FurseeStartupError，由调用方按 D6/D9 计数熔断。
        """
        if self._fursee_adapter is None:
            from core.identity.fursee_adapter import FurseeAdapter
            self._fursee_adapter = FurseeAdapter()
        adapter = self._fursee_adapter
        # 幂等启动：未 ready 才 start（注入的 fake 可自带 state='ready' 跳过）
        if getattr(adapter, "state", "ready") != "ready":
            adapter.start()
        return adapter

    # ============================================================
    # 查询接口
    # ============================================================

    def get_groups(self, group_type=None):
        groups = self.db.get_all_groups(group_type=group_type)
        result = []
        for group in groups:
            if group is None:
                continue
            images = self.db.get_images_by_group(group["id"])
            if images is None:
                continue
            image_paths = [img["image_path"] for img in images if img is not None]
            if not image_paths:
                continue
            result.append({
                "character_id": group.get("id", ""),
                "name": group.get("name", ""),
                "type": group.get("type", ""),
                "description": group.get("description", ""),
                "images": image_paths,
                "cover_image": group.get("cover_image") or image_paths[0],
                "count": len(image_paths),
            })
        result.sort(key=lambda g: g.get("count", 0), reverse=True)
        return result

    def update_name(self, character_id, name):
        self.db.update_group(character_id, name=name)

    def merge_groups(self, target_id, source_ids):
        for source_id in source_ids:
            images = self.db.get_images_by_group(source_id)
            for img in images:
                self.db.add_image(
                    group_id=target_id,
                    image_path=img["image_path"],
                    embedding_type=img.get("embedding_type", ""),
                )
            self.db.delete_group(source_id)
        cover = self.db.get_group_cover(target_id)
        if cover:
            self.db.update_group(target_id, cover_image=cover)

    def close(self):
        # 关闭 Fursee worker（若已启动）；失败不阻断数据库关闭
        if self._fursee_adapter is not None:
            try:
                self._fursee_adapter.shutdown()
            except Exception as e:
                print(f"[IdentityManager] Fursee worker 关闭失败：{e}")
        self.db.close()