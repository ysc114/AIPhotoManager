"""Storage-layer exceptions."""


class StorageError(Exception):
    """Base exception for storage operations."""


class BackendNotAvailableError(StorageError):
    """Raised when a storage backend is not accessible."""


class PhotoNotFoundError(StorageError):
    """Raised when a photo is not found in the backend."""


class BackendAlreadyRegisteredError(StorageError):
    """Raised when a backend with the same name is already registered."""


class UnsupportedFormatError(StorageError):
    """Raised when a photo format is not supported."""
