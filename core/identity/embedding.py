# core/identity/embedding.py
"""
身份识别与聚合模块 - 特征提取层 V4
根据 L1 分类结果自动路由：
    - 普通人物 → InsightFace 人脸 embedding
    - 兽装人物 → CLIP 视觉 embedding
    - 其他 → 跳过
"""

import numpy as np
from PIL import Image


class IdentityEmbedding:
    """双路身份特征提取器"""

    def __init__(self):
        self._face_model = None
        self._clip_model = None
        # Stage 4B: _yolo_detector 已删除（YOLO 经 ModelHub 共享 +
        # 检测缓存复用 AIClassifier 的结果）；_crop_func 保留，
        # _extract_face / _extract_fursuit_visual 仍在使用
        self._crop_func = None
        self._classifier = None

    # ============================================================
    # 模型加载
    # ============================================================

    def _ensure_face_model(self):
        if self._face_model is None:
            # Stage 4B: InsightFace 经 ModelHub 共享（懒加载、统一 device、
            # providers 按 CUDA 可用性自动选择）。不可用时返回 None，
            # 与原降级策略一致（原代码置 False，这里统一用 None 标记）
            from core.model_hub import get_model_hub
            self._face_model = get_model_hub().get_insightface()
            if self._face_model is None:
                print("[IdentityEmbedding] InsightFace 不可用（ModelHub 返回 None）")

    def _ensure_clip_model(self):
        if self._clip_model is None:
            # Stage 4B: CLIP 经 ModelHub 共享（与 AIClassifier 同一实例）
            from core.model_hub import get_model_hub
            self._clip_model = get_model_hub().get_clip()

    def _ensure_yolo(self):
        """Stage 4B: 仅初始化裁剪函数。

        YOLO 检测本身改走 ModelHub.get_detections()（共享 YOLO 实例 +
        内存检测缓存），本类不再持有独立的 YOLODetector。
        """
        if self._crop_func is None:
            from core.yolo_detector import crop_from_bbox
            self._crop_func = crop_from_bbox

    def _ensure_classifier(self):
        if self._classifier is None:
            from core.ai_classifier import AIClassifier
            self._classifier = AIClassifier()

    # ============================================================
    # L1 路由（P-C4-C1/S2 从 extract() 抽出的纯重构）
    # ============================================================

    def get_l1_info(self, image_path):
        """读取单张图片的 L1 分类信息（缓存优先，未命中走 AIClassifier）。

        P-C4-C1/S2 从 extract() 抽出的纯重构：供 IdentityManager 在
        选择 InsightFace / FurseeAdapter 路由之前做判断。
        AIClassifier.analyze() 会回写 analysis_cache，因此先调用本方法
        再调用 extract() 不会重复分类（第二次读取命中缓存）。

        返回:
            dict: {"category": str, "label_cn": str, "confidence": float}
        """
        self._ensure_classifier()

        from core.analysis_cache import get_cache
        cache = get_cache()
        cached = cache.get(image_path)
        if cached:
            return {
                "category": cached.get("category", ""),
                "label_cn": cached.get("_category_cn") or "",
                "confidence": cached.get("quality", 0),
            }
        result = self._classifier.analyze(image_path)
        l1 = result.get("layer1") or {}
        return {
            "category": l1.get("category", ""),
            "label_cn": l1.get("label_cn", ""),
            "confidence": l1.get("confidence", 0),
        }

    @staticmethod
    def route_l1(l1_info):
        """根据 L1 分类信息返回身份提取路由。

        返回:
            "fursuit" | "person" | None

        判定条件与原 extract() 内部逻辑逐条一致（两者同时命中时
        person 优先——与原代码 `if is_real_person → face` 的顺序相同）。
        """
        category = str((l1_info or {}).get("category", "")).lower()
        label_cn = str((l1_info or {}).get("label_cn", ""))
        if "normal person" in category or "普通人物" in label_cn:
            return "person"
        if "fursuit" in category or "兽装" in label_cn:
            return "fursuit"
        return None

    # ============================================================
    # 特征提取
    # ============================================================

    def extract(self, image_path):
        """
        提取单张图片的身份特征。

        返回:
            dict: {
                "embedding": numpy array or None,
                "embedding_type": "face" / "fursuit_visual" / None,
                "bbox": [x1, y1, x2, y2] or None,
                "layer1_category": str,
                "confidence": float,
            }
        """
        self._ensure_classifier()
        self._ensure_yolo()

        # 优先从缓存读 L1 分类（P-C4-C1/S2：改用 get_l1_info，行为不变）
        l1_info = self.get_l1_info(image_path)
        l1_label_cn = l1_info["label_cn"]
        l1_confidence = l1_info["confidence"]
        route = self.route_l1(l1_info)

        if route is None:
            return {
                "embedding": None,
                "embedding_type": None,
                "bbox": None,
                "layer1_category": l1_label_cn,
                "confidence": l1_confidence,
            }

        image = Image.open(image_path).convert("RGB")

        # Stage 4B: 检测走 ModelHub 检测缓存 —— 若 AIClassifier 刚检测过
        # 同一张图片（organize_folder 正常流程），此处直接命中缓存，
        # 不再重复 YOLO 推理；缓存未命中时由 ModelHub 用共享 YOLO 检测
        from core.model_hub import get_model_hub
        persons = get_model_hub().get_detections(image_path, image=image)

        if not persons:
            return {
                "embedding": None,
                "embedding_type": None,
                "bbox": None,
                "layer1_category": l1_label_cn,
                "confidence": l1_confidence,
            }

        best_bbox = persons[0].bbox

        if route == "person":
            return self._extract_face(image, best_bbox, l1_label_cn, l1_confidence)
        else:
            return self._extract_fursuit_visual(image, best_bbox, l1_label_cn, l1_confidence)

    def _extract_face(self, image, bbox, label_cn, confidence):
        self._ensure_face_model()
        # Stage 4B: ModelHub 不可用时返回 None（原 False 哨兵已废弃）
        if self._face_model is None or self._face_model is False:
            return self._empty_result("face", bbox, label_cn, confidence)

        person_crop = self._crop_func(image, bbox, padding=0.0)
        person_np = np.array(person_crop)

        try:
            faces = self._face_model.get(person_np)
        except Exception as e:
            print(f"[IdentityEmbedding] 人脸检测失败: {e}")
            return self._empty_result("face", bbox, label_cn, confidence)

        if not faces:
            return self._empty_result("face", bbox, label_cn, confidence)

        best_face = max(faces, key=lambda f:
            (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))

        return {
            "embedding": best_face.embedding.astype(np.float32),
            "embedding_type": "face",
            "bbox": bbox,
            "layer1_category": label_cn,
            "confidence": confidence,
        }

    def _extract_fursuit_visual(self, image, bbox, label_cn, confidence):
        self._ensure_clip_model()
        person_crop = self._crop_func(image, bbox, padding=0.1)
        # Stage 4B: 改用公开方法 encode_image()（不再调用私有 _encode_image）
        image_features = self._clip_model.encode_image(person_crop)
        embedding = image_features.cpu().numpy().flatten().astype(np.float32)

        return {
            "embedding": embedding,
            "embedding_type": "fursuit_visual",
            "bbox": bbox,
            "layer1_category": label_cn,
            "confidence": confidence,
        }

    def _empty_result(self, emb_type, bbox, label_cn, confidence):
        return {
            "embedding": None,
            "embedding_type": emb_type,
            "bbox": bbox,
            "layer1_category": label_cn,
            "confidence": confidence,
        }

    def extract_batch(self, image_paths, progress_callback=None):
        results = []
        total = len(image_paths)
        for idx, path in enumerate(image_paths):
            if progress_callback:
                progress_callback(idx + 1, total, f"提取特征：{path}")
            try:
                r = self.extract(path)
                r["image_path"] = path
                results.append(r)
            except Exception as e:
                print(f"[IdentityEmbedding] 提取失败 {path}: {e}")
                results.append({
                    "image_path": path,
                    "embedding": None,
                    "embedding_type": None,
                    "bbox": None,
                    "layer1_category": "",
                    "confidence": 0,
                })
        return results