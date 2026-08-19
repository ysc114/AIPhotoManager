"""Local filesystem storage backend + LocalPhotoLibrary facade.

``LocalFilesystemBackend`` works with any filesystem path:
- Local drives (C:\\, D:\\)
- Mounted network shares (Z:\\, /mnt/nas)
- UNC paths (\\\\server\\share)

``LocalPhotoLibrary`` is a convenience facade used by the application:
one folder -> photos, with ``photo.abs_path`` holding the real absolute
path so the existing AI pipeline keeps working unchanged.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator

from .base import StorageBackend
from .exceptions import BackendNotAvailableError, PhotoNotFoundError
from .models import PhotoInfo, PhotoRef, SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)


class LocalFilesystemBackend(StorageBackend):
    """Storage backend for local filesystem paths.

    Also works with mounted NAS shares because they appear as
    normal filesystem paths.
    """

    def __init__(self, name: str, root: str | Path):
        """
        Args:
            name: Unique name for this backend (e.g. 'local-photos').
            root: Root directory path, Windows or POSIX style.
        """
        self._name = name
        self._root = Path(root).resolve()

    @property
    def name(self) -> str:
        return self._name

    @property
    def root(self) -> Path:
        return self._root

    @property
    def is_available(self) -> bool:
        return self._root.is_dir()

    # ------------------------------------------------------------------ #
    #  Path conversion helpers                                            #
    # ------------------------------------------------------------------ #

    def _to_posix_path(self, abs_path: Path) -> str:
        """Absolute filesystem path -> backend-relative POSIX path."""
        try:
            rel = abs_path.relative_to(self._root)
        except ValueError:
            rel_str = str(abs_path)
            root_str = str(self._root)
            if rel_str.startswith(root_str):
                rel_str = rel_str[len(root_str):].lstrip(os.sep)
            rel = Path(rel_str)
        return PurePosixPath(*rel.parts).as_posix()

    def _to_abs_path(self, posix_path: str) -> Path:
        """Backend-relative POSIX path -> absolute filesystem path."""
        parts = PurePosixPath(posix_path).parts
        return self._root.joinpath(*parts)

    # ------------------------------------------------------------------ #
    #  StorageBackend API                                                 #
    # ------------------------------------------------------------------ #

    def list_photos(
        self, directory: str = "", recursive: bool = True
    ) -> Iterator[PhotoInfo]:
        if not self.is_available:
            raise BackendNotAvailableError(
                f"Backend '{self._name}' root not accessible: {self._root}"
            )

        scan_dir = self._to_abs_path(directory) if directory else self._root

        if not scan_dir.exists():
            logger.warning("Scan directory does not exist: %s", scan_dir)
            return

        if recursive:
            walker = os.walk(scan_dir)
            for dirpath, _dirnames, filenames in walker:
                for filename in filenames:
                    info = self._make_info(Path(dirpath), filename)
                    if info is not None:
                        yield info
        else:
            try:
                entries = list(os.scandir(scan_dir))
            except OSError as e:
                logger.error("Cannot scan %s: %s", scan_dir, e)
                return
            for entry in entries:
                if not entry.is_file():
                    continue
                info = self._make_info(scan_dir, entry.name)
                if info is not None:
                    yield info

    def _make_info(self, dirpath: Path, filename: str) -> PhotoInfo | None:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in SUPPORTED_EXTENSIONS:
            return None

        abs_path = dirpath / filename
        try:
            stat_result = abs_path.stat()
        except OSError as e:
            logger.warning("Cannot stat %s: %s", abs_path, e)
            return None

        posix_path = self._to_posix_path(abs_path)
        ref = PhotoRef(backend_name=self._name, path=posix_path)
        return PhotoInfo(
            ref=ref,
            size=stat_result.st_size,
            modified_time=stat_result.st_mtime,
            created_time=stat_result.st_ctime,
            abs_path=str(abs_path),
        )

    def open(self, ref: PhotoRef) -> BinaryIO:
        abs_path = self._to_abs_path(ref.path)
        if not abs_path.is_file():
            raise PhotoNotFoundError(f"Photo not found: {ref}")
        return open(abs_path, "rb")

    def read_bytes(self, ref: PhotoRef) -> bytes:
        with self.open(ref) as f:
            return f.read()

    def stat(self, ref: PhotoRef) -> PhotoInfo:
        abs_path = self._to_abs_path(ref.path)
        if not abs_path.is_file():
            raise PhotoNotFoundError(f"Photo not found: {ref}")
        stat_result = abs_path.stat()
        return PhotoInfo(
            ref=ref,
            size=stat_result.st_size,
            modified_time=stat_result.st_mtime,
            created_time=stat_result.st_ctime,
            abs_path=str(abs_path),
        )

    def exists(self, path: str) -> bool:
        return self._to_abs_path(path).exists()


class LocalPhotoLibrary:
    """One-folder photo library — the drop-in replacement for
    ``load_images_from_folder()`` style scanning.

    Usage:
        lib = LocalPhotoLibrary("C:/Photos")
        photos = lib.list_photos()          # non-recursive by default
        for photo in photos:
            print(photo.abs_path)           # real absolute path
            print(photo.size, photo.modified_time)

    Notes:
        - ``list_photos()`` defaults to NON-recursive to match the
          legacy behaviour of ``load_images_from_folder()``.
          Pass ``recursive=True`` to scan subdirectories.
        - Every returned PhotoInfo has ``abs_path`` set to the native
          absolute path, byte-identical in meaning to what
          ``os.path.join(folder, filename)`` produced before.
    """

    def __init__(self, root: str | Path, name: str = "local") -> None:
        self._backend = LocalFilesystemBackend(name, root)

    @property
    def backend(self) -> LocalFilesystemBackend:
        return self._backend

    @property
    def name(self) -> str:
        return self._backend.name

    @property
    def root(self) -> Path:
        return self._backend.root

    @property
    def is_available(self) -> bool:
        return self._backend.is_available

    def list_photos(self, recursive: bool = False) -> list[PhotoInfo]:
        """List photos in the library folder.

        Args:
            recursive: ``False`` (default) = top level only, matching
                       legacy behaviour. ``True`` = include subfolders.

        Returns:
            List of PhotoInfo; each item's ``abs_path`` is the real
            absolute filesystem path.
        """
        return list(self._backend.list_photos(recursive=recursive))

    def read_bytes(self, photo: PhotoInfo) -> bytes:
        """Read the raw bytes of a photo returned by ``list_photos``."""
        return self._backend.read_bytes(photo.ref)
