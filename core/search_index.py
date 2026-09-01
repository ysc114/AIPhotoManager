"""
search_index.py —— 全局搜索只读索引

Spotlight 风格全局搜索的数据层：
- 角色：名称 / character_id / 类别（兽装/人物/角色页全部）
- 照片：photos/ 目录文件名
- 收藏：list_favorites() 路径（在照片结果上标注）

约束：
- 纯只读查询（get_groups / list_favorites / 目录扫描），
  绝不触发 AI 分析、不写数据库、不改任何身份数据
- 索引懒构建（首次搜索时 refresh），内存缓存

返回结构（search 方法）：
    {"roles": [group_dict, ...], "photos": [{"path", "name", "favorite"}], ...}
"""

import os
import re
import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 类别关键词 → 匹配词组（角色类别搜索）
_TYPE_KEYWORDS = {
    "fursuit_character": ("兽装", "fursuit", "fur", "兽"),
    "real_person": ("人物", "真人", "person", "face", "人"),
}

_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


class SearchIndex:
    """全局搜索索引（懒构建 + 内存缓存，只读）。"""

    def __init__(self, project_root=None):
        self._root = Path(project_root) if project_root else _PROJECT_ROOT
        self._roles = None
        self._photos = None
        self._favs = None
        self._photo_role = {}        # path -> {"name","character_id","type"}（照片所属角色）

    # --------------------------------------------------------
    # 索引构建
    # --------------------------------------------------------
    def refresh(self):
        """重建索引（惰性；只在需要时调用）。"""
        self._roles = self._build_roles()
        self._photos = self._build_photos()
        self._favs = self._build_favorites()
        self._build_photo_role_map()

    def _build_roles(self):
        """角色索引：get_groups('all')（兽装+人物，排除 legacy）。"""
        try:
            from core.identity import get_reader
            mgr = get_reader()   # 共享只读连接
            try:
                return mgr.get_groups("all") or []
            finally:
                mgr.close()
        except Exception as e:
            print(f"[搜索] 角色索引失败: {e}")
            return []

    def _build_photos(self):
        """照片索引：photos/ 目录全部图片文件。"""
        photos_dir = self._root / "photos"
        if not photos_dir.is_dir():
            return []
        out = []
        try:
            for n in sorted(os.listdir(photos_dir)):
                if os.path.splitext(n)[1].lower() in _PHOTO_EXTS:
                    out.append({
                        "path": (photos_dir / n).as_posix(),
                        "name": n,
                    })
        except OSError:
            pass
        return out

    def _build_favorites(self):
        """收藏照片路径集合。"""
        try:
            from core.identity import get_reader
            mgr = get_reader()   # 共享只读连接
            try:
                favs = mgr.db.list_favorites() or []
            finally:
                mgr.close()
            norm = set()
            for f in favs:
                norm.add(Path(f).as_posix().replace("\\", "/"))
            return norm
        except Exception:
            return set()

    def _build_photo_role_map(self):
        """照片 → 所属角色映射（只读，get_groups detections 构建）。

        一张照片可能属于多个角色（合照多角色）→ 取第一个有名字的角色。
        """
        mapping = {}
        for g in (self._roles or []):
            name = str(g.get("name") or "")
            cid = str(g.get("character_id") or "")
            gtype = str(g.get("type") or "")
            label = name if name else f"角色 {str(cid)[:10]}"
            for det in (g.get("detections") or []):
                img = det.get("image_path")
                if not img:
                    continue
                np_ = Path(img).as_posix().replace("\\", "/")
                mapping.setdefault(np_, {"name": label, "character_id": cid, "type": gtype})
        self._photo_role = mapping

    # --------------------------------------------------------
    # 搜索
    # --------------------------------------------------------
    def search(self, query, limit_roles=15, limit_photos=20,
               type_filter="all", date_from=None, date_to=None,
               favorite_only=False):
        """按子串匹配搜索（不区分大小写）。返回 dict。

        roles: 匹配的角色组 dict（含 name/character_id/type/detections）
        photos: [{"path","name","favorite","date","role"}]
        type_filter: "all" / "fursuit_character" / "real_person"
        date_from/date_to: "YYYY-MM-DD"（含边界，按照片日期过滤）
        favorite_only: True 时照片仅返回收藏
        """
        q = (query or "").strip().lower()
        if self._roles is None:
            self.refresh()
        # 空关键词：筛选器仍独立生效（返回过滤后的全量）；关键词非空才子串匹配

        roles = []
        for g in (self._roles or []):
            if len(roles) >= limit_roles:
                break
            gtype = str(g.get("type") or "")
            if type_filter != "all" and gtype != type_filter:
                continue
            name = str(g.get("name") or "")
            cid = str(g.get("character_id") or "")
            kws = _TYPE_KEYWORDS.get(gtype, ())
            haystack = (name + " " + cid + " " + " ".join(kws)).lower()
            if (not q) or q in haystack or q in name.lower() or q in cid.lower():
                roles.append(g)

        photos = []
        for p in (self._photos or []):
            if len(photos) >= limit_photos:
                break
            base = os.path.splitext(p["name"])[0].lower()
            if q and not (q in base or q in p["name"].lower()):
                continue
            fav = p["path"] in (self._favs or set())
            if favorite_only and not fav:
                continue
            d = self._photo_date(p["path"])
            if date_from and d < date_from:
                continue
            if date_to and d > date_to:
                continue
            role = self._photo_role.get(p["path"])
            photos.append({
                "path": p["path"],
                "name": p["name"],
                "favorite": fav,
                "date": d,
                "role": role,
            })
        return {"roles": roles, "photos": photos}

    @staticmethod
    def _photo_date(path):
        """照片日期：优先文件名时间戳，fallback 文件 mtime。返回 YYYY-MM-DD。"""
        name = os.path.basename(path)
        base = os.path.splitext(name)[0]
        # 20260601_125816.jpg 形式
        m = re.match(r"^(\d{8})(?:[_-]\d{1,6})?$", base)
        if m:
            y, mo, d = m.group(1)[:4], m.group(1)[4:6], m.group(1)[6:8]
            return f"{y}-{mo}-{d}"
        # 13 位毫秒时间戳（如 1787539644969）
        if base.isdigit() and len(base) >= 10:
            try:
                ts = int(base) / 1000.0 if len(base) == 13 else float(base)
                return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            except (ValueError, OSError, OverflowError):
                pass
        # fallback mtime
        try:
            return datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat()
        except OSError:
            return 


# 模块级单例
search_index = SearchIndex()
