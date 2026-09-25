# core/visual_search/query.py
"""自然语言查询预处理：中文关键词 → CLIP 更擅长的英文提示词。

背景（2026-09-25 实测，ViT-L-14 / datacomp_xl_s13b_b90k，240 张真实图库）：
CLIP 文本塔对中文可用但明显偏弱。同一张图的最高相似度：
- 中文「兽装」0.220 → 英文 "a photo of fursuit" 0.318（+0.10，排名也随之改善）
- 中文「狼」0.251 ≈ 英文 "a photo of wolf" 0.253
- 而「风景 / 食物 / 夜晚 / 多人合照」的英文提示词反而略低于中文原文

因此这里做**离线关键词映射**：保留原始中文查询的同时，额外给出英文提示词，
检索时逐照片取最大相似度——**只会变好，不会变差**（上例「兽装」0.220 → 0.318，其余保持不变）。

注意：不引入翻译服务/网络依赖；映射表覆盖照片常见语义，可随时增补。
"""

# 中文关键词 → 英文提示词。键按「长词优先」匹配（见 _match_terms），
# 因此「水果」不会被单字「水」误判为 water。
_TERM_MAP = {
    # 兽装 / 二次元
    "兽装": ("fursuit", "mascot costume"),
    "福瑞": ("furry", "anthropomorphic animal"),
    "兽人": ("furry", "anthropomorphic animal"),
    "毛绒": ("fursuit", "plush costume"),
    "动漫": ("anime", "cartoon"),
    "二次元": ("anime", "cartoon"),
    "手办": ("figurine", "figure"),
    # 人物
    "合照": ("group photo", "several people"),
    "自拍": ("selfie",),
    "人物": ("person", "portrait"),
    "男人": ("man",),
    "女人": ("woman",),
    "小孩": ("child",),
    "人群": ("crowd",),
    # 动物
    "狼": ("wolf",),
    "狐狸": ("fox",),
    "猫": ("cat",),
    "狗": ("dog",),
    "兔": ("rabbit",),
    "马": ("horse",),
    "鸟": ("bird",),
    "龙": ("dragon",),
    "动物": ("animal",),
    # 自然 / 风景
    "风景": ("landscape", "scenery"),
    "山": ("mountain",),
    "海": ("sea",),
    "沙滩": ("beach", "sand"),
    "湖": ("lake",),
    "水": ("water",),
    "天空": ("sky",),
    "云": ("clouds",),
    "日落": ("sunset",),
    "夕阳": ("sunset",),
    "夜景": ("night scene", "city lights"),
    "夜晚": ("night",),
    "森林": ("forest",),
    "树林": ("forest",),
    "树": ("tree",),
    "花": ("flower",),
    "水果": ("fruit",),
    "草": ("grass",),
    "雪": ("snow",),
    "雨": ("rain",),
    # 城市 / 室内
    "城市": ("city",),
    "建筑": ("building",),
    "房子": ("house",),
    "室内": ("indoor",),
    "室外": ("outdoor",),
    "街": ("street",),
    # 食物 / 物件
    "食物": ("food",),
    "美食": ("food", "delicious meal"),
    "咖啡": ("coffee",),
    "蛋糕": ("cake",),
    "车": ("car",),
    "舞台": ("stage", "performance"),
    "跳舞": ("dancing",),
    # 颜色
    "红色": ("red",),
    "蓝色": ("blue",),
    "绿色": ("green",),
    "黄色": ("yellow",),
    "黑色": ("black",),
    "白色": ("white",),
}

#: 单次查询最多生成的变体数（原始查询 + 英文提示词），控制编码开销
MAX_VARIANTS = 4
_PROMPT_PREFIX = "a photo of "


def has_cjk(text: str) -> bool:
    """是否含中日韩表意文字（用于判断是否需要走英文映射）。"""
    for ch in str(text or ""):
        if "\u4e00" <= ch <= "\u9fff":
            return True
    return False


def match_terms(text: str):
    """返回查询里命中的中文关键词（长词优先，命中区间不重复计入）。"""
    q = str(text or "").strip()
    if not q:
        return []
    consumed = [False] * len(q)
    hits = []
    for cn in sorted(_TERM_MAP, key=len, reverse=True):
        start = q.find(cn)
        while start != -1:
            span = range(start, start + len(cn))
            if not any(consumed[i] for i in span):
                hits.append(cn)
                for i in span:
                    consumed[i] = True
            start = q.find(cn, start + 1)
    return hits


def expand_queries(text, limit: int = MAX_VARIANTS):
    """把查询扩展为「原始查询 + 英文提示词」列表（去重、限量、保序）。

    - 英文/无映射查询：只返回原始串（行为不变）
    - 中文命中关键词：额外给出 ``a photo of <英文词>`` 组合与单词提示词
    """
    q = str(text or "").strip()
    if not q:
        return []
    out = [q]
    terms = []
    for cn in match_terms(q):
        for en in _TERM_MAP.get(cn, ()):      # 同一中文词可映射多个英文词
            if en not in terms:
                terms.append(en)
    if terms:
        out.append(_PROMPT_PREFIX + ", ".join(terms))
        for en in terms:
            out.append(_PROMPT_PREFIX + en)
    seen = set()
    uniq = []
    for item in out:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq[:max(1, int(limit or MAX_VARIANTS))]
