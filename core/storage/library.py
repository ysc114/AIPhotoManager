"""Unified photo library — single entry point for multi-backend access.

``PhotoLibrary`` composes one or more ``StorageBackend`` instances and
exposes a flat, backend-agnostic API. The AI pipeline (later stages)
only ever talks to this class.

For the simple one-folder case, use ``LocalPhotoLibrary`` from
``core.storage.local`` instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Iterator

from .base import StorageBackend
from .exceptions import (
    BackendAlreadyRegisteredError,
    BackendNotAvailableError,
    PhotoNotFoundError,
)
from .index import PhotoIndex, RecordStatus
from .models import PhotoInfo

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    """Statistics returned by ``PhotoLibrary.scan()``."""
    total_scanned: int = 0
    new_photos: int = 0
    updated_photos: int = 0
    unchanged_photos: int = 0
    errors: list[str] = field(default_factory=list)
    backends_scanned: list[str] = field(default_factory=list)


class PhotoLibrary:
    """Unified photo library across multiple storage backends."""

    def __init__(self, index_path: str | Path | None = None) -> None:
        """
        Args:
            index_path: SQLite database path for persistent incremental
                        scanning. ``None`` (default) = in-memory.
        """
        self._backends: dict[str, StorageBackend] = {}
        self._index = PhotoIndex(index_path)

    # ------------------------------------------------------------------ #
    #  Backend management                                                #
    # ------------------------------------------------------------------ #

    def add_backend(self, backend: StorageBackend) -> None:
        if backend.name in self._backends:
            raise BackendAlreadyRegisteredError(
                f"Backend '{backend.name}' is already registered"
            )
        self._backends[backend.name] = backend
        logger.info(
            "Registered backend '%s' (%s)", backend.name, type(backend).__name__
        )

    def remove_backend(self, name: str) -> None:
        if name not in self._backends:
            return
        del self._backends[name]
        deleted = self._index.clear_backend(name)
        logger.info("Removed backend '%s' (%d photos purged)", name, deleted)

    def get_backend(self, name: str) -> StorageBackend | None:
        return self._backends.get(name)

    @property
    def backend_names(self) -> list[str]:
        return list(self._backends.keys())

    # ------------------------------------------------------------------ #
    #  Scanning                                                          #
    # ------------------------------------------------------------------ #

    def scan(self, force: bool = False) -> ScanResult:
        """Scan all registered backends and update the index.

        Args:
            force: If ``True``, treat every photo as new (re-index).
                   Defaults to ``False`` (incremental).
        """
        result = ScanResult()

        for name, backend in self._backends.items():
            result.backends_scanned.append(name)

            if not backend.is_available:
                msg = f"Backend '{name}' is not available — skipped"
                logger.warning(msg)
                result.errors.append(msg)
                continue

            try:
                for info in backend.list_photos():
                    result.total_scanned += 1

                    if force:
                        self._index.upsert(info)
                        result.new_photos += 1
                    else:
                        status = self._index.upsert(info)
                        if status is RecordStatus.NEW:
                            result.new_photos += 1
                        elif status is RecordStatus.UPDATED:
                            result.updated_photos += 1
                        else:
                            result.unchanged_photos += 1

            except Exception as exc:  # noqa: BLE001
                msg = f"Error scanning backend '{name}': {exc}"
                logger.error(msg, exc_info=True)
                result.errors.append(msg)

        logger.info(
            "Scan complete — scanned=%d  new=%d  updated=%d  unchanged=%d  errors=%d",
            result.total_scanned, result.new_photos,
            result.updated_photos, result.unchanged_photos,
            len(result.errors),
        )
        return result

    # ------------------------------------------------------------------ #
    #  Querying                                                          #
    # ------------------------------------------------------------------ #

    def get_photo(self, photo_id: str) -> PhotoInfo | None:
        return self._index.get(photo_id)

    def iter_photos(self) -> Iterator[PhotoInfo]:
        yield from self._index.list_all()

    def iter_photos_by_backend(self, backend_name: str) -> Iterator[PhotoInfo]:
        yield from self._index.list_by_backend(backend_name)

    def photo_count(self) -> int:
        return self._index.count()

    def photo_count_by_backend(self, backend_name: str) -> int:
        return self._index.count_by_backend(backend_name)

    # ------------------------------------------------------------------ #
    #  Reading                                                           #
    # ------------------------------------------------------------------ #

    def open(self, photo_id: str) -> BinaryIO:
        """Open a photo by its ``photo_id``.

        Raises:
            PhotoNotFoundError: photo_id not in index.
            BackendNotAvailableError: backend removed or offline.
        """
        info = self._index.get(photo_id)
        if info is None:
            raise PhotoNotFoundError(f"Photo ID not in index: {photo_id}")

        backend = self._backends.get(info.ref.backend_name)
        if backend is None:
            raise BackendNotAvailableError(
                f"Backend '{info.ref.backend_name}' is not registered"
            )
        return backend.open(info.ref)

    def read_bytes(self, photo_id: str) -> bytes:
        with self.open(photo_id) as f:
            return f.read()

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                         #
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        self._index.close()
