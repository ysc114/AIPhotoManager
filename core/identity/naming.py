# core/identity/naming.py
"""角色显示名（纯展示层：只读，不写库、不改 schema）。

命名规则（统一卡片 / 照片墙 / 搜索 / 合照跳转菜单的显示）：

- 已命名            → 用户起的名字
- 未命名 + 有序号   → 「未命名{类型标签} #{序号:03d}」
- 未命名 + 无序号   → 「未命名{类型标签}」

序号来自 identity_group 的稳定排序（同类型内 created_at DESC, id DESC），
与角色列表默认顺序一致：最新的组是 #001。序号只用于显示，库里不落新字段。
"""

_LABELS = {
    "fursuit_character": "兽装角色",
    "real_person": "人物",
}


def type_label(group_type=None):
    """角色类型 → 中文标签（未知类型回退「角色」）。"""
    return _LABELS.get(str(group_type or ""), "角色")


def display_name(name=None, group_type=None, serial=None, photos=None):
    """统一的角色显示名；photos 非空时追加「· N 张照片」。"""
    base = str(name or "").strip()
    if not base:
        label = type_label(group_type)
        base = (f"未命名{label} #{int(serial):03d}"
                if serial else f"未命名{label}")
    if photos:
        base = f"{base} · {int(photos)} 张照片"
    return base
