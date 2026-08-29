"""
icons.py —— 公共线性图标库（现代系统级风格 · 原创设计）

参考 Apple SF Symbols / HyperOS 系统图标的设计思路（非复制）：
- 统一线宽：默认 2.2px 圆头描边（RoundCap / RoundJoin）
- 统一圆角：所有几何元素圆角化（矩形圆角 / 顶点圆角）
- 统一视觉重量：主体约占 70~85% 画布，居中
- 简洁几何化：无冗余细节，小尺寸（25px）下依然清晰

家族化设计（三级分类体系）：
- 兽装 fursuit：抽象「圆耳兽头」轮廓（头 + 双耳 + 点眼）
- 人物 person： 抽象「人物头像」轮廓（圆头 + 肩弧）
- 角色 character：抽象「角色卡片」（小头像圆 + 卡片框）
  三者共用「圆形头部」元素，形成明显同系列；区分度靠耳朵/肩/卡片。

三态适配（由调用方控制颜色/光晕，本库只画线条）：
- 普通：低对比单色线性
- Hover：调用方提亮 + Aurora 光晕
- 选中：调用方加深 + 加粗描边（emphasized）

依赖：仅 QPainterPath / QPen，任何 QWidget 可复用。
"""

import math

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainterPath, QPen

# 支持的图标键
ICON_KEYS = (
    "overview", "ai_pick", "photo", "fursuit", "person",
    "character", "favorites", "pending", "settings", "search", "close",
)


def _stroke(p, color, pen_width, emphasized):
    pen = QPen(color, pen_width + 0.35 if emphasized else pen_width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)


def _rounded_rect(x0, y0, x1, y1, r):
    """归一化圆角矩形 path。"""
    path = QPainterPath()
    path.addRoundedRect(QRectF(x0, y0, x1 - x0, y1 - y0), r, r)
    return path


def _circle(cx, cy, r):
    path = QPainterPath()
    path.addEllipse(QPointF(cx, cy), r, r)
    return path


# ------------------------------------------------------------
# 图标路径（归一化 0~1 坐标）
# ------------------------------------------------------------
def _path_overview():
    """总览：现代圆润房屋（屋顶+墙身一体 + 圆角门）。"""
    path = QPainterPath()
    path.moveTo(0.5, 0.06)
    path.lineTo(0.9, 0.38)
    path.lineTo(0.9, 0.9)
    path.lineTo(0.1, 0.9)
    path.lineTo(0.1, 0.38)
    path.closeSubpath()
    # 门（U 形）
    door = QPainterPath()
    door.moveTo(0.42, 0.9)
    door.lineTo(0.42, 0.66)
    door.quadTo(0.5, 0.56, 0.58, 0.66)
    door.lineTo(0.58, 0.9)
    return [path, door]


def _path_ai_pick():
    """AI 整理：主 Sparkle + 小图片框（AI 魔法闪光 + 照片整理）。"""
    cx, cy = 0.74, 0.26
    sp = QPainterPath()
    sp.moveTo(cx, cy - 0.30)
    sp.quadTo(cx + 0.10, cy - 0.04, cx + 0.30, cy)
    sp.quadTo(cx + 0.10, cy + 0.04, cx, cy + 0.30)
    sp.quadTo(cx - 0.10, cy + 0.04, cx - 0.30, cy)
    sp.quadTo(cx - 0.10, cy - 0.04, cx, cy - 0.30)
    sp.closeSubpath()
    # 小图片框 + 山
    frame = _rounded_rect(0.06, 0.38, 0.58, 0.94, 0.10)
    sun = _circle(0.20, 0.52, 0.06)
    hill = QPainterPath()
    hill.moveTo(0.12, 0.82)
    hill.lineTo(0.34, 0.62)
    hill.lineTo(0.46, 0.74)
    return [sp, frame, sun, hill]


def _path_photo():
    """照片：圆角照片框 + 太阳 + 山景。"""
    frame = _rounded_rect(0.08, 0.14, 0.92, 0.86, 0.13)
    sun = _circle(0.30, 0.36, 0.08)
    hill = QPainterPath()
    hill.moveTo(0.14, 0.74)
    hill.lineTo(0.38, 0.50)
    hill.lineTo(0.56, 0.66)
    hill.lineTo(0.86, 0.46)
    return [frame, sun, hill]


def _path_fursuit():
    """兽装（家族 1）：抽象圆耳兽头轮廓 + 点眼。"""
    head = QPainterPath()
    # 左耳（外弧向上尖）
    head.moveTo(0.24, 0.56)
    head.quadTo(0.10, 0.20, 0.40, 0.20)
    # 头顶中凹 → 右耳
    head.quadTo(0.50, 0.30, 0.60, 0.20)
    head.quadTo(0.90, 0.20, 0.76, 0.56)
    # 脸颊 → 下巴 → 回起点
    head.quadTo(0.70, 0.90, 0.50, 0.90)
    head.quadTo(0.30, 0.90, 0.24, 0.56)
    head.closeSubpath()
    # 点眼
    eye_l = _circle(0.40, 0.50, 0.035)
    eye_r = _circle(0.60, 0.50, 0.035)
    return [head, eye_l, eye_r]


def _path_person():
    """人物（家族 2）：圆头 + 肩弧。"""
    head = _circle(0.50, 0.30, 0.22)
    shoulder = QPainterPath()
    shoulder.moveTo(0.08, 0.94)
    shoulder.quadTo(0.50, 0.42, 0.92, 0.94)
    return [head, shoulder]


def _path_character():
    """角色（家族 3）：圆形头像 + 角色卡片。"""
    avatar = _circle(0.50, 0.20, 0.13)
    card = _rounded_rect(0.14, 0.42, 0.86, 0.94, 0.13)
    inner = _circle(0.50, 0.66, 0.10)
    return [avatar, card, inner]


def _path_favorites():
    """收藏：几何化五角星（圆角 join 自然圆润）。"""
    path = QPainterPath()
    cx, cy, R, r = 0.5, 0.52, 0.44, 0.20
    for i in range(5):
        a0 = i * (2 * math.pi / 5) - math.pi / 2
        a1 = a0 + math.pi / 5
        x0, y0 = cx + math.cos(a0) * R, cy + math.sin(a0) * R
        x1, y1 = cx + math.cos(a1) * r, cy + math.sin(a1) * r
        if i == 0:
            path.moveTo(x0, y0)
        else:
            path.lineTo(x0, y0)
        path.lineTo(x1, y1)
    path.closeSubpath()
    return [path]


def _path_pending():
    """待处理：收件箱（圆角梯形 + 内部向下箭头=有待处理内容）。"""
    box = QPainterPath()
    box.moveTo(0.16, 0.42)
    box.lineTo(0.84, 0.42)
    box.lineTo(0.74, 0.88)
    box.lineTo(0.26, 0.88)
    box.closeSubpath()
    # 向下箭头（内容待收）
    arrow = QPainterPath()
    arrow.moveTo(0.50, 0.50)
    arrow.lineTo(0.50, 0.72)
    arrow.moveTo(0.40, 0.62)
    arrow.lineTo(0.50, 0.74)
    arrow.lineTo(0.60, 0.62)
    return [box, arrow]


def _path_settings():
    """设置：简化齿轮（4 齿圆角矩形 + 中心圆，现代风格）。"""
    gear = []
    # 上下左右 4 个圆角齿
    gear.append(_rounded_rect(0.42, 0.10, 0.58, 0.32, 0.05))
    gear.append(_rounded_rect(0.42, 0.68, 0.58, 0.90, 0.05))
    gear.append(_rounded_rect(0.10, 0.42, 0.32, 0.58, 0.05))
    gear.append(_rounded_rect(0.68, 0.42, 0.90, 0.58, 0.05))
    # 中心圆
    gear.append(_circle(0.50, 0.50, 0.17))
    return gear


def _path_search():
    """搜索：圆 + 手柄（供搜索框使用）。"""
    return [_circle(0.40, 0.40, 0.30), _stroke_line_handle()]


def _stroke_line_handle():
    """搜索手柄（45° 斜线）。"""
    path = QPainterPath()
    path.moveTo(0.60, 0.60)
    path.lineTo(0.90, 0.90)
    return path


def _path_close():
    path = QPainterPath()
    path.moveTo(0.2, 0.2)
    path.lineTo(0.8, 0.8)
    path.moveTo(0.8, 0.2)
    path.lineTo(0.2, 0.8)
    return [path]


# 图标键 → 路径列表构造器
_PATH_BUILDERS = {
    "overview": _path_overview,
    "ai_pick": _path_ai_pick,
    "photo": _path_photo,
    "fursuit": _path_fursuit,
    "person": _path_person,
    "character": _path_character,
    "favorites": _path_favorites,
    "pending": _path_pending,
    "settings": _path_settings,
    "search": _path_search,
    "close": _path_close,
}


def draw_icon(p, rect, key, color, pen_width=2.2, emphasized=False):
    """在 rect(QRectF) 内绘制 key 对应线性图标。

    p: QPainter（已开启 Antialiasing）
    color: QColor 主色
    emphasized: True 时描边加粗（选中/激活态）
    """
    builder = _PATH_BUILDERS.get(key)
    if builder is None:
        return
    _stroke(p, color, pen_width, emphasized)
    paths = builder()
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    from PySide6.QtGui import QTransform
    scale = QTransform(w, 0, 0, h, x, y)
    for path in paths:
        if path.isEmpty():
            continue
        p.drawPath(scale.map(path))
