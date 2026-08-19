# core/ai_advisor.py
"""
AI建议生成 + 反馈管理模块 V3.3

封装为 AIAdvisor 类，统一管理：
    - 反馈存储与读取
    - AI 建议生成（含智能修正）
    - 标签映射与星级评定

向后兼容：文件末尾保留模块级包装函数，旧调用方式不受影响。

使用方式（新）：
    advisor = AIAdvisor()
    advice = advisor.generate_ai_advice(category_en, category_cn, quality, scores, image_path)
    advisor.save_feedback(image_path, ai_category, human_category, timestamp)
    fb = advisor.get_feedback_for_image(image_path)

使用方式（旧，兼容）：
    from core.ai_advisor import generate_ai_advice, save_feedback
"""

import os
import json
from pathlib import Path
from typing import Optional, Dict, List, Any

from config.labels import TAG_MAP, FURSUIT_LABELS


class AIAdvisor:
    """
    AI 建议生成与反馈管理。

    成员变量：
        - feedback_file: 反馈文件路径
        - tag_map: 分类→标签映射（来自 config/labels.py）
        - fursuit_labels: 兽装标签集合（用于智能修正）

    公开方法：
        - generate_ai_advice()
        - save_feedback()
        - get_feedback_for_image()
    """

    def __init__(self):
        """初始化，加载配置和标签映射"""
        self.feedback_file: str = str(Path(__file__).resolve().parents[1] / "feedback.json")
        self.tag_map: Dict[str, List[str]] = TAG_MAP
        self.fursuit_labels: set = FURSUIT_LABELS

    # ============================================================
    # 反馈管理
    # ============================================================

    def save_feedback(
        self,
        image_path: str,
        ai_category: str,
        human_category: str,
        timestamp: str
    ) -> None:
        """
        保存人工反馈到 JSON 文件。

        参数:
            image_path: 图片绝对路径
            ai_category: AI 判断的分类（中文）
            human_category: 人工标注的分类（中文）
            timestamp: 反馈时间字符串
        """

        project_root = str(Path(__file__).resolve().parents[1])

        try:
            rel_path = os.path.relpath(image_path, project_root)
        except ValueError:
            rel_path = image_path

        rel_path = rel_path.replace("\\", "/")
        image_path = image_path.replace("\\", "/")

        record: Dict[str, str] = {
            "image_path": image_path,
            "rel_path": rel_path,
            "ai_category": ai_category,
            "human_category": human_category,
            "timestamp": timestamp,
        }

        feedbacks: List[Dict[str, str]] = []
        if os.path.exists(self.feedback_file):
            try:
                with open(self.feedback_file, "r", encoding="utf-8") as f:
                    content = f.read()
                    if content.strip():
                        feedbacks = json.loads(content)
            except (json.JSONDecodeError, IOError):
                feedbacks = []

        feedbacks.append(record)

        with open(self.feedback_file, "w", encoding="utf-8") as f:
            json.dump(feedbacks, f, ensure_ascii=False, indent=2)

        # 同步写入缓存
        from core.analysis_cache import get_cache
        get_cache().set_category_cn(image_path, human_category)

    def get_feedback_for_image(self, image_path: str) -> Optional[str]:
        """
        查询某张图片是否有人工反馈。

        参数:
            image_path: 图片路径

        返回:
            人工标注的分类名，没有则返回 None
        """

        if not os.path.exists(self.feedback_file):
            return None

        try:
            with open(self.feedback_file, "r", encoding="utf-8") as f:
                feedbacks: List[Dict[str, str]] = json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

        image_path = image_path.replace("\\", "/")

        for record in reversed(feedbacks):
            stored_path = record.get("image_path", "").replace("\\", "/")
            rel_path = record.get("rel_path", "").replace("\\", "/")

            if stored_path == image_path or (rel_path and image_path.endswith(rel_path)):
                return record.get("human_category")

        return None

    # ============================================================
    # AI 建议生成
    # ============================================================

    def generate_ai_advice(
        self,
        category_en: str,
        category_cn: str,
        quality: float,
        scores: Optional[Dict[str, float]] = None,
        image_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        根据 AI 分析结果自动生成建议，含智能修正。

        参数:
            category_en: 英文分类名
            category_cn: 中文分类名（可能含 emoji）
            quality: 可信度百分比（0-100）
            scores: 所有分类的概率字典
            image_path: 图片路径（用于检查反馈）

        返回:
            dict: {
                "category_cn": 修正后的中文分类名,
                "detection": 检测结果文本,
                "suggestion": 归档建议文本,
                "tags": 建议标签列表,
                "stars": 推荐星级文本,
            }
        """

        clean_cn = self._strip_emoji(category_cn)
        corrected_cn = clean_cn

        # 检查历史反馈
        if image_path:
            fb = self.get_feedback_for_image(image_path)
            if fb:
                return self._build_result(
                    fb,
                    f"根据历史人工标注，这是一张{fb}照片。",
                    quality
                )

        # 智能修正：兽装 Top1/Top2 接近时降级
        if scores:
            fursuit_scores = {
                k: v for k, v in scores.items() if k in self.fursuit_labels
            }
            if fursuit_scores and category_en in fursuit_scores:
                top = max(fursuit_scores.values())
                if fursuit_scores[category_en] == top:
                    arr = sorted(fursuit_scores.values(), reverse=True)
                    if len(arr) >= 2 and (arr[0] - arr[1]) < 0.05:
                        corrected_cn = "兽装人物"

        if corrected_cn != clean_cn:
            detection = f"AI认为这是一张兽装照片，但无法确定具体物种（可能是{clean_cn}）。"
        else:
            detection = f"AI认为这是一张{corrected_cn}照片。"

        if quality < 60:
            detection = "AI对此图片判断不够确定。"
            return self._build_result(corrected_cn, detection, quality, suggest_confirm=True)

        return self._build_result(corrected_cn, detection, quality)

    # ============================================================
    # 辅助方法
    # ============================================================

    @staticmethod
    def _strip_emoji(text: str) -> str:
        """去掉字符串开头的 emoji 和空格"""
        for i in range(len(text)):
            if ord(text[i]) > 127:
                continue
            elif text[i] == ' ':
                return text[i + 1:]
            else:
                break
        return text

    def _build_result(
        self,
        category: str,
        detection: str,
        quality: float,
        suggest_confirm: bool = False
    ) -> Dict[str, Any]:
        """构建建议结果字典"""
        suggestion = (
            "建议人工确认分类。" if suggest_confirm
            else f"建议归档到【{category}】分类。"
        )
        tags = self.tag_map.get(category, [category])

        if quality >= 90:
            stars = "★★★★★"
        elif quality >= 70:
            stars = "★★★★☆"
        elif quality >= 50:
            stars = "★★★☆☆"
        elif quality >= 30:
            stars = "★★☆☆☆"
        else:
            stars = "★☆☆☆☆"

        return {
            "category_cn": category,
            "detection": detection,
            "suggestion": suggestion,
            "tags": tags,
            "stars": stars,
        }


# ============================================================
# 向后兼容：模块级包装函数
# 旧代码无需修改即可继续使用
# ============================================================

_advisor = AIAdvisor()


def generate_ai_advice(
    category_en: str,
    category_cn: str,
    quality: float,
    scores: Optional[Dict[str, float]] = None,
    image_path: Optional[str] = None
) -> Dict[str, Any]:
    """兼容旧接口：生成 AI 建议"""
    return _advisor.generate_ai_advice(category_en, category_cn, quality, scores, image_path)


def save_feedback(
    image_path: str,
    ai_category: str,
    human_category: str,
    timestamp: str
) -> None:
    """兼容旧接口：保存反馈"""
    _advisor.save_feedback(image_path, ai_category, human_category, timestamp)


def get_feedback_for_image(image_path: str) -> Optional[str]:
    """兼容旧接口：查询反馈"""
    return _advisor.get_feedback_for_image(image_path)