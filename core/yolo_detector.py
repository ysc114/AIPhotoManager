# core/yolo_detector.py
"""
YOLO 检测模块 V3.2
支持 YOLO-World 零样本检测，可自定义检测类别。

使用方式：
    detector = YOLODetector()
    detector.set_classes(["fursuit", "furry character", "person"])
    detections = detector.detect(image)
"""

from abc import ABC, abstractmethod
import numpy as np
from PIL import Image


class Detection:
    """单条检测结果"""

    def __init__(self, class_name, confidence, bbox):
        self.class_name = class_name
        self.confidence = confidence
        self.bbox = bbox

    def __repr__(self):
        return f"Detection({self.class_name}, {self.confidence:.2f}, {self.bbox})"


class BaseYOLODetector(ABC):
    """YOLO 检测器抽象基类"""

    @abstractmethod
    def detect(self, image):
        pass

    @abstractmethod
    def detect_persons(self, image, confidence_threshold=0.3):
        pass


class YOLOv11Detector(BaseYOLODetector):
    """Ultralytics YOLOv11（保留，用于通用检测）"""

    def __init__(self, model_path="yolo11n.pt", device=None):
        self.model_path = model_path
        self.device = device
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from ultralytics import YOLO
            self._model = YOLO(self.model_path)
            if self.device is not None:
                self._model.to(self.device)

    def detect(self, image):
        self._ensure_model()

        if isinstance(image, Image.Image):
            image_np = np.array(image)
        else:
            image_np = image

        results = self._model(image_np, verbose=False)

        detections = []
        for result in results:
            if result.boxes is None:
                continue
            boxes = result.boxes.xyxyn.cpu().tolist()
            confs = result.boxes.conf.cpu().tolist()
            clss  = result.boxes.cls.cpu().tolist()

            for box, conf, cls_id in zip(boxes, confs, clss):
                class_name = self._model.names[int(cls_id)]
                detections.append(
                    Detection(class_name=class_name, confidence=round(float(conf), 4),
                              bbox=tuple(round(float(v), 4) for v in box))
                )
        return detections

    def detect_persons(self, image, confidence_threshold=0.3):
        all_detections = self.detect(image)
        persons = [d for d in all_detections
                   if d.class_name == "person" and d.confidence >= confidence_threshold]
        persons.sort(key=lambda d: d.confidence, reverse=True)
        return persons


class YOLOWorldDetector(BaseYOLODetector):
    """YOLO-World 零样本检测器，支持自定义类别"""

    def __init__(self, model_path="yolov8s-world.pt", device=None):
        self.model_path = model_path
        self.device = device
        self._model = None
        self._custom_classes = None

    def set_classes(self, classes):
        """
        设置自定义检测类别。

        参数:
            classes: list[str] 如 ["fursuit", "furry character", "person", "face"]
        """
        self._custom_classes = classes
        if self._model is not None:
            self._model.set_classes(classes)

    def _ensure_model(self):
        if self._model is None:
            from ultralytics import YOLOWorld
            self._model = YOLOWorld(self.model_path)
            if self.device is not None:
                self._model.to(self.device)
            if self._custom_classes is not None:
                self._model.set_classes(self._custom_classes)

    def detect(self, image):
        self._ensure_model()

        if isinstance(image, Image.Image):
            image_np = np.array(image)
        else:
            image_np = image

        results = self._model(image_np, verbose=False)

        detections = []
        for result in results:
            if result.boxes is None:
                continue
            boxes = result.boxes.xyxyn.cpu().tolist()
            confs = result.boxes.conf.cpu().tolist()
            clss  = result.boxes.cls.cpu().tolist()

            for box, conf, cls_id in zip(boxes, confs, clss):
                class_name = self._model.names[int(cls_id)]
                detections.append(
                    Detection(class_name=class_name, confidence=round(float(conf), 4),
                              bbox=tuple(round(float(v), 4) for v in box))
                )
        return detections

    def detect_persons(self, image, confidence_threshold=0.3):
        """检测 fursuit / person / furry character 等"""
        all_detections = self.detect(image)

        target_classes = {"fursuit", "furry character", "person",
                          "anthropomorphic animal", "cartoon character", "furry"}
        if self._custom_classes:
            target_classes = set(self._custom_classes)

        hits = [d for d in all_detections
                if d.class_name in target_classes and d.confidence >= confidence_threshold]
        hits.sort(key=lambda d: d.confidence, reverse=True)
        return hits


class YOLODetector:
    """
    YOLO 检测器统一入口 V3.2。
    默认使用 YOLO-World，支持自定义类别。
    """

    def __init__(self, model_version="world", device=None):
        self.model_version = model_version
        self.device = device
        self._detector = None
        self._classes = None

    def set_classes(self, classes):
        self._classes = classes
        if self._detector is not None and hasattr(self._detector, "set_classes"):
            self._detector.set_classes(classes)

    def _ensure_detector(self):
        if self._detector is None:
            if self.model_version == "world":
                self._detector = YOLOWorldDetector(
                    model_path="yolov8s-world.pt",
                    device=self.device,
                )
                if self._classes is not None:
                    self._detector.set_classes(self._classes)
            elif self.model_version == "v11":
                self._detector = YOLOv11Detector(device=self.device)
            else:
                raise ValueError(f"不支持的 YOLO 版本：{self.model_version}")

    def detect_persons(self, image, confidence_threshold=0.3):
        self._ensure_detector()
        return self._detector.detect_persons(image, confidence_threshold)

    def detect(self, image):
        self._ensure_detector()
        return self._detector.detect(image)


def crop_from_bbox(image, bbox, padding=0.0):
    w, h = image.size
    x1, y1, x2, y2 = bbox[0] * w, bbox[1] * h, bbox[2] * w, bbox[3] * h
    bw, bh = (x2 - x1) * padding, (y2 - y1) * padding
    x1, y1 = max(0, x1 - bw), max(0, y1 - bh)
    x2, y2 = min(w, x2 + bw), min(h, y2 + bh)
    return image.crop((int(x1), int(y1), int(x2), int(y2)))