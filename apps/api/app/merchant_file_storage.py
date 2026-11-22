"""Merchant-file storage (P1-D): one small interface over the private
file store (see docs/P1D_API.md §4).

Local disk is for the demo / experience environment only. Before
launch, swap this module's implementation for a private object store
(S3 / GCS private bucket); the routers and the cleanup script only
call save / open_bytes / delete, so nothing else changes.

Root is MERCHANT_FILE_DIR, read from config at call time. stored_name
is always a server-generated "<uuid>.<ext>" -- no client filename
ever reaches the filesystem, so there is no path-traversal surface
here; anything containing a path separator is rejected anyway.
"""
import os
from pathlib import Path

from . import config

# Magic numbers used to detect a file's real type (item 4): the
# download endpoint answers with the server-detected type, never the
# client-declared content_type stored at upload time.
_PDF_MAGIC = b"%PDF-"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


def detect_content_type(data: bytes) -> str | None:
    """Detect application/pdf / image/png / image/jpeg from file
    magic bytes; returns None when the bytes match none of them."""
    if data.startswith(_PDF_MAGIC):
        return "application/pdf"
    if data.startswith(_PNG_MAGIC):
        return "image/png"
    if data.startswith(_JPEG_MAGIC):
        return "image/jpeg"
    return None


def _root() -> Path:
    return Path(config.MERCHANT_FILE_DIR)


def _resolve(stored_name: str) -> Path:
    if not stored_name or "/" in stored_name or "\\" in stored_name \
            or stored_name in (".", ".."):
        raise ValueError(f"無效的檔案名：{stored_name!r}")
    return _root() / stored_name


def save(stored_name: str, data: bytes) -> Path:
    """Write data under MERCHANT_FILE_DIR; returns the stored path."""
    path = _resolve(stored_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def open_bytes(stored_name: str) -> bytes:
    """Read the whole stored file; raises FileNotFoundError if absent."""
    return _resolve(stored_name).read_bytes()


def delete(stored_name: str) -> bool:
    """Delete the stored file; returns True if it existed, False if
    it was already gone (so cleanup stays idempotent)."""
    try:
        _resolve(stored_name).unlink()
    except FileNotFoundError:
        return False
    return True


def exists(stored_name: str) -> bool:
    return _resolve(stored_name).exists()
