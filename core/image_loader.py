"""Image loader — Stage 2B compatibility layer.

The actual scanning is now done by ``core.storage.LocalPhotoLibrary``;
this module keeps the legacy public API so existing callers
(``ui/main_window_v3.py`` etc.) continue to receive a ``list[str]``
of absolute paths, byte-identical to the old implementation.

Rollback: the original 5-line implementation is kept below as
``_load_images_from_folder_legacy`` and is used automatically if
``core.storage`` cannot be imported.
"""

import os

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _load_images_from_folder_legacy(folder):
    # Original Stage<=3 implementation, kept verbatim as fallback/rollback.
    return [os.path.join(folder, f) for f in os.listdir(folder)
            if os.path.splitext(f)[1].lower() in SUPPORTED_EXTENSIONS]


def load_images_from_folder(folder):
    """List image files directly inside ``folder`` (non-recursive).

    Behaviour identical to the legacy implementation:
    - non-recursive, only .jpg/.jpeg/.png/.bmp/.webp
    - returns list[str] of paths joined as os.path.join(folder, name)
    - raises FileNotFoundError when ``folder`` does not exist
    """
    try:
        from core.storage import LocalPhotoLibrary
        from core.storage.exceptions import BackendNotAvailableError
    except ImportError:
        return _load_images_from_folder_legacy(folder)

    library = LocalPhotoLibrary(folder)
    try:
        photos = library.list_photos(recursive=False)
    except BackendNotAvailableError as e:
        # os.listdir() raised FileNotFoundError for missing dirs; keep that.
        raise FileNotFoundError(f"No such directory: {folder!r}") from e

    # Re-join with the original ``folder`` argument (not the resolved
    # root) so returned paths stay byte-identical to the legacy output —
    # analysis_cache.json / identity_db.sqlite keys must not change.
    return [os.path.join(folder, os.path.basename(p.abs_path)) for p in photos]
