# AIPhotoManager V4
最终要完成的项目
现在还未完成只开发到：本地、AI 自动分类、角色照片墙与完整原图预览
AI 照片管理系统：本地 + NAS 照片管理，AI 自动分类、多人合照归属、角色照片墙与完整原图预览。

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

- **照片管理**：本地 photos/ 目录扫描、入库、预览（完整原图）
- **AI 分类**：L1 粗分类（CLIP）→ 路由（`fursuit` 兽装 / `person` 人物 / `None` 其他）
- **Fursee 兽装识别**：YOLO 主体检测 + 512D 归一化 embedding（独立 worker 进程）
- **角色分组**：DBSCAN 聚类 + **Incremental Assignment 增量分配**（新照片只加入/新建，不拆散已有组）
- **多人合照归属**：同一照片多个 detection 各自独立归组（`(image_path, detection_index)` 复合键）
- **人工合并角色**：UI 一键合并，永久保留（不因后续聚类被拆散）
- **角色照片墙**：组内按 `(path, det_idx)` 去重，bbox 主体裁剪作为卡片封面
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
├── main.py                  # 入口（PySide6 MainWindow）
├── core/
│   ├── storage/             # 存储抽象（local / smb_backend / index）
│   ├── identity/
│   │   ├── database.py      # SQLite v2：identity_image / identity_group
│   │   ├── embedding.py     # L1 分类 + 路由 + CLIP/YOLO 旧链路
│   │   ├── cluster.py       # DBSCAN + incremental_assign（增量分配）
│   │   ├── manager.py       # IdentityManager 门面（生产入口）
│   │   ├── fursee_adapter.py# Fursee worker 子进程适配器（协议/熔断/重试）
│   │   └── fursee_worker.py # Fursee worker：YOLO 检测 + 512D embedding
│   ├── ai_classifier.py     # CLIP L1 分类 + 缓存读写
│   ├── ai_organizer.py      # 「AI智能整理」批量入口
│   ├── model_hub.py         # 模型单例共享（CLIP/YOLO/insightface）
│   └── analysis_cache.py    # 分析缓存（JSON）
├── ui/
│   └── main_window_v3.py    # 主界面（Phase 2.5：detection 级展示）
├── tests/                   # 单元/集成测试（temp 库隔离，不碰生产）
├── config/labels.py         # 分类标签文案
├── backups/                 # 各阶段生产库备份（git 忽略）
└── .scratch_5b2/            # 诊断/实验产物（git 忽略）
```

---

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

| 测试文件 | 覆盖 |
|---|---|
| `test_incremental_cluster.py` | 增量分配 8 项（加入/新建/冲突/同图多det/人工合并保护/visual隔离/幂等/阈值边界）|
| `test_no_full_recluster.py` | run(None) 抛错 / 定向 run / analyze_folder 不拆组 / 幂等 / face 增量 |
| `test_detection_aware_identity.py` | merge 保留字段 / schema v2 / Legacy-Fursee 隔离 |
| `test_detection_aware_ui.py` | 照片墙复合键 / bbox 裁剪渲染（offscreen）|
| `test_legacy_visibility.py` | get_groups 过滤（连接生产库，慎跑）|
| `test_ai_classifier_cache.py` | 缓存命中（None/{}→重分析，有效→命中）|

> ⚠️ `test_legacy_visibility.py` 使用无参 `IdentityManager()`（连生产库），CI/他人环境运行前请确认或跳过。

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
