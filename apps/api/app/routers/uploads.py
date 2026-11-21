"""Review-image uploads (v2 backing for the v1 reserved images[] field).

v1 (line B) accepted images[] as client-supplied URL strings with no
upload endpoint. This router closes that gap: a logged-in user uploads
image files and gets back /uploads/... paths to pass as images[] when
creating a review (POST /reviews).

Storage (placeholder, local only — NOT object storage):
- Files land under <UPLOAD_DIR>/review-images/<uuid>.<ext> on the API
  server's local disk and are served by the StaticFiles mount at
  /uploads (see app.main). No real object storage (S3 / GCS) is wired;
  swapping in object storage means replacing _save_file() and the
  returned URL prefix, nothing else. Local disk is a DEVELOPMENT
  placeholder: it must be replaced by object storage before launch
  (see README "還沒做的事"）.
- Validation: image/* content type, extension in the allow-list, each
  file <= UPLOAD_MAX_BYTES, at most 9 files per request (same cap as
  the review images[] field). No virus scan, no thumbnailing — both
  noted as follow-ups before any production use.
- EXIF (P1-6): stripped losslessly on upload — the JPEG APP1 (Exif)
  segment, the PNG eXIf chunk and the WebP EXIF chunk are dropped
  and every other byte is kept verbatim (no re-encode, no quality
  loss). GIF carries no EXIF and passes through. A file the stripper
  cannot parse is stored unchanged (it carries no parseable EXIF
  segment to remove); stripping never rejects an upload.
- Filenames are server-generated UUIDs, so no path traversal is
  possible through a client-supplied name.
"""
import logging
import os
import struct
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from .. import config
from ..deps import get_current_user, require_role

router = APIRouter()

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}
# §3.5: merchant listing images — no GIF (still frames only).
MERCHANT_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
MAX_FILES = 9


def _strip_jpeg_exif(data: bytes) -> bytes:
    """Drop the APP1 (Exif) segment from a JPEG, byte-identical else."""
    if len(data) < 4 or data[0:2] != b"\xff\xd8":
        return data
    out = bytearray(data[0:2])
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            break
        marker = data[i + 1]
        if marker == 0xD9:  # EOI
            out += data[i:]
            return bytes(out)
        if marker == 0xDA:  # SOS: entropy data follows, copy the rest
            out += data[i:]
            return bytes(out)
        if marker in (0x01,) or 0xD0 <= marker <= 0xD7:
            out += data[i:i + 2]
            i += 2
            continue
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        if seg_len < 2 or i + 2 + seg_len > len(data):
            break
        segment = data[i + 4:i + 2 + seg_len]
        is_exif = marker == 0xE1 and segment.startswith(b"Exif\x00\x00")
        if not is_exif:
            out += data[i:i + 2 + seg_len]
        i += 2 + seg_len
    out += data[i:]
    return bytes(out)


def _strip_png_exif(data: bytes) -> bytes:
    """Drop the eXIf chunk from a PNG, byte-identical else."""
    if len(data) < 8 or data[0:8] != b"\x89PNG\r\n\x1a\n":
        return data
    out = bytearray(data[0:8])
    i = 8
    while i + 12 <= len(data):
        length = struct.unpack(">I", data[i:i + 4])[0]
        ctype = data[i + 4:i + 8]
        end = i + 12 + length
        if end > len(data):
            break
        if ctype != b"eXIf":
            out += data[i:end]
        i = end
    out += data[i:]
    return bytes(out)


def _strip_webp_exif(data: bytes) -> bytes:
    """Drop the EXIF chunk from a WebP (RIFF), byte-identical else."""
    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WEBP":
        return data
    out = bytearray(data[0:12])
    i = 12
    while i + 8 <= len(data):
        fourcc = data[i:i + 4]
        size = struct.unpack("<I", data[i + 4:i + 8])[0]
        end = i + 8 + size + (size % 2)
        if end > len(data):
            break
        if fourcc != b"EXIF":
            out += data[i:end]
        i = end
    out += data[i:]
    # Keep the RIFF size field honest after dropping the chunk.
    riff_size = len(out) - 8
    out[4:8] = struct.pack("<I", riff_size)
    return bytes(out)


def strip_exif(data: bytes, ext: str) -> bytes:
    """P1-6: strip EXIF metadata from one uploaded image (lossless).

    JPEG -> APP1 (Exif) segment, PNG -> eXIf chunk, WebP -> EXIF
    chunk; GIF has no EXIF. Unparseable input is returned unchanged
    (never raises: stripping must not reject an upload).
    """
    try:
        if ext in ("jpg", "jpeg"):
            return _strip_jpeg_exif(data)
        if ext == "png":
            return _strip_png_exif(data)
        if ext == "webp":
            return _strip_webp_exif(data)
    except Exception:
        logger.warning("EXIF strip failed for .%s upload; storing as-is",
                       ext, exc_info=True)
    return data


def _save_file(upload: UploadFile, subdir: str = "review-images",
               allowed_exts=None) -> str:
    ext = (upload.filename or "").rsplit(".", 1)[-1].lower()
    allowed = allowed_exts if allowed_exts is not None else ALLOWED_EXTENSIONS
    if ext not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"不支援的圖片格式：{ext or '(無副檔名)'}，只接受 "
                   + " / ".join(sorted(allowed)))
    if upload.content_type and not upload.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="只接受圖片檔案")
    data = upload.file.read(config.UPLOAD_MAX_BYTES + 1)
    if len(data) > config.UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail="圖片過大，上限 %d KB" % (config.UPLOAD_MAX_BYTES // 1024))
    if not data:
        raise HTTPException(status_code=400, detail="空檔案")
    subdir_path = Path(config.UPLOAD_DIR) / subdir
    subdir_path.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    (subdir_path / name).write_bytes(strip_exif(data, ext))
    return f"/uploads/{subdir}/{name}"


@router.post("/uploads/review-images", status_code=201)
def upload_review_images(files: list[UploadFile] = File(...),
                         user=Depends(get_current_user)):
    if not files:
        raise HTTPException(status_code=400, detail="請選擇圖片")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail="一次最多上傳 9 張圖")
    paths = [_save_file(f) for f in files]
    return {"message": "圖片已上傳", "images": paths}


@router.post("/uploads/merchant-images", status_code=201)
def upload_merchant_images(files: list[UploadFile] = File(...),
                           user=Depends(require_role("vendor", "admin"))):
    """§3.5: merchant listing images (coupon cover / shop photo).
    Same storage, size cap and EXIF stripping as review images; only
    jpg / png / webp. Returns /uploads/merchant-images/... paths to
    pass as Coupon.image / Shop.image. No object storage yet — same
    local-disk placeholder as review images (see module docstring)."""
    if not files:
        raise HTTPException(status_code=400, detail="請選擇圖片")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail="一次最多上傳 9 張圖")
    paths = [_save_file(f, subdir="merchant-images",
                        allowed_exts=MERCHANT_EXTENSIONS) for f in files]
    return {"message": "圖片已上傳", "images": paths}
