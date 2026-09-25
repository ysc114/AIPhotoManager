# core/health_check.py
"""数据体检（纯只读）：一屏看清 库 / 照片 / 缓存 的健康状况。

设计要点：
- **只读**：不改数据库、不动照片、不写缓存；只 stat / 读 JSON / 查库
- 不加载任何 AI 模型（Fursee/CLIP 均不参与），实测全库 < 0.5s
- 每项返回 {key, label, status, count, detail, fix}；
  status ∈ ok / warn / error，UI 只负责展示与引导
- 单项失败不影响整体（各自 try/except，降级为 detail 说明）
"""

import os
import sqlite3
import time

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_ERROR = "error"

_ICONS = {STATUS_OK: "✅", STATUS_WARN: "⚠️", STATUS_ERROR: "❌"}


def status_icon(status):
    return _ICONS.get(str(status or ""), "•")


def _item(key, label, status, count=0, detail="", fix=""):
    return {"key": key, "label": label, "status": status, "count": int(count),
            "detail": detail, "fix": fix}


def _project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_health_check(photos_dir=None, db_path=None, analysis_cache_file=None,
                     visual_index_path=None, visual_search_cache_dir=None,
                     include_duplicates=True, project_root=None):
    """跑一遍体检，返回 {items, errors, warnings, ok_count}（纯只读）。"""
    root = project_root or _project_root()
    photos_dir = photos_dir or os.path.join(root, "photos")
    db_path = db_path or os.path.join(root, "identity_db.sqlite")
    items = []

    # ---- 数据库：引用缺失文件 / 未分配 / 孤儿组 / 完整性 ----
    db_meta = {}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = con.cursor()
        try:
            ok = cur.execute("PRAGMA quick_check").fetchone()[0]
            items.append(_item(
                "db_integrity", "数据库完整性", STATUS_OK if ok == "ok" else STATUS_ERROR,
                detail=("quick_check 通过" if ok == "ok" else f"quick_check: {ok}")))
        except Exception as e:
            items.append(_item("db_integrity", "数据库完整性", STATUS_ERROR,
                               detail=str(e)[:80]))
        paths = [r[0] for r in cur.execute(
            "SELECT DISTINCT image_path FROM identity_image").fetchall() if r[0]]
        missing = [p for p in paths if not os.path.exists(p)]
        items.append(_item(
            "db_missing_files", "库内照片文件", STATUS_WARN if missing else STATUS_OK,
            len(missing),
            detail=(f"{len(paths)} 张已入库，{len(missing)} 张文件缺失"
                    if missing else f"{len(paths)} 张全部存在"),
            fix="缺失文件：照片被移动/删除后可在「重复照片」页清理对应记录" if missing else ""))
        unassigned = cur.execute(
            "SELECT COUNT(*) FROM identity_image WHERE group_id=''").fetchone()[0]
        items.append(_item(
            "unassigned_rows", "未分配 detection",
            STATUS_WARN if unassigned else STATUS_OK, unassigned,
            detail=(f"{unassigned} 个 detection 尚未归组" if unassigned
                    else "全部 detection 已归组"),
            fix="运行「分析新照片」会按增量分配归组" if unassigned else ""))
        orphan = 0
        for (gid,) in cur.execute("SELECT id FROM identity_group").fetchall():
            ps = [r[0] for r in cur.execute(
                "SELECT DISTINCT image_path FROM identity_image WHERE group_id=?",
                (gid,))]
            if ps and all(not os.path.exists(p) for p in ps):
                orphan += 1
        items.append(_item(
            "orphan_groups", "角色组照片可用性",
            STATUS_WARN if orphan else STATUS_OK, orphan,
            detail=(f"{orphan} 个角色组的所有照片都已缺失" if orphan
                    else "每个角色组都至少有一张可用照片"),
            fix="清理缺失记录后这些空组可在角色页合并/删除" if orphan else ""))
        db_meta["groups"] = cur.execute(
            "SELECT COUNT(*) FROM identity_group").fetchone()[0]
        con.close()
    except Exception as e:
        items.append(_item("db_integrity", "数据库完整性", STATUS_ERROR,
                           detail=f"读取失败：{str(e)[:60]}"))

    # ---- photos/ 目录：未入库数量 ----
    if os.path.isdir(photos_dir):
        exts = (".jpg", ".jpeg", ".png", ".webp")
        files = [f for f in os.listdir(photos_dir)
                 if os.path.splitext(f)[1].lower() in exts]
        known = set()
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            known = {r[0] for r in con.execute(
                "SELECT DISTINCT image_path FROM identity_image").fetchall()}
            con.close()
        except Exception:
            pass
        pending = 0
        for name in files:
            p = os.path.join(photos_dir, name).replace("\\", "/")
            if p not in known:
                pending += 1
        items.append(_item(
            "pending_photos", "photos/ 待入库",
            STATUS_WARN if pending else STATUS_OK, pending,
            detail=(f"{len(files)} 张照片中 {pending} 张未入库" if pending
                    else f"{len(files)} 张照片全部已入库"),
            fix="「待处理」页 → 扫描/分析新照片" if pending else ""))

    # ---- 缓存：失效条目 ----
    try:
        from core.analysis_cache import AnalysisCache
        n = AnalysisCache(cache_file=analysis_cache_file).stale_count()
        items.append(_item(
            "stale_analysis_cache", "分析缓存",
            STATUS_WARN if n else STATUS_OK, n,
            detail=(f"{n} 条指向已删除照片" if n else "无失效条目"),
            fix="设置中心「🧹 清理失效缓存」" if n else ""))
    except Exception as e:
        items.append(_item("stale_analysis_cache", "分析缓存", STATUS_WARN,
                           detail=f"读取失败：{str(e)[:60]}"))
    try:
        from core.visual_duplicates import VisualDuplicateIndex
        vi = VisualDuplicateIndex(photos_dir=photos_dir,
                                  index_path=visual_index_path)
        n = vi.stale_count()
        items.append(_item(
            "stale_visual_fingerprints", "视觉指纹缓存",
            STATUS_WARN if n else STATUS_OK, n,
            detail=(f"{n} 条指向已删除照片（共 {len(vi._files)} 条）" if n
                    else f"{len(vi._files)} 条全部有效"),
            fix="设置中心「🧹 清理失效缓存」" if n else ""))
    except Exception as e:
        items.append(_item("stale_visual_fingerprints", "视觉指纹缓存", STATUS_WARN,
                           detail=f"读取失败：{str(e)[:60]}"))

    # ---- 视觉搜索索引 ----
    try:
        from core.visual_search.index import read_index_status
        st = read_index_status(cache_dir=visual_search_cache_dir,
                               photos_dir=photos_dir)
        state = st.get("state")
        gap = max(0, int(st.get("photos_total") or 0) - int(st.get("indexed") or 0))
        if state == "ready" and gap == 0:
            status, detail = STATUS_OK, f"已索引 {st['indexed']}/{st['photos_total']} 张"
        elif state == "missing":
            status, detail = STATUS_OK, "尚未建立（首次语义搜索时自动构建）"
        else:
            status = STATUS_WARN
            detail = f"状态 {state}：已索引 {st.get('indexed', 0)}/{st.get('photos_total', 0)}"
        items.append(_item("visual_search_index", "语义搜索索引", status, gap,
                           detail=detail,
                           fix="设置中心「🧠 更新视觉索引」" if status == STATUS_WARN else ""))
    except Exception as e:
        items.append(_item("visual_search_index", "语义搜索索引", STATUS_WARN,
                           detail=f"读取失败：{str(e)[:60]}"))

    # ---- 兽装分析环境（Fursee worker 前置：只查路径，不启动进程）----
    try:
        from core.identity.fursee_adapter import FurseeAdapterConfig
        cfg = FurseeAdapterConfig()
        py_ok = os.path.isfile(cfg.python_exe)
        wk_ok = os.path.isfile(cfg.worker_path)
        env_dir = os.path.dirname(cfg.python_exe)
        detail = (f"worker 解释器 {'就绪' if py_ok else '缺失'} · "
                  f"worker 脚本 {'就绪' if wk_ok else '缺失'}"
                  f"（{env_dir}）")
        ok_env = py_ok and wk_ok
        items.append(_item(
            "fursee_env", "兽装分析环境",
            STATUS_OK if ok_env else STATUS_WARN, 0,
            detail=detail,
            fix=("未找到 fursee_test 解释器或 worker 脚本：兽装照片无法完成入库，"
                 "请确认 conda 环境含 torch(cu128) + ultralytics")
            if not ok_env else ""))
    except Exception as e:
        items.append(_item("fursee_env", "兽装分析环境", STATUS_WARN,
                           detail=f"检查失败：{str(e)[:60]}"))

    # ---- 数据备份新鲜度（库里是人工整理成果，值得提醒）----
    try:
        backup_root = os.path.join(root, "backups")
        newest_ts = None
        if os.path.isdir(backup_root):
            for dirpath, _dirs, filenames in os.walk(backup_root):
                for name in filenames:
                    if not name.endswith((".sqlite", ".db")):
                        continue
                    try:
                        ts = os.path.getmtime(os.path.join(dirpath, name))
                    except OSError:
                        continue
                    if newest_ts is None or ts > newest_ts:
                        newest_ts = ts
        try:
            db_ts = os.path.getmtime(db_path)
        except OSError:
            db_ts = None
        if newest_ts is None:
            items.append(_item(
                "backup_freshness", "数据备份", STATUS_WARN, 0,
                detail="还没有任何数据库备份",
                fix="设置中心「💾 立即备份」或开启自动备份"))
        else:
            age_days = int((time.time() - newest_ts) / 86400)
            stale = age_days > 14
            db_newer = bool(db_ts and db_ts > newest_ts + 3600)
            detail = f"最近备份 {age_days} 天前"
            if db_newer:
                detail += "，之后库又有改动（尚无对应备份）"
            items.append(_item(
                "backup_freshness", "数据备份",
                STATUS_WARN if (stale or db_newer) else STATUS_OK, age_days,
                detail=detail,
                fix="设置中心「💾 立即备份」" if (stale or db_newer) else ""))
    except Exception as e:
        items.append(_item("backup_freshness", "数据备份", STATUS_WARN,
                           detail=f"读取失败：{str(e)[:60]}"))

    # ---- 云同步残留（百度网盘会在被同步目录里写 .cfg 占位文件）----
    try:
        git_hits = other_hits = refs_hits = 0
        for dirpath, dirnames, filenames in os.walk(root):
            if ".venv" in dirnames:
                dirnames.remove(".venv")      # 依赖目录不统计，避免拖慢体检
            norm_dir = (dirpath + os.sep).replace("\\", "/")
            in_git = "/.git/" in norm_dir
            in_refs = in_git and "/refs/" in norm_dir
            for name in filenames:
                if "baiduyun" in name and ("uploading.cfg" in name
                                           or "downloading" in name):
                    if in_git:
                        git_hits += 1
                        if in_refs:
                            refs_hits += 1
                    else:
                        other_hits += 1
        total_sync = git_hits + other_hits
        detail = (f"{total_sync} 个同步临时文件（.git 内 {git_hits} 个）"
                  if total_sync else "没有云同步残留文件")
        if refs_hits:
            detail += (f"——其中 {refs_hits} 个在 .git/refs，"
                       "已导致 git fsck/后台维护报错")
        elif git_hits:
            detail += "——.git 被同步可能干扰 git 操作"
        items.append(_item(
            "sync_pollution", "云同步残留",
            STATUS_WARN if total_sync else STATUS_OK, total_sync,
            detail=detail,
            fix=("把 .git / .venv / cache 排除出百度网盘同步目录；"
                 "确认同步完成后再删除这些 .cfg 临时文件")
            if total_sync else ""))
    except Exception as e:
        items.append(_item("sync_pollution", "云同步残留", STATUS_WARN,
                           detail=f"扫描失败：{str(e)[:60]}"))

    # ---- 完全重复的照片（MD5，可选：需要读盘）----
    if include_duplicates:
        try:
            from core.duplicates import DuplicateScanner
            groups = DuplicateScanner(photos_dir=photos_dir).scan()
            n = sum(len(g["paths"]) - 1 for g in groups)
            items.append(_item(
                "duplicate_photos", "完全重复照片",
                STATUS_WARN if n else STATUS_OK, n,
                detail=(f"{len(groups)} 组 / 多出 {n} 个副本" if n else "没有完全重复的照片"),
                fix="「重复照片」页可保留一张、清理其余" if n else ""))
        except Exception as e:
            items.append(_item("duplicate_photos", "完全重复照片", STATUS_WARN,
                               detail=f"扫描失败：{str(e)[:60]}"))

    return {
        "items": items,
        "warnings": sum(1 for i in items if i["status"] == STATUS_WARN),
        "errors": sum(1 for i in items if i["status"] == STATUS_ERROR),
        "ok_count": sum(1 for i in items if i["status"] == STATUS_OK),
    }


def format_report(result):
    """把体检结果格式化为多行文本（UI/CLI 通用）。"""
    lines = []
    for it in (result or {}).get("items") or []:
        head = f"{status_icon(it['status'])} {it['label']}：{it['detail']}"
        if it.get("fix"):
            head += f"（{it['fix']}）"
        lines.append(head)
    warn = (result or {}).get("warnings", 0)
    err = (result or {}).get("errors", 0)
    tail = ("全部正常" if not warn and not err
            else f"待处理 {warn} 项" + (f" · 异常 {err} 项" if err else ""))
    lines.append(f"—— 体检结论：{tail}")
    return "\n".join(lines)
