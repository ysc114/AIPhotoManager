"""Storage backend abstract base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import BinaryIO, Iterator

from .models import PhotoInfo, PhotoRef


class StorageBackend(ABC):
    """Abstract base class for photo storage backends.

    A backend knows how to:
    - List photos in a directory (scan)
    - Open/read photo bytes
    - Get photo metadata (stat)

    Known implementations:
    - LocalFilesystemBackend (core.storage.local): local drives or
      mounted network shares
    - SMBBackend (core.storage.smb_backend): direct SMB access (stub)
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this backend instance."""

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Whether this backend is currently accessible."""

    @abstractmethod
    def list_photos(
        self, directory: str = "", recursive: bool = True
    ) -> Iterator[PhotoInfo]:
        """List photos in a directory.

        Args:
            directory: Subdirectory within backend root (POSIX path).
            recursive: Whether to scan subdirectories.

        Yields:
            PhotoInfo for each photo found.
        """

    @abstractmethod
    def open(self, ref: PhotoRef) -> BinaryIO:
        """Open a photo for reading. Caller must close the object."""

    @abstractmethod
    def read_bytes(self, ref: PhotoRef) -> bytes:
        """Read entire photo as bytes."""

    @abstractmethod
    def stat(self, ref: PhotoRef) -> PhotoInfo:
        """Get metadata for a specific photo.

        Raises:
            PhotoNotFoundError: if the photo doesn't exist.
        """

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Check if a path exists in this backend."""
