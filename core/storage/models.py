"""Data models for the storage layer.

All virtual paths within backends use POSIX-style separators (/)
regardless of the host OS, so keys stay consistent across
Windows and Linux.

``abs_path`` carries the *real* absolute path in native OS format
(e.g. ``C:\\Photos\\a.jpg``). It is only filled by local filesystem
backends and exists so that legacy code (AI pipeline, analysis cache,
identity database) can keep using absolute paths unchanged.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import PurePosixPath

# Extensions treated as photos when scanning.
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({
    "jpg", "jpeg", "png", "webp", "heic", "heif", "bmp", "tiff", "tif",
})


class PhotoFormat(Enum):
    """Logical photo format (maps multiple extensions to one value)."""
    JPEG = "jpeg"
    PNG = "png"
    WEBP = "webp"
    HEIC = "heic"
    BMP = "bmp"
    TIFF = "tiff"


_EXTENSION_MAP: dict[str, PhotoFormat] = {
    "jpg": PhotoFormat.JPEG,
    "jpeg": PhotoFormat.JPEG,
    "png": PhotoFormat.PNG,
    "webp": PhotoFormat.WEBP,
    "heic": PhotoFormat.HEIC,
    "heif": PhotoFormat.HEIC,
    "bmp": PhotoFormat.BMP,
    "tiff": PhotoFormat.TIFF,
    "tif": PhotoFormat.TIFF,
}


@dataclass(frozen=True)
class PhotoRef:
    """Immutable reference to a photo in a storage backend.

    ``backend_name`` + ``path`` form a composite key that uniquely
    identifies a photo across the whole library. ``path`` is always
    POSIX-style (forward slashes) and relative to the backend root.
    """

    backend_name: str
    path: str

    @property
    def photo_id(self) -> str:
        """Stable 16-char hex ID derived from backend_name + path."""
        raw = f"{self.backend_name}:{self.path}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @property
    def filename(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def extension(self) -> str:
        return PurePosixPath(self.path).suffix.lstrip(".").lower()

    @property
    def format(self) -> PhotoFormat | None:
        return _EXTENSION_MAP.get(self.extension)

    @property
    def is_supported(self) -> bool:
        return self.extension in SUPPORTED_EXTENSIONS

    def __str__(self) -> str:
        return f"{self.backend_name}://{self.path}"


@dataclass
class PhotoInfo:
    """Metadata about a scanned photo.

    ``width`` / ``height`` are optional — filled by the AI pipeline
    in later stages, not by the storage layer.
    """

    ref: PhotoRef
    size: int               # bytes
    modified_time: float    # unix timestamp (mtime)
    created_time: float | None = None
    width: int | None = None
    height: int | None = None
    # Real absolute path in native OS format. Only set by local
    # filesystem backends. Legacy consumers (AI pipeline, caches,
    # databases) should keep using this — NOT photo_id or URIs.
    abs_path: str | None = None

    @property
    def photo_id(self) -> str:
        return self.ref.photo_id

    @property
    def format(self) -> PhotoFormat | None:
        return self.ref.format

    @property
    def modified_datetime(self) -> datetime:
        return datetime.fromtimestamp(self.modified_time, tz=timezone.utc)

    def is_unchanged(self, other: PhotoInfo) -> bool:
        """True if ``other`` represents the same on-disk version."""
        return (
            self.size == other.size
            and self.modified_time == other.modified_time
        )
