"""core/photo_quality —— 角色内 AI 照片精选（第一阶段）。

借鉴 rickkeller/photo-sorter 思路，结合本项目架构实现：
- technical.py   技术指标（清晰度 / 曝光 / 对比度 / 饱和度 / 边缘能量）
- aesthetic.py   美学评分（第一版：技术启发式；预留 CLIP 可选接口）
- duplicate.py   近似照片分组（dHash + 直方图，无模型）
- selector.py    相似组内选择最佳照片 + 推荐理由
- scorer.py      评分入口：单角色分析 + 独立缓存（cache/photo_quality.json）

设计约束：
- 与 identity 数据完全分离，不读写 identity_db；
- 不修改 Fursee / Face / character_id / 聚类参数 / 增量扫描链路；
- 只读照片文件，绝不删除 / 移动任何原图；
- 支持单个角色分析，默认不扫描全库；
- 已分析照片命中缓存直接复用，mtime/size 变化自动重算；
- 全部可后台 QThread 调用（纯 PIL/numpy，线程安全）。
"""

__version__ = "1.0"
