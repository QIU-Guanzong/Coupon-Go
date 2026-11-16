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
  returned URL prefix, nothing else.
- Validation: image/* content type, extension in the allow-list, each
  file <= UPLOAD_MAX_BYTES, at most 9 files per request (same cap as
  the review images[] field). No virus scan, no EXIF stripping, no
  thumbnailing — all noted as follow-ups before any production use.
- Filenames are server-generated UUIDs, so no path traversal is
  possible through a client-supplied name.
"""
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from .. import config
from ..deps import get_current_user

router = APIRouter()

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}
MAX_FILES = 9


def _save_file(upload: UploadFile) -> str:
    ext = (upload.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支援的圖片格式：{ext or '(無副檔名)'}，只接受 "
                   "jpg / jpeg / png / webp / gif")
    if upload.content_type and not upload.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="只接受圖片檔案")
    data = upload.file.read(config.UPLOAD_MAX_BYTES + 1)
    if len(data) > config.UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail="圖片過大，上限 %d KB" % (config.UPLOAD_MAX_BYTES // 1024))
    if not data:
        raise HTTPException(status_code=400, detail="空檔案")
    subdir = Path(config.UPLOAD_DIR) / "review-images"
    subdir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    (subdir / name).write_bytes(data)
    return f"/uploads/review-images/{name}"


@router.post("/uploads/review-images", status_code=201)
def upload_review_images(files: list[UploadFile] = File(...),
                         user=Depends(get_current_user)):
    if not files:
        raise HTTPException(status_code=400, detail="請選擇圖片")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail="一次最多上傳 9 張圖")
    paths = [_save_file(f) for f in files]
    return {"message": "圖片已上傳", "images": paths}
