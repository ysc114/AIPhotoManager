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

    # --------------------------------------------------------
    # 索引构建
    # --------------------------------------------------------
    def refresh(self):
        """重建索引（惰性；只在需要时调用）。"""
        self._roles = self._build_roles()
        self._photos = self._build_photos()
        self._favs = self._build_favorites()

    def _build_roles(self):
        """角色索引：get_groups('all')（兽装+人物，排除 legacy）。"""
        try:
            from core.identity import IdentityManager
            mgr = IdentityManager()
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
            from core.identity import IdentityManager
            mgr = IdentityManager()
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

    # --------------------------------------------------------
    # 搜索
    # --------------------------------------------------------
    def search(self, query, limit_roles=15, limit_photos=20):
        """按子串匹配搜索（不区分大小写）。返回 dict。

        roles: 匹配的角色组 dict（含 name/character_id/type/detections）
        photos: [{"path","name","favorite"}]
        """
        q = (query or "").strip().lower()
        if not q:
            return {"roles": [], "photos": []}
        if self._roles is None:
            self.refresh()

        roles = []
        for g in (self._roles or []):
            if len(roles) >= limit_roles:
                break
            name = str(g.get("name") or "")
            cid = str(g.get("character_id") or "")
            gtype = str(g.get("type") or "")
            kws = _TYPE_KEYWORDS.get(gtype, ())
            haystack = (name + " " + cid + " " + " ".join(kws)).lower()
            if q in haystack or q in name.lower() or q in cid.lower():
                roles.append(g)

        photos = []
        for p in (self._photos or []):
            if len(photos) >= limit_photos:
                break
            base = os.path.splitext(p["name"])[0].lower()
            if q in base or q in p["name"].lower():
                photos.append({
                    "path": p["path"],
                    "name": p["name"],
                    "favorite": p["path"] in (self._favs or set()),
                })
        return {"roles": roles, "photos": photos}


# 模块级单例
search_index = SearchIndex()
