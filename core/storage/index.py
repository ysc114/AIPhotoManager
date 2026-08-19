"""Persistent photo index backed by SQLite.

Enables incremental scanning: only photos that changed (size/mtime)
are treated as new by upper layers.
"""

from __future__ import annotations

import sqlite3
import threading
from enum import Enum
from pathlib import Path
from typing import Iterator

from .models import PhotoInfo, PhotoRef


class RecordStatus(Enum):
    """Result of an index upsert."""
    NEW = "new"                # not in index before
    UPDATED = "updated"        # existed but size/mtime changed
    UNCHANGED = "unchanged"    # identical size/mtime


_SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    photo_id     TEXT PRIMARY KEY,
    backend_name TEXT NOT NULL,
    path         TEXT NOT NULL,
    size         INTEGER NOT NULL,
    modified_time REAL NOT NULL,
    created_time REAL,
    width        INTEGER,
    height       INTEGER,
    indexed_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_backend ON photos(backend_name);
CREATE INDEX IF NOT EXISTS idx_path ON photos(backend_name, path);
"""


class PhotoIndex:
    """SQLite-backed photo index for persistence and fast lookup."""

    def __init__(self, db_path: str | Path | None = None):
        """
        Args:
            db_path: SQLite database file path. ``None`` = in-memory.
        """
        self._db_path = str(db_path) if db_path else ":memory:"
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def upsert(self, info: PhotoInfo) -> RecordStatus:
        """Insert or update a photo. Returns its record status."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT size, modified_time FROM photos WHERE photo_id = ?",
                (info.photo_id,),
            )
            row = cur.fetchone()
            if row is not None:
                if (row["size"] == info.size
                        and row["modified_time"] == info.modified_time):
                    return RecordStatus.UNCHANGED
                status = RecordStatus.UPDATED
            else:
                status = RecordStatus.NEW

            self._conn.execute(
                """INSERT INTO photos
                       (photo_id, backend_name, path, size,
                        modified_time, created_time, width, height)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(photo_id) DO UPDATE SET
                       size = excluded.size,
                       modified_time = excluded.modified_time,
                       created_time = excluded.created_time,
                       width = excluded.width,
                       height = excluded.height,
                       indexed_at = datetime('now')
                """,
                (info.photo_id, info.ref.backend_name, info.ref.path,
                 info.size, info.modified_time, info.created_time,
                 info.width, info.height),
            )
            self._conn.commit()
            return status

    def get(self, photo_id: str) -> PhotoInfo | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM photos WHERE photo_id = ?", (photo_id,)
            )
            row = cur.fetchone()
            return self._row_to_info(row) if row else None

    def list_all(self) -> Iterator[PhotoInfo]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM photos ORDER BY backend_name, path"
            )
            for row in cur:
                yield self._row_to_info(row)

    def list_by_backend(self, backend_name: str) -> Iterator[PhotoInfo]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM photos WHERE backend_name = ? ORDER BY path",
                (backend_name,),
            )
            for row in cur:
                yield self._row_to_info(row)

    def delete(self, photo_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM photos WHERE photo_id = ?", (photo_id,)
            )
            self._conn.commit()
            return cur.rowcount > 0

    def count(self) -> int:
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM photos")
            return cur.fetchone()[0]

    def count_by_backend(self, backend_name: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM photos WHERE backend_name = ?",
                (backend_name,),
            )
            return cur.fetchone()[0]

    def clear_backend(self, backend_name: str) -> int:
        """Remove all photos of one backend. Returns count deleted."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM photos WHERE backend_name = ?", (backend_name,)
            )
            self._conn.commit()
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row_to_info(row: sqlite3.Row) -> PhotoInfo:
        ref = PhotoRef(backend_name=row["backend_name"], path=row["path"])
        return PhotoInfo(
            ref=ref,
            size=row["size"],
            modified_time=row["modified_time"],
            created_time=row["created_time"],
            width=row["width"],
            height=row["height"],
        )
