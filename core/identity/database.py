# core/identity/database.py
"""
身份识别与聚合模块 - 数据库层 V4
统一 SQLite 持久化。

数据库文件：项目根目录/identity_db.sqlite

表结构（schema v2，PRAGMA user_version = 2）：
    identity_group  - 身份分组（真人/兽装角色）
    identity_image  - 身份-图片关联（含 embedding）

schema v2 要点（P-C4-B 迁移引入）：
    identity_image.image_path 不再有列级 UNIQUE，
    改为复合唯一键 UNIQUE(image_path, detection_index)。

    一张照片可以有多个 detection（如同一照片中 F 与 P 两个角色）：
        photo.jpg + detection_index=0 → 独立一行（各自 embedding/group）
        photo.jpg + detection_index=1 → 独立一行（各自 embedding/group）

    旧数据（schema v1）全部映射 detection_index = 0。
    打开 v1 旧库时 _migrate_schema_v2() 会自动执行单事务迁移。
"""

import sqlite3
import json
import uuid
import numpy as np
from pathlib import Path
from datetime import datetime


DB_FILE = str(Path(__file__).resolve().parents[2] / "identity_db.sqlite")


class IdentityDatabase:
    """身份数据库管理"""

    def __init__(self, db_path=None):
        self.db_path = db_path or DB_FILE
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=OFF")
        self._create_tables()
        self._migrate_add_columns()
        self._migrate_schema_v2()

    def _create_tables(self):
        # schema v2：detection_index + 复合唯一键 UNIQUE(image_path, detection_index)
        # 与 P-C4-B 已迁移的 identity_db.sqlite 保持一致（新建库直接得到 v2）
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS identity_group (
                id          TEXT PRIMARY KEY,
                name        TEXT DEFAULT '',
                type        TEXT DEFAULT '',
                description TEXT DEFAULT '',
                cover_image TEXT DEFAULT '',
                created_at  TEXT DEFAULT '',
                updated_at  TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS identity_image (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id       TEXT NOT NULL DEFAULT '',
                image_path     TEXT NOT NULL,
                detection_index INTEGER NOT NULL DEFAULT 0,
                embedding      BLOB,
                embedding_type TEXT DEFAULT '',
                bbox           TEXT DEFAULT '',
                layer1_category TEXT DEFAULT '',
                confidence     REAL DEFAULT 0.0,
                added_at       TEXT DEFAULT '',
                UNIQUE(image_path, detection_index)
            );

            CREATE INDEX IF NOT EXISTS idx_identity_image_group
                ON identity_image(group_id);
            CREATE INDEX IF NOT EXISTS idx_identity_image_path
                ON identity_image(image_path);
            CREATE INDEX IF NOT EXISTS idx_identity_group_type
                ON identity_group(type);

            -- 收藏（照片级，UI Phase 3-1）：image_path 主键天然防重复，
            -- 不关联 detection/embedding/character_id，不影响角色归属。
            CREATE TABLE IF NOT EXISTS favorite_image (
                image_path TEXT PRIMARY KEY,
                created_at TEXT DEFAULT ''
            );
        """)
        self.conn.commit()

    def _migrate_add_columns(self):
        migrations = [
            ("identity_image", "layer1_category", "TEXT DEFAULT ''"),
            ("identity_image", "confidence", "REAL DEFAULT 0.0"),
        ]
        for table, column, col_type in migrations:
            try:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            except sqlite3.OperationalError:
                pass
        self.conn.commit()

    def _migrate_schema_v2(self):
        """v1 -> v2 迁移（幂等，单事务，与 P-C4-B 迁移脚本逻辑一致）。

        v1（image_path 列级 UNIQUE）→ v2（detection_index + 复合唯一键）。
        旧数据全部 detection_index = 0，其余字段原值保留。
        已是 v2 的库此方法为 no-op（仅确保 user_version >= 2）。
        """
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(identity_image)")]
        if cols and "detection_index" not in cols:
            self.conn.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE identity_image_v2 (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id       TEXT NOT NULL DEFAULT '',
                    image_path     TEXT NOT NULL,
                    detection_index INTEGER NOT NULL DEFAULT 0,
                    embedding      BLOB,
                    embedding_type TEXT DEFAULT '',
                    bbox           TEXT DEFAULT '',
                    layer1_category TEXT DEFAULT '',
                    confidence     REAL DEFAULT 0.0,
                    added_at       TEXT DEFAULT '',
                    UNIQUE(image_path, detection_index)
                );
                INSERT INTO identity_image_v2
                    (id, group_id, image_path, detection_index, embedding,
                     embedding_type, bbox, layer1_category, confidence, added_at)
                SELECT id, group_id, image_path, 0, embedding,
                       embedding_type, bbox, layer1_category, confidence, added_at
                FROM identity_image;
                DROP TABLE identity_image;
                ALTER TABLE identity_image_v2 RENAME TO identity_image;
                CREATE INDEX idx_identity_image_group ON identity_image(group_id);
                CREATE INDEX idx_identity_image_path ON identity_image(image_path);
                COMMIT;
            """)
            self.conn.commit()
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 2:
            self.conn.execute("PRAGMA user_version = 2")
            self.conn.commit()

    # ============================================================
    # identity_group CRUD
    # ============================================================

    def create_group(self, name="", group_type="", description="", cover_image=""):
        group_id = str(uuid.uuid4())[:8]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            """INSERT INTO identity_group (id, name, type, description, cover_image, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (group_id, name, group_type, description, cover_image, now, now)
        )
        self.conn.commit()
        return group_id

    def get_group(self, group_id):
        row = self.conn.execute(
            "SELECT * FROM identity_group WHERE id = ?", (group_id,)
        ).fetchone()
        if row is None:
            return None
        cols = ["id", "name", "type", "description", "cover_image", "created_at", "updated_at"]
        return self._row_to_dict(row, cols)

    def get_all_groups(self, group_type=None):
        if group_type:
            rows = self.conn.execute(
                "SELECT * FROM identity_group WHERE type = ? ORDER BY created_at DESC",
                (group_type,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM identity_group ORDER BY created_at DESC"
            ).fetchall()
        cols = ["id", "name", "type", "description", "cover_image", "created_at", "updated_at"]
        return [self._row_to_dict(r, cols) for r in rows]

    def update_group(self, group_id, **kwargs):
        allowed = {"name", "description", "cover_image"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [group_id]
        self.conn.execute(f"UPDATE identity_group SET {set_clause} WHERE id = ?", values)
        self.conn.commit()

    def delete_group(self, group_id):
        self.conn.execute("DELETE FROM identity_image WHERE group_id = ?", (group_id,))
        self.conn.execute("DELETE FROM identity_group WHERE id = ?", (group_id,))
        self.conn.commit()

    def merge_group_members(self, target_id, source_ids):
        """将源组的 detection 级成员转入目标组并删除源组。

        只更新 identity_image.group_id，不重新写入 embedding 或元数据，
        因而会原样保留 image_path、detection_index、bbox、confidence、
        embedding 和 embedding_type。数据库 schema 不变。
        """
        target_id = str(target_id or "").strip()
        source_ids = list(dict.fromkeys(
            str(group_id or "").strip() for group_id in (source_ids or [])
        ))
        source_ids = [group_id for group_id in source_ids if group_id]
        if not target_id:
            raise ValueError("target_id 不能为空")
        if target_id in source_ids:
            raise ValueError("target_id 不能同时作为 source_id")
        if not source_ids:
            return {"target_id": target_id, "source_ids": [], "moved": 0}

        placeholders = ",".join("?" for _ in source_ids)
        with self.conn:
            target = self.conn.execute(
                "SELECT id, type, name, cover_image FROM identity_group WHERE id = ?",
                (target_id,),
            ).fetchone()
            if target is None:
                raise ValueError(f"目标角色组不存在：{target_id}")

            source_rows = self.conn.execute(
                f"SELECT id, type FROM identity_group WHERE id IN ({placeholders})",
                source_ids,
            ).fetchall()
            found_ids = {row[0] for row in source_rows}
            missing = [group_id for group_id in source_ids if group_id not in found_ids]
            if missing:
                raise ValueError(f"源角色组不存在：{', '.join(missing)}")
            mismatched = [
                row[0] for row in source_rows
                if row[1] != target[1]
            ]
            if mismatched:
                raise ValueError("只能合并相同类型的角色组")

            target_types = {
                row[0] for row in self.conn.execute(
                    "SELECT DISTINCT embedding_type FROM identity_image "
                    "WHERE group_id = ? AND embedding_type <> ''",
                    (target_id,),
                ).fetchall()
            }
            source_types = {
                row[0] for row in self.conn.execute(
                    f"SELECT DISTINCT embedding_type FROM identity_image "
                    f"WHERE group_id IN ({placeholders}) AND embedding_type <> ''",
                    source_ids,
                ).fetchall()
            }
            if target_types and source_types and target_types != source_types:
                raise ValueError(
                    "Legacy 与 Fursee 角色组不能直接合并，请保持两类数据分离"
                )

            moved = self.conn.execute(
                f"UPDATE identity_image SET group_id = ? "
                f"WHERE group_id IN ({placeholders})",
                [target_id, *source_ids],
            ).rowcount

            self.conn.execute(
                f"DELETE FROM identity_group WHERE id IN ({placeholders})",
                source_ids,
            )

            cover_image = target[3]
            if not cover_image:
                cover_row = self.conn.execute(
                    "SELECT image_path FROM identity_image "
                    "WHERE group_id = ? ORDER BY added_at ASC, id ASC LIMIT 1",
                    (target_id,),
                ).fetchone()
                cover_image = cover_row[0] if cover_row else ""

            self.conn.execute(
                "UPDATE identity_group SET cover_image = ?, updated_at = ? "
                "WHERE id = ?",
                (cover_image, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), target_id),
            )

        return {
            "target_id": target_id,
            "source_ids": source_ids,
            "moved": moved,
        }

    def get_group_image_count(self, group_id):
        """组内照片数（按 image_path 去重）。

        schema v2 起同一照片可能有多行（多 detection），
        这里按 DISTINCT(image_path) 计数以保持既有 GUI 语义
        （count = 照片数，D4 决策）：一张照片多个 detection 不会被
        计成多张照片。当前存量数据 path 均唯一，返回值与 v1 一致。
        """
        row = self.conn.execute(
            "SELECT COUNT(DISTINCT image_path) FROM identity_image WHERE group_id = ?",
            (group_id,)
        ).fetchone()
        return row[0] if row else 0

    # ============================================================
    # identity_image CRUD
    # ============================================================

    def add_image(self, group_id, image_path, embedding=None,
                  embedding_type="", bbox=None, layer1_category="", confidence=0.0,
                  detection_index=0):
        """upsert 一条 identity_image 记录（schema v2：按复合键查重）。

        唯一键为 (image_path, detection_index)：
          - photo.jpg + detection_index=0 与 photo.jpg + detection_index=1
            可以同时存在（同一照片多个角色/检测目标）。
          - 同一 (image_path, detection_index) 再次写入时保持原有
            UPDATE/upsert 语义（更新既有行，不产生第二行）。

        向后兼容：旧调用方不传 detection_index 时默认为 0，
        行为与 v1（image_path 唯一）时代完全一致。
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        embedding_blob = embedding.astype(np.float32).tobytes() if embedding is not None else None
        bbox_str = json.dumps(list(bbox)) if bbox else ""

        existing = self.conn.execute(
            "SELECT id FROM identity_image WHERE image_path = ? AND detection_index = ?",
            (image_path, detection_index)
        ).fetchone()

        if existing:
            self.conn.execute(
                """UPDATE identity_image
                   SET group_id=?, embedding=?, embedding_type=?, bbox=?,
                       layer1_category=?, confidence=?, added_at=?
                   WHERE image_path=? AND detection_index=?""",
                (group_id, embedding_blob, embedding_type, bbox_str,
                 layer1_category, confidence, now, image_path, detection_index)
            )
        else:
            self.conn.execute(
                """INSERT INTO identity_image
                   (group_id, image_path, detection_index, embedding, embedding_type,
                    bbox, layer1_category, confidence, added_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (group_id, image_path, detection_index, embedding_blob, embedding_type,
                 bbox_str, layer1_category, confidence, now)
            )

        cover = self.get_group_cover(group_id)
        if not cover:
            self.update_group(group_id, cover_image=image_path)
        self.conn.commit()

    def get_images_by_group(self, group_id):
        rows = self.conn.execute(
            """SELECT id, group_id, image_path, detection_index, embedding,
                      embedding_type, bbox, layer1_category, confidence, added_at
               FROM identity_image WHERE group_id = ? ORDER BY added_at DESC""",
            (group_id,)
        ).fetchall()
        cols = ["id", "group_id", "image_path", "detection_index", "embedding",
                "embedding_type", "bbox", "layer1_category", "confidence", "added_at"]
        return [self._row_to_dict(r, cols) for r in rows]

    def get_all_embeddings(self, embedding_type=None, include_detection_index=False):
        """读取全部（或指定类型）非空 embedding。

        schema v2 起一个 image_path 可能对应多行（多 detection），
        每行都会作为独立记录返回。

        返回结构（保持旧调用方兼容）：
          - 默认（include_detection_index=False）：
            [(image_path, embedding, embedding_type, group_id), ...]
            与 v1 时代完全一致（cluster.py 等旧调用方零改动）。
          - include_detection_index=True：
            [(image_path, detection_index, embedding, embedding_type, group_id), ...]
            供 P-C4-C1 起需要区分同照片多 detection 的新调用方使用。

        兼容性说明：旧调用方如果假设"一个 image_path = 一个 embedding"，
        在数据库出现多 detection 行之后该假设不再成立（同一 path 会
        返回多条记录）；当前所有存量数据 detection_index 均为 0，
        行为与迁移前完全一致。
        """
        if include_detection_index:
            select_cols = "image_path, detection_index, embedding, embedding_type, group_id"
        else:
            select_cols = "image_path, embedding, embedding_type, group_id"

        if embedding_type:
            rows = self.conn.execute(
                f"""SELECT {select_cols}
                   FROM identity_image
                   WHERE embedding IS NOT NULL AND embedding_type = ?""",
                (embedding_type,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                f"""SELECT {select_cols}
                   FROM identity_image WHERE embedding IS NOT NULL"""
            ).fetchall()

        results = []
        for row in rows:
            if include_detection_index:
                path, det_idx, blob, etype, gid = row
                emb = np.frombuffer(blob, dtype=np.float32)
                results.append((path, det_idx, emb, etype, gid))
            else:
                path, blob, etype, gid = row
                emb = np.frombuffer(blob, dtype=np.float32)
                results.append((path, emb, etype, gid))
        return results

    def get_group_cover(self, group_id):
        row = self.conn.execute(
            "SELECT image_path FROM identity_image WHERE group_id = ? ORDER BY added_at ASC LIMIT 1",
            (group_id,)
        ).fetchone()
        return row[0] if row else None

    def remove_image(self, image_path, detection_index=None):
        """删除照片的身份记录。

        - detection_index=None（默认，旧行为）：删除该照片的全部
          detection 行（整张照片移出身份库）。
        - detection_index=N：只删除该照片第 N 个 detection 的那一行，
          其余 detection 保留（P-C4 多角色支持）。
        """
        if detection_index is None:
            self.conn.execute(
                "DELETE FROM identity_image WHERE image_path = ?", (image_path,)
            )
        else:
            self.conn.execute(
                "DELETE FROM identity_image WHERE image_path = ? AND detection_index = ?",
                (image_path, detection_index)
            )
        self.conn.commit()

    # ============================================================
    # 收藏（UI Phase 3-1，照片级；不动角色/detection/embedding）
    # ============================================================

    def add_favorite(self, image_path):
        """收藏一张照片（image_path 主键，重复收藏幂等）。"""
        self.conn.execute(
            "INSERT OR IGNORE INTO favorite_image (image_path, created_at) VALUES (?, ?)",
            (image_path, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        self.conn.commit()

    def remove_favorite(self, image_path):
        """取消收藏。"""
        self.conn.execute(
            "DELETE FROM favorite_image WHERE image_path = ?", (image_path,)
        )
        self.conn.commit()

    def list_favorites(self):
        """全部收藏照片路径（按收藏时间倒序）。"""
        rows = self.conn.execute(
            "SELECT image_path FROM favorite_image ORDER BY created_at DESC"
        ).fetchall()
        return [r[0] for r in rows]

    def is_favorite(self, image_path):
        """是否已收藏。"""
        row = self.conn.execute(
            "SELECT 1 FROM favorite_image WHERE image_path = ?", (image_path,)
        ).fetchone()
        return row is not None

    def _row_to_dict(self, row, columns):
        return {col: row[i] for i, col in enumerate(columns)}

    def close(self):
        self.conn.close()
