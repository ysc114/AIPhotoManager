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
            "detections": [
                {
                    "image_path": "path1.jpg",
                    "detection_index": 0,
                    "bbox": "[x1, y1, x2, y2]",
                    "confidence": 0.98,
                    "embedding_type": "fursuit_fursee",
                },
            ],
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
        # 第三阶段（2026-08-25）：禁止无参全量聚类（会重建全部组并拆散
        # 人工合并）。改为增量分配：新 detection 只加入已有组或新建组，
        # 已有行 group_id 零改动；face 阈值由旧 eps=0.4 换算 cos=0.92。
        self.cluster.incremental_assign(
            embedding_type="fursuit_fursee", threshold=0.79, margin=0.02
        )
        self.cluster.incremental_assign(
            embedding_type="face", threshold=0.92, margin=0.02
        )

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
        """获取角色组列表。

        group_type:
            None              → 全部组（含 Legacy fursuit_visual，仅内部/调试用）
            "fursuit_character" → 只保留 fursuit_fursee 成员（兽装页）
            "real_person"       → 只保留 face 成员（人物页）
            "all"               → 兽装 + 人物（排除 Legacy；角色页）
        """
        db_filter = None if group_type == "all" else group_type
        groups = self.db.get_all_groups(group_type=db_filter)
        result = []
        for group in groups:
            if group is None:
                continue
            images = self.db.get_images_by_group(group["id"])
            if images is None:
                continue
            valid_images = [img for img in images if img is not None]
            if group_type == "fursuit_character":
                valid_images = [
                    img for img in valid_images
                    if img.get("embedding_type") == "fursuit_fursee"
                ]
            elif group_type == "real_person":
                valid_images = [
                    img for img in valid_images
                    if img.get("embedding_type") == "face"
                ]
            elif group_type == "all":
                valid_images = [
                    img for img in valid_images
                    if img.get("embedding_type") in ("fursuit_fursee", "face")
                ]
            image_paths = list(dict.fromkeys(
                img["image_path"] for img in valid_images
            ))
            if not image_paths:
                continue
            detections = [
                {
                    "image_path": img["image_path"],
                    "detection_index": img.get("detection_index", 0),
                    "bbox": img.get("bbox"),
                    "confidence": img.get("confidence", 0.0),
                    "embedding_type": img.get("embedding_type", ""),
                }
                for img in valid_images
            ]
            result.append({
                "character_id": group.get("id", ""),
                "name": group.get("name", ""),
                "type": group.get("type", ""),
                "description": group.get("description", ""),
                "images": image_paths,
                "detections": detections,
                "source_types": sorted(set(
                    img.get("embedding_type", "") for img in valid_images
                    if img.get("embedding_type")
                )),
                "cover_image": (
                    group.get("cover_image")
                    if group.get("cover_image") in image_paths
                    else image_paths[0]
                ),
                "count": len(image_paths),
                "detection_count": len(detections),
            })
        result.sort(key=lambda g: g.get("count", 0), reverse=True)
        return result

    def update_name(self, character_id, name):
        self.db.update_group(character_id, name=name)

    def merge_groups(self, target_id, source_ids):
        """检测级安全合并角色组，保留全部 detection 元数据。"""
        return self.db.merge_group_members(target_id, source_ids)

    def analyze_new_photos(self, photos_dir=None, progress_callback=None):
        """增量分析：扫描 photos/ 中未入库照片，Fursee 入库 + 定向聚类。

        安全设计（P-C4-C4 踩坑后固化）：
        - 只处理 identity_image 中不存在的 image_path（_process_single_image
          内部还有整图 path 查重，双保险；旧 fursuit_visual 冻结不受影响）
        - 聚类使用 incremental_assign（Incremental Assignment）：新 detection
          只与已有角色组逐组比较（cosine ≥ 0.79 加入已有组 / < 0.79 新建组 /
          最高与次高差距 < 0.02 保守不合并），**不重跑 DBSCAN、不拆散任何
          已有组**（人工「合并角色」确认的关系永久保留）
        - 单张失败不中断，计数并继续

        返回 {"scanned": 扫描图片数, "new": 新增处理数,
              "skipped": 已存在数, "failed": 失败数}
        """
        if photos_dir is None:
            photos_dir = os.path.join(os.path.dirname(self.db.db_path), "photos")
        if not os.path.isdir(photos_dir):
            return {"scanned": 0, "new": 0, "skipped": 0, "failed": 0}

        exts = {".jpg", ".jpeg", ".png", ".webp"}
        files = sorted(
            f for f in os.listdir(photos_dir)
            if os.path.splitext(f)[1].lower() in exts
        )
        existing = {
            row[0] for row in self.db.conn.execute(
                "SELECT DISTINCT image_path FROM identity_image"
            )
        }
        new_files = [
            os.path.join(photos_dir, f).replace("\\", "/")
            for f in files
            if os.path.join(photos_dir, f).replace("\\", "/") not in existing
        ]
        # MD5 内容级去重（防再发生）：photos/ 中同一图片的 (1) 副本文件
        # 文件名不同但内容相同——按 path 查重拦不住，会各自入库建组造成
        # "重复角色"。这里对未入库文件计算 MD5，与已入库图片的 MD5 比对，
        # 内容重复的副本直接跳过（不重复分析、不重复建组）。
        if new_files:
            import hashlib
            known_md5 = set()
            for p in existing:
                if os.path.exists(p):
                    try:
                        with open(p, "rb") as fh:
                            known_md5.add(hashlib.md5(fh.read()).hexdigest())
                    except OSError:
                        pass
            kept = []
            for p in new_files:
                try:
                    with open(p, "rb") as fh:
                        m = hashlib.md5(fh.read()).hexdigest()
                    if m in known_md5:
                        continue  # 与已入库图片内容相同 → 副本，跳过
                    kept.append(p)
                except OSError:
                    kept.append(p)  # 读不到按原逻辑处理
            new_files = kept
        total = len(new_files)
        failed = 0
        for i, path in enumerate(new_files, 1):
            try:
                self._process_single_image(path)
            except Exception as e:
                failed += 1
                print(f"[analyze_new_photos] 失败 {os.path.basename(path)}: {e}")
            if progress_callback:
                progress_callback(i, total)
        if total:
            try:
                assign_result = self.cluster.incremental_assign(
                    embedding_type="fursuit_fursee",
                    threshold=0.79,
                    margin=0.02,
                )
                print(f"[analyze_new_photos] 增量分配: "
                      f"加入{assign_result['joined']} 新建{assign_result['created']} "
                      f"冲突{len(assign_result['conflicts'])}")
            except Exception as e:
                print(f"[analyze_new_photos] 增量聚类失败: {e}")
            # Face 增量（与 analyze_paths 对齐）：新人物照片归入已有/新建
            # 人物组；不动 fursuit_visual / fursee 已有组。
            try:
                self.cluster.incremental_assign(
                    embedding_type="face", threshold=0.92, margin=0.02
                )
            except Exception as e:
                print(f"[analyze_new_photos] Face 增量分配失败: {e}")
        return {
            "scanned": len(files),
            "new": total,
            "skipped": len(files) - total,
            "failed": failed,
        }

    def analyze_paths(self, paths, progress_callback=None):
        """增量分析用户选择的文件列表（GUI「添加照片」入口）。

        与 analyze_new_photos 共用同一条安全增量链路：
        - 只处理 identity_image 中不存在的 image_path（path 查重）
        - MD5 内容级去重：与已入库图片 MD5 相同的副本跳过；同一批内
          MD5 相同的文件只处理第一个（批内去重，防同批 (1) 副本）
        - 逐张 _process_single_image：L1 路由（fursuit → Fursee /
          person → face / 其他 → 不写身份库）
        - 尾部 incremental_assign：fursuit_fursee(0.79, 0.02) +
          face(0.92, 0.02)，**不重跑 DBSCAN、不拆散已有组**
        - 兽装绝不走旧 CLIP fursuit_visual 链路（fursuit_visual 冻结）

        progress_callback: cb(current, total, status_str)

        返回 {"scanned", "new", "fursuit", "person", "other",
              "dup_path", "dup_md5", "failed",
              "joined_fursee", "created_fursee",
              "joined_face", "created_face"}
        """
        if not paths:
            return {"scanned": 0, "new": 0, "fursuit": 0, "person": 0,
                    "other": 0, "dup_path": 0, "dup_md5": 0, "failed": 0,
                    "joined_fursee": 0, "created_fursee": 0,
                    "joined_face": 0, "created_face": 0}

        exts = {".jpg", ".jpeg", ".png", ".webp"}
        # 归一化 + 过滤非图片 + path 去重保序
        norm_paths = []
        seen_path = set()
        for raw in paths:
            p = str(raw).replace("\\", "/")
            if os.path.splitext(p)[1].lower() not in exts:
                continue
            if p in seen_path:
                continue
            seen_path.add(p)
            norm_paths.append(p)

        existing = {
            row[0] for row in self.db.conn.execute(
                "SELECT DISTINCT image_path FROM identity_image"
            )
        }

        # 已入库图片的 MD5 集合（一次计算，供内容级去重）
        import hashlib
        known_md5 = set()
        for p in existing:
            if os.path.exists(p):
                try:
                    with open(p, "rb") as fh:
                        known_md5.add(hashlib.md5(fh.read()).hexdigest())
                except OSError:
                    pass

        to_process = []
        dup_path = dup_md5 = 0
        batch_md5 = set()
        for p in norm_paths:
            if p in existing:
                dup_path += 1
                continue
            try:
                with open(p, "rb") as fh:
                    m = hashlib.md5(fh.read()).hexdigest()
            except OSError:
                to_process.append(p)  # 读不到按原逻辑处理
                continue
            if m in known_md5 or m in batch_md5:
                dup_md5 += 1
                continue
            batch_md5.add(m)
            to_process.append(p)

        total = len(to_process)
        n_fursuit = n_person = n_other = failed = 0
        for i, path in enumerate(to_process, 1):
            try:
                # L1 路由统计（与 _process_single_image 内部一致；缓存命中）
                l1_info = self.embedder.get_l1_info(path)
                route = self.embedder.route_l1(l1_info)
                if route == "fursuit":
                    n_fursuit += 1
                elif route == "person":
                    n_person += 1
                else:
                    n_other += 1
                self._process_single_image(path)
            except Exception as e:
                failed += 1
                print(f"[analyze_paths] 失败 {os.path.basename(path)}: {e}")
            if progress_callback:
                progress_callback(i, total, os.path.basename(path))

        joined_fursee = created_fursee = 0
        joined_face = created_face = 0
        if total:
            try:
                r = self.cluster.incremental_assign(
                    embedding_type="fursuit_fursee", threshold=0.79, margin=0.02
                )
                joined_fursee = r.get("joined", 0)
                created_fursee = r.get("created", 0)
            except Exception as e:
                print(f"[analyze_paths] Fursee 增量分配失败: {e}")
            try:
                r = self.cluster.incremental_assign(
                    embedding_type="face", threshold=0.92, margin=0.02
                )
                joined_face = r.get("joined", 0)
                created_face = r.get("created", 0)
            except Exception as e:
                print(f"[analyze_paths] Face 增量分配失败: {e}")

        return {
            "scanned": len(norm_paths),
            "new": total,
            "fursuit": n_fursuit,
            "person": n_person,
            "other": n_other,
            "dup_path": dup_path,
            "dup_md5": dup_md5,
            "failed": failed,
            "joined_fursee": joined_fursee,
            "created_fursee": created_fursee,
            "joined_face": joined_face,
            "created_face": created_face,
        }

    def close(self):
        # 共享只读 reader：进程级复用，close() 为 no-op（由 get_reader 持有）
        if getattr(self, "_shared_reader", False):
            return
        # 关闭 Fursee worker（若已启动）；失败不阻断数据库关闭
        if self._fursee_adapter is not None:
            try:
                self._fursee_adapter.shutdown()
            except Exception as e:
                print(f"[IdentityManager] Fursee worker 关闭失败：{e}")
        self.db.close()


_READER = None


def get_reader():
    """进程级共享只读 IdentityManager（主线程 UI 只读路径用）。

    - 惰性创建，进程生命周期复用；close() 对共享实例为 no-op
      （调用方可沿用 try/finally mgr.close() 模式）
    - 仅限**主线程只读**用途（get_groups / list_favorites 等 SELECT）
    - 写操作（重命名/合并/收藏增删/分析入库）必须使用新建实例，
      避免共享连接跨线程与事务污染
    """
    global _READER
    if _READER is None:
        r = IdentityManager()
        r._shared_reader = True
        _READER = r
    return _READER
