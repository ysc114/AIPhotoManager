"""
AIPhotoManager 设置管理（SettingsManager）

独立配置层：所有设置以 JSON 存于 config/settings.json，
UI / AI / 扫描 / 备份模块统一通过 SettingsManager 读写，
避免把配置逻辑散落在 main_window_v3.py。

- get/set 支持点号路径（如 "ui.mode" / "ai.fursuit_threshold"）
- load() 递归合并默认值，缺失键自动补默认
- save() 原子写（临时文件 + os.replace），避免写坏配置
- 模块级单例 settings，供各模块直接导入使用
"""

import json
import os
import tempfile
import weakref
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent
DEFAULT_PATH = CONFIG_DIR / "settings.json"

# ============================================================
# 默认配置（唯一事实来源）
# ============================================================
DEFAULT_SETTINGS = {
    "version": 1,

    # ── 🖥️ 界面 ────────────────────────────────────────────
    "ui": {
        "mode": "new",            # "new"=新版界面 / "classic"=经典版界面
        "theme": "system",        # "system" / "light" / "dark"
        "liquid_glass": True,     # Liquid Glass 玻璃效果
        "glass_opacity": 0.55,    # 玻璃透明度（0.30~0.90，越小越透明）
        "glass_blur": 30,         # 模糊/柔和强度（1~60，阴影与弥散）
        "corner_radius": 18,      # 卡片圆角（8~28 px）
        "thumb_radius": 14,       # 缩略图圆角（4~24 px）
        "shadow_strength": 40,    # 卡片阴影强度（0~80）
        "animation": True,        # 界面动画
        "animation_speed": 1.0,   # 动画速度/强度（0.5~2.0）
        "thumbnail_size": 138,    # 照片墙缩略图边长
        "grid_columns": 6,        # 每行照片数量
        "rounded_photos": True,   # 圆角照片
        "show_photo_info": True,  # 显示照片信息
    },

    # ── 🧠 AI 识别 ──────────────────────────────────────────
    "ai": {
        "l1_classifier": "clip",
        "fursuit_engine": "fursee",   # 兽装识别引擎
        "face_engine": "face",        # 人物识别引擎
        "fursuit_threshold": 0.79,    # Fursee 匹配阈值（P-C4-C3 定稿）
        "face_threshold": 0.92,       # Face 匹配阈值
        "md5_dedup": True,            # MD5 内容去重
        "fursee_eps": 0.6481,         # 高级：Fursee eps（euclidean）
        "min_samples": 1,             # 高级：min_samples
        "metric": "euclidean",        # 高级：距离度量
        "embedding_type": "fursuit_fursee",  # 高级：embedding 类型
    },

    # ── 📡 照片扫描 ─────────────────────────────────────────
    "scan": {
        "auto_scan_photos": True,     # 扫描新照片
        "auto_detect_fursuit": True,  # 自动识别兽装
        "auto_detect_person": True,   # 自动识别人物
        "skip_duplicates": True,      # 跳过重复照片
        "md5_dedup": True,            # MD5 内容去重
    },

    # ── 📂 存储 ─────────────────────────────────────────────
    "storage": {
        "photos_dir": "",             # 空 = 项目默认 photos/
        "data_dir": "",               # 空 = 项目根
        "cache_dir": "",              # 空 = 项目根（analysis_cache.json 所在）
        "backup_dir": "backups",      # 相对项目根
    },

    # ── 💾 数据与备份 ───────────────────────────────────────
    "backup": {
        "auto_backup": False,         # 自动备份
        "frequency": "daily",         # "daily" / "weekly" / "manual"
        "keep_count": 7,              # 保留备份数量
    },

    # ── 🔔 通知 ─────────────────────────────────────────────
    "notifications": {
        "analysis_done": True,
        "scan_done": True,
        "new_character": True,
        "error": True,
    },

    # ── 🔍 AI 数据管理 ──────────────────────────────────────
    "data": {
        "clean_invalid_cache": True,  # 清理无效缓存（空 {} 条目等）
    },

    # ── ✨ Aurora 极光 ──────────────────────────────────────
    "aurora": {
        "enabled": True,          # 极光总开关（关闭后卡片退化为普通玻璃，零动态开销）
        "intensity": 0.55,        # 极光强度（0.0~1.0，光晕颜色浓度）
        "speed": 1.0,             # 流动速度（0.2~3.0，淡入淡出与内部流动速度）
        "color_mode": "auto",     # 颜色模式："auto"自动 / "soft"柔和 / "vivid"彩色
        "blur": 0.6,              # 光晕模糊/柔和度（0.0~1.0）
        "radius": 0.6,            # 光晕范围（0.3~1.2，扩散半径系数）
        "follow": 0.8,            # 鼠标跟随强度（0.0~1.0，0=不跟随固定位置）
        "smoothing": 0.6,         # 跟随平滑度（0.0~1.0，越大惯性/延迟越大）
        "opacity": 0.85,          # 极光整体透明度（0.0~1.0）
        "hover_lift": 0.5,        # Hover 浮起幅度（0.0~1.0，0=不浮起）
        "light_count": 3,         # 渐变光源数量（2~5）
    },

    # ── 🧊 Liquid Glass 折射层（pyglass vendor 引擎）────────────
    "glass": {
        "enabled": True,          # Liquid Glass 总开关（关闭后恢复普通玻璃；pyglass 不可用时自动忽略）
        "thickness": 0.35,        # 玻璃厚度 / 折射强度（0.0~1.0，越大边缘弯曲/色散越明显）
        "frost": 0.25,            # 玻璃模糊 / Frost（0.0~1.0，越大越磨砂）
        "opacity": 0.35,          # 玻璃透明度（0.0~1.0，影响内部色调强度）
        "mouse_follow": True,     # 鼠标跟随：hover 时实时折射跟随（False=进入时折射一次）
    },

    # ── 🧭 底部导航（液态玻璃导航栏）─────────────────────────
    "nav": {
        "show_text": True,            # 显示文字（窄窗口自动只显示图标）
        "animation": True,            # 液态动画开关
        "animation_strength": 1.0,    # 动画强度（0.5~2.0）
        "liquid_effect": "standard",  # 液态效果："standard"标准 / "soft"柔和 / "vivid"明显
    },

    # ── 🛠️ 高级设置 ─────────────────────────────────────────
    "advanced": {
        "debug_mode": False,
        "log_level": "INFO",          # DEBUG / INFO / WARNING / ERROR
        "cache_strategy": "balanced", # "aggressive" / "balanced" / "conservative"
    },
}


class SettingsManager:
    """设置读写管理器（JSON 存储，点号路径访问，带变更通知）。"""

    def __init__(self, path=None):
        self.path = Path(path) if path else DEFAULT_PATH
        self._data = {}
        self._listeners = {}   # key 前缀 -> [callback(key, value), ...]
        self.load()

    # --------------------------------------------------------
    # 读写
    # --------------------------------------------------------
    def load(self):
        """从磁盘加载，与默认值递归合并（缺失键补默认）。"""
        self._data = json.loads(
            json.dumps(DEFAULT_SETTINGS)  # 深拷贝默认
        )
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self._merge(self._data, loaded)
            except (json.JSONDecodeError, OSError) as e:
                print(f"[settings] 加载失败，使用默认配置: {e}")
        return self

    def save(self):
        """原子写入 settings.json（临时文件 + os.replace）。

        写盘失败（如目录被同步/占用）时不抛异常：内存已更新，下次
        set/save 时重试；避免设置页因持久化失败而崩溃。
        """
        os.makedirs(self.path.parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix="settings_", suffix=".json", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError as e:
            print(f"[settings] 保存失败: {e}")
            try:
                os.remove(tmp)
            except OSError:
                pass
        return True

    def get(self, key, default=None):
        """按点号路径读取，如 settings.get('ui.mode')。"""
        node = self._data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, key, value, save=True):
        """按点号路径写入，如 settings.set('ui.mode', 'classic')。

        写入后通知监听者（key 自身及所有点号前缀），供 UI 即时刷新。
        """
        parts = key.split(".")
        node = self._data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
        if save:
            self.save()
        self._notify(key, value)
        return value

    def set_many(self, updates, save=True):
        """批量写入：{ 'ui.mode': 'classic', ... }。"""
        for k, v in updates.items():
            self.set(k, v, save=False)
        if save:
            self.save()
        return True

    # --------------------------------------------------------
    # 变更通知
    # --------------------------------------------------------
    def on_change(self, key_prefix, callback):
        """注册变更监听：key 或其点号前缀等于 key_prefix 时回调(key, value)。

        例如监听 "aurora" 会收到 aurora.enabled / aurora.intensity 等所有
        aurora.* 变更；监听 "aurora.intensity" 只收到该键变更。
        返回 callback 本身（便于 off_change 注销）。

        bound method 回调以「弱引用 + 方法名」存储：监听对象被垃圾回收后
        自动移除，不会因回调持有对象引用造成泄漏（幽灵回调）。
        """
        entry = self._make_entry(callback)
        self._listeners.setdefault(key_prefix, []).append(entry)
        return callback

    @staticmethod
    def _make_entry(callback):
        import inspect
        if inspect.ismethod(callback):
            return (weakref.ref(callback.__self__), callback.__name__)
        return callback

    def off_change(self, key_prefix, callback):
        """注销变更监听（幂等）。"""
        cbs = self._listeners.get(key_prefix)
        if not cbs:
            return
        entry = self._make_entry(callback)
        try:
            cbs.remove(entry)
        except ValueError:
            pass
        if not cbs:
            self._listeners.pop(key_prefix, None)

    def _notify(self, key, value):
        """按 key 及其前缀通知监听者（弱引用条目自动清理）。"""
        parts = key.split(".")
        prefixes = [".".join(parts[:i]) for i in range(1, len(parts) + 1)]
        for prefix in prefixes:
            cbs = self._listeners.get(prefix)
            if not cbs:
                continue
            for entry in tuple(cbs):
                try:
                    if isinstance(entry, tuple):
                        owner = entry[0]()
                        if owner is None:
                            cbs.remove(entry)   # 对象已回收 → 自动移除
                            continue
                        getattr(owner, entry[1])(key, value)
                    else:
                        entry(key, value)
                except Exception as e:  # 监听回调异常不影响设置写入
                    print(f"[settings] 监听回调异常 ({prefix}): {e}")

    # --------------------------------------------------------
    # 工具
    # --------------------------------------------------------
    def get_all(self):
        return json.loads(json.dumps(self._data))

    def to_dict(self):
        return self.get_all()

    @staticmethod
    def _merge(base, override):
        """递归合并：override 覆盖 base，缺失键保留 base 默认。"""
        for k, v in override.items():
            if (
                k in base
                and isinstance(base[k], dict)
                and isinstance(v, dict)
            ):
                SettingsManager._merge(base[k], v)
            else:
                base[k] = v


# 模块级单例：各模块直接 `from config.settings_manager import settings`
settings = SettingsManager()
