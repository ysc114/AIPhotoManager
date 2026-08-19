# core/clip_model.py

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import torch
import open_clip
from PIL import Image

from config.labels import (
    L1_LABELS_EN, L1_LABEL_MAP,
    L2_LABELS_EN, L2_LABEL_MAP,
    L3_LABELS_EN, L3_LABEL_MAP,
    L1_FURSUIT_LABEL,
)


class CLIPModel:

    def __init__(self, device=None):
        # Stage 4B: 可选 device 参数（由 ModelHub 统一传入）；
        # 不传时保持原行为（自动检测），向后兼容。
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device

        print(f"CLIP运行设备：{self.device}")

        model_name = "ViT-L-14"
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            model_name,
            pretrained="datacomp_xl_s13b_b90k"
        )

        self.model.to(self.device)
        self.model.eval()

        self.tokenizer = open_clip.get_tokenizer("ViT-L-14")

        # 预编码三层标签文本（只执行一次）
        self.text_features_L1 = self._encode_labels(L1_LABELS_EN)
        self.text_features_L2 = self._encode_labels(L2_LABELS_EN)
        self.text_features_L3 = self._encode_labels(L3_LABELS_EN)

    # ============================================================
    # 内部方法
    # ============================================================

    def _encode_labels(self, labels):
        """编码文本标签，返回归一化特征向量"""
        text = self.tokenizer(labels)
        with torch.no_grad():
            features = self.model.encode_text(text.to(self.device))
            features /= features.norm(dim=-1, keepdim=True)
        return features

    def _encode_image(self, image):
        """编码图像，返回归一化特征向量。支持文件路径或 PIL.Image"""
        if isinstance(image, str):
            image = Image.open(image).convert("RGB")
        elif isinstance(image, Image.Image):
            image = image.convert("RGB")

        image = self.preprocess(image).unsqueeze(0).to(self.device)

        with torch.no_grad():
            features = self.model.encode_image(image)
            features /= features.norm(dim=-1, keepdim=True)
        return features

    def _classify(self, image_features, text_features, label_list, label_map):
        """
        通用分类方法。
        参数:
            image_features: 图像特征向量
            text_features:  文本特征矩阵
            label_list:     英文标签列表
            label_map:      英文→中文映射
        返回:
            dict: {"category", "label_cn", "confidence", "scores"}
        """
        with torch.no_grad():
            similarity = image_features @ text_features.T
            similarity = torch.sigmoid(similarity * 5.0)
            similarity = similarity / similarity.sum(dim=-1, keepdim=True)

        scores = similarity[0].cpu().tolist()

        result_scores = {}
        for label, score in zip(label_list, scores):
            result_scores[label] = round(float(score), 4)

        best = max(result_scores, key=result_scores.get)

        return {
            "category": best,
            "label_cn": label_map.get(best, best),
            "confidence": result_scores[best],
            "scores": result_scores,
        }

    # ============================================================
    # 对外接口（保持不变）
    # ============================================================

    def encode_image(self, image):
        """公开图像编码接口（Stage 4B）。

        委托给 _encode_image()，供 IdentityEmbedding 等外部模块调用，
        消除对私有方法的依赖。返回 Tensor（在 self.device 上，调用方
        自行 .cpu()）。
        """
        return self._encode_image(image)

    def analyze(self, image_input):
        """
        三级分类分析。
        图像特征只编码一次，三层共享。

        参数:
            image_input: 文件路径 (str) 或 PIL.Image 对象

        返回:
            dict: {
                "category": str,       # 最终分类（兼容旧接口）
                "quality": float,       # 置信度（兼容旧接口）
                "scores": dict,         # 概率分布（兼容旧接口）
                "layer1": dict,         # L1 主体识别（新增）
                "layer2": dict|None,    # L2 兽装物种（新增，可能为 None）
                "layer3": dict,         # L3 照片类型（新增）
            }
        """

        # 图像只编码一次
        image_features = self._encode_image(image_input)

        # L1: 主体识别（始终执行）
        l1 = self._classify(
            image_features,
            self.text_features_L1,
            L1_LABELS_EN,
            L1_LABEL_MAP,
        )

        # L2: 兽装物种（仅当 L1 判断为兽装时执行）
        l2 = None
        if l1["category"] == L1_FURSUIT_LABEL:
            l2 = self._classify(
                image_features,
                self.text_features_L2,
                L2_LABELS_EN,
                L2_LABEL_MAP,
            )

        # L3: 照片类型（始终执行）
        l3 = self._classify(
            image_features,
            self.text_features_L3,
            L3_LABELS_EN,
            L3_LABEL_MAP,
        )

        # 组装最终结果（兼容旧接口）
        if l2 is not None:
            final_category = l2["category"]
            final_quality = l2["confidence"]
            final_scores = l2["scores"]
        else:
            final_category = l1["category"]
            final_quality = l1["confidence"]
            final_scores = l1["scores"]

        return {
            "category": final_category,
            "quality": final_quality,
            "scores": final_scores,
            "layer1": l1,
            "layer2": l2,
            "layer3": l3,
        }