"""Storage layer: unified interface for local and NAS photo storage.

Typical usage (one folder, legacy-friendly):

    from core.storage import LocalPhotoLibrary

    lib = LocalPhotoLibrary("C:/Photos")
    photos = lib.list_photos()          # non-recursive
    for photo in photos:
        print(photo.abs_path, photo.size)

Multi-backend usage (future NAS support):

    from core.storage import PhotoLibrary, LocalFilesystemBackend

    library = PhotoLibrary()
    library.add_backend(LocalFilesystemBackend("local", "C:/Photos"))
    library.add_backend(LocalFilesystemBackend("nas", "Z:/NAS/Photos"))
    result = library.scan()             # incremental
"""

from .base import StorageBackend
from .local import LocalFilesystemBackend, LocalPhotoLibrary
from .smb_backend import SMBBackend
from .library import PhotoLibrary, ScanResult
from .index import PhotoIndex, RecordStatus
from .models import PhotoRef, PhotoInfo, PhotoFormat, SUPPORTED_EXTENSIONS
from .exceptions import (
    StorageError,
    BackendNotAvailableError,
    PhotoNotFoundError,
    BackendAlreadyRegisteredError,
    UnsupportedFormatError,
)

__all__ = [
    # backends / library
    "StorageBackend",
    "LocalFilesystemBackend",
    "LocalPhotoLibrary",
    "SMBBackend",
    "PhotoLibrary",
    "ScanResult",
    "PhotoIndex",
    "RecordStatus",
    # models
    "PhotoRef",
    "PhotoInfo",
    "PhotoFormat",
    "SUPPORTED_EXTENSIONS",
    # exceptions
    "StorageError",
    "BackendNotAvailableError",
    "PhotoNotFoundError",
    "BackendAlreadyRegisteredError",
    "UnsupportedFormatError",
]
