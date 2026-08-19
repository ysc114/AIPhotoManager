"""SMB / CIFS storage backend  (stub).

When implemented (future stage), this backend will allow *direct*
SMB access to a NAS share without an OS-level mount. On Windows the
share can simply be mounted and used through ``LocalFilesystemBackend``.

All methods raise NotImplementedError for now; the interface exists so
PhotoLibrary and tests can reference it.
"""

from __future__ import annotations

from typing import BinaryIO, Iterator

from .base import StorageBackend
from .models import PhotoInfo, PhotoRef


class SMBBackend(StorageBackend):
    """Direct SMB/CIFS backend for NAS access without a mount point.

    This is a **stub** — all methods raise ``NotImplementedError``.
    """

    def __init__(
        self,
        name: str,
        server: str,
        share: str,
        username: str = "",
        password: str = "",
        port: int = 445,
    ) -> None:
        self._name = name
        self._server = server
        self._share = share
        self._username = username
        self._password = password
        self._port = port
        self._connected = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_available(self) -> bool:
        return self._connected

    def connect(self) -> None:
        raise NotImplementedError(
            "SMBBackend will be implemented in a future stage. "
            "For now, mount the NAS share and use LocalFilesystemBackend."
        )

    def list_photos(
        self, directory: str = "", recursive: bool = True
    ) -> Iterator[PhotoInfo]:
        raise NotImplementedError(
            "SMBBackend is a stub. Mount the NAS and use LocalFilesystemBackend."
        )

    def open(self, ref: PhotoRef) -> BinaryIO:
        raise NotImplementedError(
            "SMBBackend is a stub. Mount the NAS and use LocalFilesystemBackend."
        )

    def read_bytes(self, ref: PhotoRef) -> bytes:
        raise NotImplementedError(
            "SMBBackend is a stub. Mount the NAS and use LocalFilesystemBackend."
        )

    def stat(self, ref: PhotoRef) -> PhotoInfo:
        raise NotImplementedError(
            "SMBBackend is a stub. Mount the NAS and use LocalFilesystemBackend."
        )

    def exists(self, path: str) -> bool:
        raise NotImplementedError(
            "SMBBackend is a stub. Mount the NAS and use LocalFilesystemBackend."
        )
