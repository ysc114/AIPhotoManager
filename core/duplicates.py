"""
duplicates.py —— 重复照片管理中心（只读扫描 + 安全删除）

在既有 MD5 同图判定基础上提供独立能力（复用同一定义：MD5 完全一致才算重复）：

- scan()：遍历 photos/ 目录，按 MD5 分组；>1 张的组返回
  （相同内容不同文件名，如 xxx.jpg / xxx (1).jpg，归为同一组）
- delete_path()：安全删除单个副本：
    1. 只删「与组内另一副本 MD5 一致」的文件（防误删）
    2. 删文件 → identity_db.remove_image（该照片全部 detection）
       → favorite 移除 → analysis_cache 键清理
    3. 任何一步失败不破坏数据库（文件删除失败则不碰数据库；
       数据库清理失败仅记录并继续，不抛中断）
- 与角色身份彻底分离：只删 identity_image 行，绝不触碰 identity_group、
  character_id、其他照片的 detection / Fursee / Face embedding；
  合照多角色归属不受影响（该文件全部 detection 一起移除，其他文件保留）

所有路径统一为绝对路径（正斜杠），与 identity_image.image_path /
analysis_cache 键一致。
"""

import hashlib
import os
from pathlib import Path

# 支持的图片扩展（与照片库一致）
_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def norm_path(p):
    """统一为绝对路径 + 正斜杠。"""
    return str(Path(p).resolve()).replace("\\", "/")


def _md5(p, chunk=1 << 20):
    """流式 MD5（避免大文件整读）。"""
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class DuplicateScanner:
    """只读扫描：按 MD5 找出完全相同的照片组。"""

    def __init__(self, photos_dir=None):
        self.photos_dir = str(photos_dir) if photos_dir else None

    def _resolve_dir(self):
        if self.photos_dir:
            return self.photos_dir
        return norm_path(Path(__file__).resolve().parent.parent / "photos")

    def scan(self, progress_cb=None):
        """返回 [ {md5, paths:[{path,name,size}...]}, ... ]（仅 >1 的组）。

        progress_cb(md5_hex, total) 可选进度回调（不阻塞扫描）。
        """
        d = self._resolve_dir()
        files = []
        if os.path.isdir(d):
            for n in sorted(os.listdir(d)):
                if os.path.splitext(n)[1].lower() in _PHOTO_EXTS:
                    files.append(os.path.join(d, n))
        total = len(files)
        groups = {}
        for i, p in enumerate(files):
            try:
                key = _md5(p)
            except OSError:
                continue
            groups.setdefault(key, []).append(p)
            if progress_cb and (i % 25 == 0 or i == total - 1):
                progress_cb(i + 1, total)
        result = []
        for key, paths in groups.items():
            if len(paths) < 2:
                continue
            items = []
            for p in paths:
                try:
                    size = os.path.getsize(p)
                except OSError:
                    size = 0
                items.append({"path": norm_path(p), "name": os.path.basename(p), "size": size})
            result.append({"md5": key, "paths": items})
        result.sort(key=lambda g: -sum(x["size"] for x in g["paths"]))
        return result


class DuplicateCleaner:
    """安全删除重复副本（文件 + 数据库 + 缓存一致性）。"""

    def __init__(self, photos_dir=None):
        self.photos_dir = photos_dir

    # --------------------------------------------------------
    # 数据库清理
    # --------------------------------------------------------
    @staticmethod
    def _clean_database(path):
        """清理 path 的照片记录：identity_image 全行 + 收藏 + 分析缓存。

        只删该照片自身记录；不触碰角色组 / character_id / 其他照片。
        任何失败只记录，不影响已完成的删除。
        """
        errors = []
        # identity_image（整张照片全部 detection，含合照多角色）
        try:
            from core.identity import IdentityManager
            mgr = IdentityManager()
            try:
                mgr.db.remove_image(path)
            finally:
                mgr.close()
        except Exception as e:
            errors.append(f"identity_db: {e}")
        # 收藏
        try:
            from core.identity import IdentityManager
            mgr = IdentityManager()
            try:
                mgr.db.remove_favorite(path)
            finally:
                mgr.close()
        except Exception as e:
            errors.append(f"favorite: {e}")
        # 分析缓存（单键移除）
        try:
            from core.analysis_cache import get_cache
            get_cache().remove(path)
        except Exception as e:
            errors.append(f"analysis_cache: {e}")
        return errors

    # --------------------------------------------------------
    # 删除入口
    # --------------------------------------------------------
    def delete_paths(self, paths, keep_md5_set=None):
        """批量删除副本。

        paths: 待删绝对路径列表
        keep_md5_set: 可选的「受保护 MD5 集合」——属于该集合的文件
        （即组内 MD5 与某个保留文件相同的）才允许删。用于防止误删
        非重复/保留文件。
        返回 {"deleted": [path...], "failed": [path...], "errors": [...]}
        """
        deleted, failed, errors = [], [], []
        for p in paths:
            np = norm_path(p)
            if not os.path.exists(np):
                failed.append(np)
                errors.append(f"文件不存在: {os.path.basename(np)}")
                continue
            # 安全校验：keep_md5_set 提供时，只允许删 MD5 命中集合的文件
            if keep_md5_set is not None:
                try:
                    if _md5(np) not in keep_md5_set:
                        failed.append(np)
                        errors.append(f"跳过非重复文件: {os.path.basename(np)}")
                        continue
                except OSError as e:
                    failed.append(np)
                    errors.append(f"MD5 读取失败: {e}")
                    continue
            # 1) 先删文件：失败则不碰数据库（防库表残留路径但文件还在）
            try:
                os.remove(np)
            except OSError as e:
                failed.append(np)
                errors.append(f"删除失败: {e}")
                continue
            # 2) 数据库/缓存清理（失败仅记录，不回滚已删文件）
            db_errors = self._clean_database(np)
            errors.extend(f"{os.path.basename(np)}: {e}" for e in db_errors)
            deleted.append(np)
        return {"deleted": deleted, "failed": failed, "errors": errors}
