# core/ai_classifier.py
"""
AI 分类器，集成 YOLO 检测 → 裁剪 → CLIP 三级分类流程。
分析结果自动缓存，后续读取直接命中。
"""

from core.analysis_cache import get_cache


class AIClassifier:

    def __init__(self, use_yolo=True, yolo_device=None):
        self.use_yolo = use_yolo
        self.yolo_device = yolo_device

        self.model = None
        self.yolo_detector = None
        self.crop_func = None
        self.cache = get_cache()

    def _ensure_model(self):
        if self.model is None:
            # Stage 4B: CLIP 经 ModelHub 共享（懒加载、统一 device）
            from core.model_hub import get_model_hub
            self.model = get_model_hub().get_clip()

    def _ensure_yolo(self):
        if self.yolo_detector is None and self.use_yolo:
            try:
                # Stage 4B: YOLO 经 ModelHub 共享（懒加载、classes 已预设、
                # 统一 device），不再各自创建 YOLODetector 实例
                from core.model_hub import get_model_hub
                from core.yolo_detector import crop_from_bbox
                self.yolo_detector = get_model_hub().get_yolo()
                self.crop_func = crop_from_bbox
                print(f"[YOLO] 加载成功 (YOLO-World, ModelHub 共享)")
            except ImportError as e:
                print(f"[YOLO] 不可用: {e}")
                self.use_yolo = False

    def analyze(self, photo):

        if isinstance(photo, str):
            image_path = photo
        elif hasattr(photo, "path"):
            image_path = photo.path
        else:
            raise TypeError("analyze expects a file path string or an object with a .path attribute")

        # 查缓存（2026-08-25 修复：空 dict {} 为历史遗留脏数据，视为
        # 无效缓存——若命中空值会短路真实分析，导致该照片永远无法重新
        # L1 分类入库。仅非空有效结果才命中；None / {} 均重新分析。）
        cached = self.cache.get(image_path)
        if cached:
            print(f"[缓存] 命中: {image_path}")
            return {
                "category": cached.get("category"),
                "quality": cached.get("quality"),
                "scores": cached.get("scores"),
                "layer1": cached.get("layer1"),
                "layer2": cached.get("layer2"),
                "layer3": cached.get("layer3"),
            }

        self._ensure_model()
        self._ensure_yolo()

        from PIL import Image
        image = Image.open(image_path).convert("RGB")

        persons = []

        if self.use_yolo and self.yolo_detector is not None:
            # Stage 4B: 检测走 ModelHub 检测缓存（同一图片 IdentityEmbedding
            # 后续读取时直接命中，不再重复推理）；threshold=0.15 由
            # ModelHub 统一提供，与原两处调用保持一致
            from core.model_hub import get_model_hub
            persons = get_model_hub().get_detections(image_path, image=image)

        if persons:
            best_bbox = persons[0].bbox
            print(f"[YOLO] 检测到 {len(persons)} 个目标: {[p.class_name for p in persons]}")
        else:
            print("[YOLO] 未检测到目标，使用全图")
            result = self.model.analyze(image)
            self.cache.set(image_path, result)
            return {
                "category": result.get("category"),
                "quality": result.get("quality"),
                "scores": result.get("scores"),
                "layer1": result.get("layer1"),
                "layer2": result.get("layer2"),
                "layer3": result.get("layer3"),
            }

        # 第一次：标准裁剪
        cropped = self.crop_func(image, best_bbox, padding=0.1)
        result = self.model.analyze(cropped)

        # L2 不确定时扩大裁剪重试
        l2 = result.get("layer2")
        if l2 is not None:
            confidence = l2.get("confidence", 0)
            scores = l2.get("scores", {})

            if scores:
                sorted_scores = sorted(scores.values(), reverse=True)
                gap = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) >= 2 else 1.0

                if confidence < 0.40 or gap < 0.08:
                    print(f"[重试] L2 不确定 (conf={confidence:.2f}, gap={gap:.2f})，扩大裁剪")

                    cropped_wide = self.crop_func(image, best_bbox, padding=0.35)
                    result_wide = self.model.analyze(cropped_wide)

                    l2_wide = result_wide.get("layer2")
                    if l2_wide and l2_wide.get("confidence", 0) > confidence:
                        print(f"[重试] 置信度提升: {confidence:.2f} → {l2_wide['confidence']:.2f}")
                        result = result_wide

        # 存入缓存
        self.cache.set(image_path, result)

        return {
            "category": result.get("category"),
            "quality": result.get("quality"),
            "scores": result.get("scores"),
            "layer1": result.get("layer1"),
            "layer2": result.get("layer2"),
            "layer3": result.get("layer3"),
        }

    def classify(self, photo):
        return self.analyze(photo)