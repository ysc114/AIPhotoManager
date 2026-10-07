# AIPhotoManager 项目记忆（AI 会话维护）

> 规则：每次创建 / 修改 / 删除文件后立即更新本文件；新会话开始先核对索引与实际文件，不一致就补全。

## 一、仓库与远端

| 项 | 值 |
|---|---|
| 本地路径 | `C:\Users\33466\Desktop\同步\AIPhotoManager` |
| 远端 | `git@github.com:ysc114/AIPhotoManager.git`（分支 `main`） |
| 当前远端 HEAD | `8b381f7`（2026-10-07 推送） |
| ⚠️ 位置风险 | 仓库位于**百度网盘同步目录**内，`.git` 会被网盘上传流程改名（见下） |

## 二、2026-10-07 上传前抢修记录

| 事项 | 详情 |
|---|---|
| 故障 | `.git` 与工作区出现 **578 个** `*.baiduyun.uploading.cfg`（网盘加密容器，非 Git 对象）；其中 `refs/heads/main.baiduyun.uploading.cfg` 触发 `fatal: bad object`，导致 fetch/push 失败 |
| 处置 | 578 个标记文件全部移出仓库 → 备份于 `C:\Users\33466\Desktop\baiduyun-git-markers-backup\20261007-204943\`（保留相对路径结构，可回滚；578 文件 / 0.26 MB） |
| 验证 | `git fsck` exit=0（仅 110 dangling blob，无 missing/broken）；`git fetch` exit=0；`git push` 成功 `c871662..8b381f7` |
| 数据完整性 | 源码与照片库完整；仅 `photos/未标题-2.png` 缺失（此前"移除示例照片"提交所致）；48 个 `.git` 游离对象丢失但**不被任何引用**，无影响 |

## 三、⚠️ 已知风险（待用户决策）

1. **网盘同步会再次破坏 `.git`**：建议在百度网盘客户端将 `.git` 加入同步排除，或把仓库移出 `同步` 目录——否则本故障会复发。
2. **隐私数据已被 git 跟踪**（`.gitignore` 规则对已跟踪文件无效）：
   - `photos/` 190 个文件（私人照片库）
   - `identity_db.sqlite`（身份数据库）
   - 处置选项：① `git rm --cached` 停止后续跟踪（历史仍含旧数据）；② 重写历史彻底清除（风险高，需备份）；③ 维持现状。

## 四、故意未提交的产物

- 根目录 `final_extra_tests.xml`、`final_keyboard_retest.xml`、`final_nonvisual_tests.xml`、`final_ui_retest.xml`、`final_visual_tests.xml`：pytest 报告，**故意不入库**（如需归档再单独提交）。
