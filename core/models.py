from dataclasses import dataclass, field
from typing import Dict, Any, List


@dataclass
class Photo:
    # 图片路径
    path: str

    # 图片基础信息
    width: int = 0
    height: int = 0
    size: int = 0
    date: float = 0.0

    # AI标签
    tags: List[str] = field(default_factory=list)

    # AI评分
    score: float = 0.0

    # AI详细数据
    metadata: Dict[str, Any] = field(default_factory=dict)