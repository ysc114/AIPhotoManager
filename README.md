# AIPhotoManager V4

AI 照片管理系统：本地 + NAS 照片管理，AI 自动分类、兽装/人物角色识别与分组、多人合照归属、角色照片墙与完整原图预览。

> 本项目是个人照片库的**生产系统**，代码经过多轮生产环境验证。开发时请严格遵守文末「开发铁律」。
> 隐私声明：照片、数据库、模型权重均被 `.gitignore` 排除，**仓库中不含任何私人数据**。

---

## 目录

- [一、功能概览](#一功能概览)
- [二、系统架构](#二系统架构)
- [三、目录结构](#三目录结构)
- [四、环境与运行](#四环境与运行)
- [五、数据处理链路](#五数据处理链路)
- [六、数据库设计](#六数据库设计)
- [七、聚类与增量分配](#七聚类与增量分配)
- [八、Fursee 兽装识别](#八fursee-兽装识别)
- [九、测试](#九测试)
- [十、开发铁律（必读）](#十开发铁律必读)
- [十一、Git 提交规范](#十一git-提交规范)

---

## 一、功能概览

- **照片管理**：本地 photos/ 目录扫描、入库、预览（完整原图）；**首次进入照片页自动载入 photos/ 全库**（列表已有内容或已载入过则不动，实测 194 张 ~0.18s，缩略图后台补齐）
- **AI 分类**：L1 粗分类（CLIP）→ 路由（`fursuit` 兽装 / `person` 人物 / `None` 其他）
- **Fursee 兽装识别**：YOLO 主体检测 + 512D 归一化 embedding（独立 worker 进程）
- **角色分组**：DBSCAN 聚类 + **Incremental Assignment 增量分配**（新照片只加入/新建，不拆散已有组）
- **多人合照归属**：同一照片多个 detection 各自独立归组（`(image_path, detection_index)` 复合键）
- **人工合并角色**：UI 一键合并，永久保留（不因后续聚类被拆散）
- **疑似同一角色**（角色中心 2.0 · 第二阶段）：跨 Fursee 组相似候选（只读比较，不改 0.79/eps）→ 人工确认才合并；"不是同一角色"判定与最近合并快照存 JSON sidecar（不改 schema），支持撤销最近一次合并
- **疑似重复照片**（路线图 ②）：视觉相似检测（dHash + 直方图 + 灰度相关性，区别于 MD5 的"内容完全一致"）——连拍/构图相似/轻微糊/曝光不同；AI 只推荐，人工「保留此张/忽略该组」，绝不自动删除（待清理仅标记，落盘 JSON 缓存复用）；**防误删不变量**：任何被「保留」的照片永不进待清理列表，保留照片已不在磁盘时该组建议作废，删除入口再过一层「保留」过滤
- **相似照片搜索**（路线图 ③）：选一张照片 → 复用视觉指纹返回库内最相似的 N 张（同场景/同角色/连拍），纯只读
- **收藏**：照片页「⭐ 收藏当前」按**当前预览照片**收藏/取消（普通浏览与角色墙/收藏跳转都可用），按钮文案跟随状态（已收藏 → 「★ 已收藏（点击取消）」）；收藏页网格支持点击预览完整原图与右键取消；只写 `favorite_image` 表，不触碰角色/detection/embedding
- **全局搜索面板**（路线图 ④ 第一阶段）：Spotlight 风格悬浮搜索（Ctrl+K / Ctrl+Shift+F），分区结果（最近搜索/角色/照片/收藏，标签/文件预留）；**筛选与顶部搜索条统一**：类型（全部/兽装/人物，作用于角色）与「⭐ 只看收藏」（作用于照片/语义，开启时照片并入收藏分区避免重复展示）；组件化 `ui/components/global_search.py`，只发信号，数据与跳转由 MainWindow 决定
- **以图搜图**（智能搜索第 2 层 · 第一阶段）：独立 OpenCLIP 视觉 Embedding + FAISS 索引（`core/visual_search/`，`cache/visual_search/`）；GPU/CPU 自适应、L2 归一化、增量建索引（已索引复用 / MD5 去重）、模型版本校验（换模型不混用）；索引读写走内存序列化（**中文路径安全**：faiss 自带路径式 I/O 在含中文目录下会失败）、**分批落盘（中断可续建）**；照片页「🔎 查找相似照片」
- **自然语言搜索**（第 2 层 · 第二阶段）：同一 CLIP 空间文本 embedding（`encode_text`）→ `search_by_text`；Spotlight 全局搜索面板新增 **🧠 语义（CLIP）** 分区（索引为空时后台自动构建并在完成后自动刷新结果）；**首次加载模型后台预热**（不阻塞界面，完成后自动刷新结果），权重**离线优先**（`HF_HUB_OFFLINE`，避免网络受限时重试 15s+；需要联网下载新模型设 `VISUAL_SEARCH_ALLOW_DOWNLOAD=1`）
- **数据体检（只读）**：设置中心「🩺 运行体检」一屏检查 12 项——数据库完整性 / 库内照片文件 / 未分配 detection / 角色组照片可用性 / photos 待入库 / 分析缓存 / 视觉指纹缓存 / 语义搜索索引 / 完全重复照片 / 云同步残留（百度网盘 .cfg 占位文件，含 .git 内计数）/ 数据备份新鲜度（最近备份超过 14 天、或备份之后库又有改动 → 提示一键备份）/ 兽装分析环境（fursee_test 解释器与 worker 脚本是否就绪，只查路径不启进程）；只 stat+查库+读 JSON，不加载 AI 模型（实测全库 0.3s），每项给出结论与建议动作（`core/health_check.py`）；能自动修的项目配一键入口（清理失效缓存 / 去待处理页入库 / 更新视觉索引 / 去重复照片页 / 立即备份），无问题时自动置灰；**启动后 2.5s 后台自动体检一次**（约 0.2s，只读），有 warning/error 时在状态栏提示，正常则完全静默；**关窗时统一回收后台线程**（体检/索引/相似搜索等），避免退出期原生崩溃
- **失效缓存清理**：设置中心「🧹 清理失效缓存」——删除分析缓存与视觉指纹中指向**已删除照片**的条目（只清缓存键，绝不删照片文件；人工分类与「不是同一角色/保留」判定保持不变），AI 数据统计行会显示当前失效条数
- **索引自动维护**：分析/扫描新照片入库完成后，后台增量补齐视觉搜索索引（设置中心可关：`data.auto_update_visual_index`，失败只提示不弹窗）；设置中心「AI 数据」显示索引状态（已索引/总数/模型/更新时间）并提供「更新/重建视觉索引」入口（重建需二次确认，仅删搜索缓存）
- **整理命名（逐个命名未命名角色）**：角色中心工具栏「🏷 整理命名（N）」→ 逐个显示未命名角色的 detection 裁剪封面 + 稳定序号 + 照片数，回车=保存并下一个、可跳过、随时结束；只调用既有 `update_name`，不合并、不动 detection/聚类（`ui/naming_walkthrough.py`）
- **角色显示名（稳定序号）**：未命名角色统一显示为「未命名兽装角色 #007」（同类型内按 created_at 倒序编号，最新为 #001，按 type 分区），卡片 / 照片墙 / 搜索 / 合照跳转菜单共用同一规则（`core/identity/naming.py`）；序号仅用于显示，库里不落新字段——已命名角色始终优先显示用户起的名字
- **角色照片墙**：组内按 `(path, det_idx)` 去重；**卡片封面**用 detection bbox 裁剪（不同角色封面可区分），**详情页显示完整原图**（多人合照在多个角色页重复出现，瓦片右上角标注「合照 ×N 个角色」，**点击角标可直接跳到同框的其他角色**，只读不动归属数据；照片页新增「🎭 同框角色」按钮，可从任意照片反向查看并跳转到它所属的角色）。整图缩略图与 bbox 无关 → 同一张合照在多个角色页共享同一份缩略图缓存
- **缩略图优化**：`QImageReader.setClipRect` 先裁后缩 + EXIF 旋转映射，小主体不再模糊

---

## 二、系统架构

```
photos/ (本地 / NAS)
   │
   ▼
Storage ───────────────► 文件扫描、路径解析（local / SMB）
   │
   ▼
AIClassifier ───────────► CLIP L1 分类（analysis_cache.json 缓存）
   │
   ▼
IdentityEmbedding ──────► route_l1: fursuit / person / None
   │                        ├─ fursuit → FurseeAdapter（worker 进程）
   │                        │           → YOLO detection + 512D embedding
   │                        └─ person  → insightface 人脸 embedding（可选）
   │
   ▼
IdentityDatabase ────────► identity_image / identity_group（SQLite v2）
   │
   ▼
IdentityCluster ─────────► ① DBSCAN 聚类（首次/全量）
   │                       ② incremental_assign（增量：只加不拆）
   ▼
IdentityManager ─────────► get_groups / merge_groups / analyze_new_photos
   │
   ▼
MainWindow (PySide6) ────► 总览 / 照片 / 兽装 / 人物 / 角色 / 收藏 / 待处理 / 设置
```

关键设计：

- **UI 不承担业务逻辑**：所有数据操作走 `IdentityManager` / `IdentityDatabase`
- **主进程与 Fursee worker 分离**：主进程（Python 3.10 + PySide6）经 stdin/stdout JSON 与 worker（Python 3.12 + torch cu128）通信；worker stdout 只允许过协议 JSON，杜绝日志污染（B2 修复）
- **GPU 推理只在 Windows 本机**，NAS 只做存储

---

## 三、目录结构

```
AIPhotoManager/
├── main.py                     # 入口（PySide6 MainWindow）
├── core/
│   ├── identity/               # 身份识别与聚类（生产核心）
│   │   ├── database.py         # SQLite v2：identity_image / identity_group
│   │   ├── manager.py          # IdentityManager 门面 + get_reader() 共享只读
│   │   ├── cluster.py          # DBSCAN（仅显式全量）+ incremental_assign
│   │   ├── naming.py           # 未命名角色稳定序号显示名（只读）
│   │   ├── suspects.py         # 疑似同一角色候选 + 人工判定 sidecar
│   │   ├── embedding.py        # L1 分类路由 + CLIP/YOLO 旧链路
│   │   └── fursee_adapter.py / fursee_worker.py   # GPU worker（conda fursee_test）
│   ├── visual_search/          # CLIP + FAISS 视觉/语义检索（中文路径安全）
│   ├── photo_quality/          # 画质评分 / 近似分组 / AI 精选
│   ├── storage/                # 存储抽象（local / smb_backend / index）
│   ├── duplicates.py           # MD5 完全重复：大小预筛扫描 + 安全删除
│   ├── visual_duplicates.py    # 视觉相似指纹 + 人工判定 sidecar
│   ├── thumbnail_cache.py      # 256/512 磁盘缩略图（2 线程 + 退出兜底）
│   ├── search_index.py         # 角色/照片关键字索引（筛选语义单一来源）
│   ├── health_check.py         # 12 项只读数据/环境体检
│   ├── qt_threads.py           # QThread 回收助手（reap_thread）
│   ├── analysis_cache.py       # 分析缓存（JSON，可显式指定路径）
│   ├── ai_classifier.py / ai_organizer.py / model_hub.py
│   └── labels.py 等
├── ui/
│   ├── main_window_v3.py       # 主窗口组装（页面方法拆到 mixin）
│   ├── overview_mixin.py       # 总览/预览/跳转/相册墙
│   ├── role_center_mixin.py    # 角色中心（卡片墙/筛选/详情/命名入口）
│   ├── favorites_mixin.py      # 收藏页
│   ├── settings_center.py      # 设置中心（含数据体检/一键修复）
│   ├── duplicates_page.py      # 重复照片页（懒扫描）
│   ├── naming_walkthrough.py   # 「🏷 整理命名」逐个命名对话框
│   ├── bottom_nav.py / search_bar.py / aurora_card.py
│   ├── components/             # Liquid Glass 组件 + Spotlight 搜索面板
│   └── vendor/pyglass/         # 折射引擎（vendor）
├── tests/                      # 39 个测试文件 / 338 项（temp 隔离，不碰生产）
├── tools/                      # 手工探针（probe_clip / probe_ai_florence，非测试）
├── config/                     # settings_manager.py / labels.py
├── backups/                    # 生产库备份（git 忽略）
└── cache/                      # 缩略图 + 视觉搜索索引（git 忽略）
```

## 四、环境与运行

### 依赖

| 组件 | 环境 | 说明 |
|---|---|---|
| 主程序 | Python 3.10 | PySide6 / numpy / scikit-learn / Pillow / torch(CUDA 可选) |
| Fursee worker | conda `fursee`（Python 3.12.13）| torch cu128 + transformers==**5.14.1**（唯一兼容版；4.56 会静默随机初始化）+ ultralytics |
| 人脸（可选） | Python 3.10 | insightface + onnxruntime（`buffalo_l` 模型） |

### 运行

```bash
cd AIPhotoManager
C:/Program Files/Python310/python.exe main.py
```

> ⚠️ 只从**项目根目录**运行（`core` 的模块导入依赖 cwd）；不要在嵌套副本/旧快照目录运行。

### Fursee worker 环境变量

- `HF_HUB_OFFLINE=1`：离线加载模型，避免联网卡死
- worker 使用独立 `YOLO_CONFIG_DIR`（启动前预初始化），防止 ultralytics "Creating new Ultralytics Settings" 警告污染协议通道

---

## 五、数据处理链路

### 新照片入库（推荐：增量安全路径）

```
photos/ 新文件
   → IdentityManager.analyze_new_photos()
   → ① path 查重（identity_image 中已存在 → 跳过）
   → ② MD5 内容级去重（与已入库图片内容相同的 (1) 副本 → 跳过）
   → ③ _process_single_image()：L1 分类 → route_l1
   → ④ fursuit → _process_fursuit_fursee() → FurseeAdapter.analyze()
   → ⑤ 每个 detection 独立写一行（det_index/bbox/conf/512D，group_id=''）
   → ⑥ cluster.incremental_assign(threshold=0.79, margin=0.02)
```

### 角色分组

- **DBSCAN 全量聚类**（`cluster.run(embedding_type=...)`）：仅限明确的全量重建场景
  - `fursuit_fursee`: eps=0.6481, metric=euclidean, min_samples=1
  - `fursuit_visual`（旧 CLIP）: eps=0.3 —— **冻结不动**
  - `face`: eps=0.4
- **Incremental Assignment**（`cluster.incremental_assign`）：新增照片的默认路径，**不重跑 DBSCAN**
  - 只处理 `group_id=''` 的未分配行
  - 与已有组代表（归一化 centroid）逐组比较 cosine
  - `max_cos ≥ 0.79` → 加入最高相似度组
  - 最高与次高差距 `< 0.02` → 保守不合并（防歧义，保持未分配待人工）
  - 全部 `< 0.79` → 创建新角色组
  - **已有行的 group_id 零改动**（人工合并关系永久保留）

### 阈值依据（P-C4-C3 人眼终审）

```
cosine_threshold = 0.79
eps = sqrt(2*(1-0.79)) = 0.6481   （L2 归一化下 cos = 1 - L2²/2）
metric = euclidean, min_samples = 1
```

> ⚠️ 0.79 是**定稿值，不要自行调整**。降低阈值会错误合并不同角色（已有实证教训）。

---

## 六、数据库设计

文件：`identity_db.sqlite`（schema v2，`PRAGMA user_version = 2`）

### identity_image（每个 detection 一行）

```sql
CREATE TABLE identity_image (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id        TEXT NOT NULL DEFAULT '',   -- 所属角色组 id（''=未分配）
    image_path      TEXT NOT NULL,              -- 照片绝对路径（正斜杠）
    detection_index INTEGER NOT NULL DEFAULT 0, -- 照片内第几个检测（0 起）
    embedding       BLOB,                       -- 512D float32
    embedding_type  TEXT DEFAULT '',            -- fursuit_fursee / fursuit_visual / face
    bbox            TEXT DEFAULT '',            -- JSON [x1,y1,x2,y2] 绝对像素
    layer1_category TEXT DEFAULT '',
    confidence      REAL DEFAULT 0.0,
    added_at        TEXT DEFAULT '',
    UNIQUE(image_path, detection_index)
);
```

### identity_group（角色组，`id` 即 character_id）

```sql
CREATE TABLE identity_group (
    id          TEXT PRIMARY KEY,   -- uuid，即 character_id（update_name 操作此列）
    name        TEXT DEFAULT '',
    type        TEXT DEFAULT '',    -- fursuit_character / real_person / ...
    description TEXT DEFAULT '',
    cover_image TEXT DEFAULT '',
    created_at  TEXT DEFAULT '',
    updated_at  TEXT DEFAULT ''
);
```

要点：

- **角色 = 组**：没有独立 character_id 列，`identity_group.id` 就是 character_id
- **一图多角色**：同一 `image_path` 可有多行（不同 `detection_index`），各行可归入不同组
- **合并角色**：`merge_group_members(target, sources)` 只 UPDATE 成员行的 `group_id`，保留全部其他字段（embedding/bbox/conf/...），删除源组行

---

## 七、聚类与增量分配

```python
# 全量聚类（仅明确重建时使用，会重建全部组）
cluster.run(embedding_type="fursuit_fursee")   # 显式定向 ✅
cluster.run(None)                              # ❌ 已禁止，直接抛 ValueError

# 增量分配（新照片默认路径）
cluster.incremental_assign(embedding_type="fursuit_fursee", threshold=0.79, margin=0.02)
```

`incremental_assign` 语义（对应 `core/identity/cluster.py`）：

| 场景 | 行为 |
|---|---|
| 新 det 与某已有组 max_cos ≥ 0.79 | `UPDATE group_id` 加入该组 |
| 最高与次高差 < 0.02 | 保守：不自动分配（记录 conflicts，待人工）|
| 全部 < 0.79 | `create_group()` 新建角色组 |
| 已有行 | **零 UPDATE**（只读入组代表）|

**为什么不能全量重聚**：DBSCAN 会对全部行重分配 group_id 并删除空组，会**拆散用户人工合并的组**（C1~C8 等）。`analyze_folder()` 曾因此破坏过生产库——已修复为增量路径。

---

## 八、Fursee 兽装识别

- `fursee_worker.py`：子进程，加载 YOLO（ultralytics）+ 兽装 embedding 模型
- `fursee_adapter.py`：主进程侧适配器
  - 启动 worker → 等待就绪 → 逐图分析
  - stdin/stdout JSON 协议（`{"op":"analyze","path":...}` → `{"bbox":[...],"confidence":...,"embedding":[...]}`）
  - 超时熔断 / 崩溃重试 / 协议错误分类（`FurseeError` 家族）
- worker 输出必须是**纯协议 JSON**，所有日志走 `log()` 到 stderr 或独立 logger

> 注意：Python 3.10 系统环境**无 CUDA**（torch 2.13.0+cpu）。Fursee GPU 推理在 conda `fursee` 环境；主进程分析走 CPU 亦可。

---

## 九、测试

```bash
# 全量（temp 库隔离，不碰生产库；UI 测试需 offscreen）
QT_QPA_PLATFORM=offscreen C:/Program Files/Python310/python.exe -m unittest discover -s tests -p "test_*.py"
```

| 分组 | 代表文件 | 覆盖 |
|---|---|---|
| 身份与聚类 | `test_incremental_cluster.py`、`test_no_full_recluster.py`、`test_detection_aware_identity.py`、`test_suspect_pairs.py`、`test_role_naming.py` | 增量分配 8 项（加入/新建/冲突/同图多 det/人工合并保护/幂等/边界）、禁止全量重聚、detection 复合键与 merge 保真、疑似候选与撤销、稳定显示名与序号 |
| 重复与相似 | `test_duplicates.py`、`test_visual_duplicates.py`、`test_visual_duplicates_ui.py` | MD5 分组 / 安全删除 / 大小预筛、视觉指纹与决策持久化、页面冒烟（只标记不删除）|
| 检索与索引 | `test_visual_search.py`、`test_visual_index_ui.py`、`test_global_search.py`、`test_global_search_filters.py`、`test_search_dock.py`、`test_search_filters.py` | CLIP/FAISS（含模型，约 52s：中文路径持久化 / 中断续建 / 临时文件清理 / 旧格式升级）、索引自动增量与重建、Spotlight 与筛选、顶部搜索条与 Dock |
| 角色与照片 UI | `test_character_page.py`、`test_character_center.py`、`test_detection_aware_ui.py`、`test_photo_wall_dedup.py`、`test_photo_list_restore.py`、`test_naming_walkthrough.py` | 角色页与详情墙（完整原图 + 合照角标 + 跳转）、筛选排序、去重、列表快照与「返回全部」、整理命名 |
| 界面与视觉 | `test_components.py`、`test_liquid_glass.py`、`test_aurora_config.py`、`test_bottom_nav.py`、`test_phase3_ui.py`、`test_favorites.py` | 组件库、Liquid Glass、极光配置、10 项底部 Dock、收藏与设置页 |
| 性能与运维 | `test_performance.py`、`test_thumbnail_cache.py`、`test_thumb_primitive.py`、`test_health_check.py`、`test_cache_prune.py`、`test_overview_stats.py`、`test_qt_threads.py`、`test_settings_manager.py` | 6 条性能护栏、缩略图缓存与取图原语、12 项数据体检、失效缓存清理、总览统计口径、QThread 回收（含关窗等待主窗口与子页面线程）、设置持久化 |
| 其它 | `test_analyze_paths.py`、`test_scan_new_photos.py`、`test_ai_classifier_cache.py`、`test_photo_quality.py`、`test_legacy_visibility.py` | 分析路径与扫描、缓存命中语义、画质评分、旧数据可见性（连生产库，慎跑）|

> ⚠️ `test_legacy_visibility.py` 使用无参 `IdentityManager()`（连生产库），CI/他人环境运行前请确认或跳过。

### 测试规模（2026-09-25）

`tests/` 共 **39 个测试文件 / 367 项**，全绿（运行结束进程退出码 0）；其中 2 个文件需要加载模型
（`test_visual_search` 约 52s、`test_character_center` 约 48s），其余文件合计约 2 分钟。

### 性能基线（2026-09-25 实测，offscreen，194 张照片）

| 指标 | 实测 |
|---|---|
| 主窗口构造 | ~0.31s（端到端冒烟复测） |
| 启动到首帧（构造 + show + 8 帧事件循环） | ~0.65s |
| 启动后台体检完成（含状态栏提示） | ~0.92s |
| 总览统计刷新（首次 / 热） | 0.04s / 0.01s |
| 待处理页统计（热；冷启动含首次 import） | 0.02s / ~2s |
| 全库重复扫描 `DuplicateScanner.scan()` | 0.02s |
| 入库去重全库 MD5 统计（真实图库 167 张 / 198MB） | 首次 0.42s、二次 0.014s（原每次整库读盘 0.38s） |
| 照片列表填充（全库 162 张，缓存优先 + 后台补图） | ~0.11s（改前 5.5s） |
| 收藏页瓦片渲染（50 张，缓存优先 + 后台补图） | ~0.06s（改前 2.2s） |
| 照片页首次自动载入全库（194 张） | ~0.18s（图标 1.2s 内后台补齐） |
| 角色页 221 组卡片全量渲染（分批，不阻塞） | ~1.4s |
| 常驻内存 RSS | ~136MB |

性能护栏（`tests/test_performance.py`，2026-09-25 收紧）：启动可交互 ≤2.5s、角色页加载 ≤0.6s、搜索索引 ≤0.3s、全库 MD5 扫描 ≤0.5s、照片列表填充 ≤1.0s、收藏瓦片 50 张 ≤1.0s。

关键惰性点（改动前都是启动期同步开销）：

- 重复照片页：`auto_scan=False`，**首次进入页面才扫描**（MD5 + 指纹变化检查）
- 设置页：Git commit / Schema 版本**首次进入设置页才读取**（原来构造时 spawn git 子进程）
- `sklearn` 仅在「显式全量重建聚类」路径导入（默认禁用路径），不再拖慢启动后首刷
- 只读查询统一走 `get_reader()` 共享连接；写操作仍新建实例（见开发铁律）
- 页面清空（`_clear_grid`）只对本次删除的对象派发 DeferredDelete：传 `None` 会连带处理其他页面遗留的待删对象，offscreen 下曾导致主线程死锁

---

## 十、开发铁律（必读）

1. **AI 找候选 → 人工确认 → 才 merge**：不自动决定两个角色是否相同；候选需提供 bbox crop 对比图给人眼确认
2. **不降低聚类阈值**：0.79/0.6481 是定稿值；错误合并 > 允许过分裂
3. **增量 > 全量**：新增照片一律走 `incremental_assign`；`cluster.run(None)` 已禁止；`analyze_folder` 尾部已是增量
4. **保护人工合并**：已有 `group_id` / `character_id` 不得被聚类重写；合并关系永久保留
5. **一图多角色独立处理**：必须按 `(image_path, detection_index)`，禁止按 path 跨角色去重
6. **旧数据隔离**：`fursuit_visual`（旧 CLIP 29 行）冻结，任何链路不得重聚/修改
7. **UI 不承担业务逻辑**：数据操作一律走 Manager/DB
8. **只读诊断优先**：写生产库前先备份（`backups/phase_xxx_YYYYMMDD/` + baseline SHA256）；破坏性操作先出方案
9. **生产库位置**：`identity_db.sqlite` 在项目根（真实路径以部署环境为准）；分析缓存 `analysis_cache.json` 的空 `{}` 条目视为无效（会重分析）
10. **路径规范**：库内 `image_path` 为正斜杠绝对路径；不要混入反斜杠（缓存 key 匹配会失效）

---

## 十一、Git 提交规范

- 提交前 `git status` 检查，**只 add 明确文件**，不 `git add .`
- `.gitignore` 已排除：`photos/`、`*.sqlite`、`analysis_cache.json`、`feedback.json`、`*.pt/*.onnx`、`backups/`、`.scratch_5b2/`、`__pycache__/`
- 提交身份：`ysc114 <ysc114@users.noreply.github.com>`（GitHub 隐私邮箱）
- 示例提交信息：

```
增量聚类+去重修复+测试套件

- cluster.py: 新增 incremental_assign ...
- manager.py: analyze_new_photos 改走 incremental_assign ...
- tests: 新增 test_incremental_cluster(8) / ...
```
