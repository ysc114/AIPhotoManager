# config/labels.py
"""
AI Photo Manager - 分类标签配置 V3.2
三级分类架构：
    L1: 主体识别（兽装/普通人物/动物/插画/风景）
    L2: 兽装物种（狼/狐狸/狗/猫/虎/豹/熊/兔/龙/其他）
    L3: 照片类型（大头照/半身照/全身照/多人/舞台/自拍/室内/室外）
"""

# ============================================================
# L1: 主体识别（5 个标签，粗粒度，避免同类稀释）
# ============================================================

L1_LABELS_EN = [
    "a person wearing a fursuit or furry animal costume",
    "a normal person in casual clothing",
    "a real animal",
    "a digital illustration or cartoon artwork",
    "a landscape, building, or still object",
]

L1_LABEL_MAP = {
    "a person wearing a fursuit or furry animal costume": "🦁 兽装人物",
    "a normal person in casual clothing": "👤 普通人物",
    "a real animal": "🐾 真实动物",
    "a digital illustration or cartoon artwork": "🎨 插画/绘画",
    "a landscape, building, or still object": "🏞 风景/物品",
}

L1_TAG_MAP = {
    "兽装人物": ["Furry", "兽装"],
    "普通人物": ["人物", "肖像"],
    "真实动物": ["动物"],
    "插画/绘画": ["插画", "绘画"],
    "风景/物品": ["风景", "物品"],
}


# ============================================================
# L2: 兽装物种（10 个标签，仅 L1=兽装人物 时触发）
# ============================================================

L2_LABELS_EN = [
    "wolf fursuit",
    "fox fursuit",
    "dog fursuit",
    "cat fursuit",
    "tiger fursuit",
    "leopard fursuit",
    "bear fursuit",
    "rabbit fursuit",
    "dragon fursuit",
    "other species fursuit",
]

L2_LABEL_MAP = {
    "wolf fursuit": "🐺 狼兽装",
    "fox fursuit": "🦊 狐狸兽装",
    "dog fursuit": "🐶 犬兽装",
    "cat fursuit": "🐱 猫兽装",
    "tiger fursuit": "🐯 虎兽装",
    "leopard fursuit": "🐆 豹兽装",
    "bear fursuit": "🐻 熊兽装",
    "rabbit fursuit": "🐰 兔兽装",
    "dragon fursuit": "🐉 龙兽装",
    "other species fursuit": "🐾 其他兽装",
}

L2_TAG_MAP = {
    "狼兽装":   ["狼",   "Furry", "兽装"],
    "狐狸兽装": ["狐狸", "Furry", "兽装"],
    "犬兽装":   ["犬",   "Furry", "兽装"],
    "猫兽装":   ["猫",   "Furry", "兽装"],
    "虎兽装":   ["虎",   "Furry", "兽装"],
    "豹兽装":   ["豹",   "Furry", "兽装"],
    "熊兽装":   ["熊",   "Furry", "兽装"],
    "兔兽装":   ["兔",   "Furry", "兽装"],
    "龙兽装":   ["龙",   "Furry", "兽装"],
    "其他兽装": ["Furry", "兽装"],
}


# ============================================================
# L3: 照片类型（8 个标签）
# ============================================================

L3_LABELS_EN = [
    "a close-up headshot portrait",
    "a half-body portrait",
    "a full-body standing photo",
    "a group photo with multiple people",
    "a stage or convention performance photo",
    "a selfie photo",
    "an indoor photo",
    "an outdoor photo",
]

L3_LABEL_MAP = {
    "a close-up headshot portrait": "📷 大头照",
    "a half-body portrait": "📷 半身照",
    "a full-body standing photo": "📷 全身照",
    "a group photo with multiple people": "👥 多人合照",
    "a stage or convention performance photo": "🎪 舞台/展会",
    "a selfie photo": "🤳 自拍",
    "an indoor photo": "🏠 室内",
    "an outdoor photo": "🌳 室外",
}


# ============================================================
# 兼容旧接口
# ============================================================

LABEL_MAP = {}
LABEL_MAP.update(L1_LABEL_MAP)
LABEL_MAP.update(L2_LABEL_MAP)
LABEL_MAP.update(L3_LABEL_MAP)

TAG_MAP = {}
TAG_MAP.update(L1_TAG_MAP)
TAG_MAP.update(L2_TAG_MAP)

FURSUIT_LABELS = set(L2_LABELS_EN)

L1_FURSUIT_LABEL = "a person wearing a fursuit or furry animal costume"