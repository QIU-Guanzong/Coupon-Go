"""P1-D merchant onboarding: private file uploads, e-contracts, and
applicant self-service (see docs/P1D_API.md).

Storage honesty (same placeholder approach as review-images):
merchant files live under <MERCHANT_FILE_DIR>/ on local disk, which is
NOT under the /uploads StaticFiles mount — there is no public URL for
them. Reads go through short-lived HMAC-signed download links only.
Swap in object storage before launch (docs/P1D_API.md §4).

The user-side 加盟 entry and merchant registration share this flow:
one application endpoint (POST /admin/merchant-applications), the same
upload / sign / review pipeline — no second flow.
"""
import hashlib
import hmac
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config, models, schemas_line_b
from ..contract_pdf import CJKFontUnavailableError, render_contract_pdf
from ..database import get_db
from ..deps import get_current_user, require_role
from .. import merchant_file_storage as storage
from ..merchant_upload_limits import check_upload_rate_limit
from ..models_line_b import (AdminAuditLog, MerchantApplication,
                             MerchantContractSignature,
                             MerchantContractVersion, MerchantFile)
from ..routers.uploads import strip_exif

router = APIRouter()
admin_only = require_role("admin")


class ConnectOnboardingIn(BaseModel):
    return_url: str | None = None
    refresh_url: str | None = None


@router.post("/merchant-applications/{application_id}/connect/onboarding")
def application_connect_onboarding(application_id: int, body: ConnectOnboardingIn,
                                   user=Depends(get_current_user), db=Depends(get_db)):
    from .. import application_connect
    return application_connect.start(db, application_id, user,
                                     body.return_url or config.STRIPE_CONNECT_RETURN_URL,
                                     body.refresh_url or config.STRIPE_CONNECT_REFRESH_URL)


@router.post("/merchant-applications/{application_id}/connect/sync")
def application_connect_sync(application_id: int, user=Depends(get_current_user),
                             db=Depends(get_db)):
    from .. import application_connect
    row = application_connect.sync(db, application_id, user.id)
    db.commit()
    return {"application_id": application_id, "onboarding_status": row.state,
            "charges_enabled": row.charges_enabled, "payouts_enabled": row.payouts_enabled,
            "simulated": row.simulated}

ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _log(db: Session, admin_id: int, action: str, target_type: str,
         target_id, detail: str = ""):
    db.add(AdminAuditLog(admin_id=admin_id, action=action,
                         target_type=target_type, target_id=target_id,
                         detail=detail))
    db.flush()


# ---------- signed download links ----------
def _sign_token(file_id: int, exp: int) -> str:
    msg = f"{file_id}:{exp}".encode("utf-8")
    return hmac.new(config.SECRET_KEY.encode("utf-8"), msg,
                    hashlib.sha256).hexdigest()


def _verify_token(file_id: int, exp: int, token: str) -> bool:
    if not token or exp <= int(datetime.now(timezone.utc).timestamp()):
        return False
    return hmac.compare_digest(token, _sign_token(file_id, exp))


def _download_link_for(file_id: int) -> dict:
    exp = int(datetime.now(timezone.utc).timestamp()) \
        + config.MERCHANT_DOWNLOAD_LINK_TTL_SECONDS
    token = _sign_token(file_id, exp)
    expires_at = datetime.fromtimestamp(exp, tz=timezone.utc).replace(
        tzinfo=None)
    return {
        "download_url": (f"/api/v3/merchant-files/{file_id}/download"
                         f"?exp={exp}&token={token}"),
        "expires_at": expires_at.isoformat(),
    }


# ---------- helpers ----------
def _file_out(f: MerchantFile) -> dict:
    return {
        "id": f.id, "application_id": f.application_id, "kind": f.kind,
        "original_filename": f.original_filename or "",
        "content_type": f.content_type or "",
        "size_bytes": f.size_bytes or 0, "sha256": f.sha256 or "",
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


def _can_read_file(db: Session, user: models.User, f: MerchantFile) -> bool:
    if user.role == "admin":
        return True
    if f.uploader_id == user.id:
        return True
    if f.application_id:
        app = db.query(MerchantApplication).filter(
            MerchantApplication.id == f.application_id).first()
        if app is not None and app.user_id == user.id:
            return True
    return False


def _get_application_or_404(db: Session, application_id: int):
    app = db.query(MerchantApplication).filter(
        MerchantApplication.id == application_id).first()
    if app is None:
        raise HTTPException(status_code=404, detail="找不到此入駐申請")
    return app


def _require_owner_or_admin(db: Session, user: models.User,
                            app: MerchantApplication):
    if user.role != "admin" and app.user_id != user.id:
        raise HTTPException(status_code=403, detail="沒有權限")


def _page(items, page: int, per_page: int = 20):
    total = len(items)
    return {
        "current_page": page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "total": total,
        "items": items[(page - 1) * per_page: page * per_page],
    }


def _save_merchant_file(upload, kind: str) -> tuple[str, bytes, str]:
    """Validate + store one uploaded file. Returns (stored_name, data, ext).

    Type: pdf / jpg / jpeg / png only (extension + content-type + file
    magic bytes -- a .pdf must start with %PDF-, a .png must carry the
    PNG signature, a .jpg/.jpeg must start with FF D8 FF).
    Size: <= UPLOAD_MAX_BYTES. Images get EXIF stripped (same helper as
    review-images). The stored name is a server UUID; no client filename
    ever reaches the filesystem.
    """
    ext = (upload.filename or "").rsplit(".", 1)[-1].lower() \
        if "." in (upload.filename or "") else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支援的檔案格式：{ext or '(無副檔名)'}，只接受 "
                   "pdf / jpg / jpeg / png")
    if ext == "pdf":
        if upload.content_type and upload.content_type != "application/pdf":
            raise HTTPException(status_code=400, detail="只接受 PDF 檔案")
    else:
        if upload.content_type and not upload.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="只接受圖片檔案")
    data = upload.file.read(config.UPLOAD_MAX_BYTES + 1)
    if len(data) > config.UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail="檔案過大，上限 %d KB" % (config.UPLOAD_MAX_BYTES // 1024))
    if not data:
        raise HTTPException(status_code=400, detail="空檔案")
    # Item 4: verify content by magic bytes, not just the extension /
    # client-declared content-type.
    if ext == "pdf":
        expected, label = b"%PDF-", "PDF"
    elif ext == "png":
        expected, label = b"\x89PNG\r\n\x1a\n", "PNG"
    else:  # jpg / jpeg
        expected, label = b"\xff\xd8\xff", "JPEG"
    if not data.startswith(expected):
        raise HTTPException(
            status_code=400,
            detail=f"檔案內容不是有效的 {label} 檔案")
    if ext in ("jpg", "jpeg", "png"):
        data = strip_exif(data, ext)
    name = f"{uuid.uuid4().hex}.{ext}"
    storage.save(name, data)
    return name, data, ext


def _check_unattached_quota(db: Session, user: models.User):
    """Item 3: unattached-file quota (count + total bytes) -> 429."""
    rows = db.query(MerchantFile.size_bytes).filter(
        MerchantFile.uploader_id == user.id,
        MerchantFile.application_id.is_(None)).all()
    count = len(rows)
    total = sum((r[0] or 0) for r in rows)
    if count >= config.MERCHANT_UNATTACHED_MAX_FILES:
        raise HTTPException(
            status_code=429,
            detail="未掛接文件已達上限（%d 個），請先提交申請認領或刪除舊文件"
                   % config.MERCHANT_UNATTACHED_MAX_FILES,
            headers={"Retry-After": "60"})
    if total >= config.MERCHANT_UNATTACHED_MAX_BYTES:
        raise HTTPException(
            status_code=429,
            detail="未掛接文件總大小已達上限（%d MB），請先提交申請認領或刪除舊文件"
                   % (config.MERCHANT_UNATTACHED_MAX_BYTES // (1024 * 1024)),
            headers={"Retry-After": "60"})


# ---------- file upload / signed download ----------
@router.post("/merchant-files", status_code=201)
def upload_merchant_file(
        kind: str = Form(...),
        application_id: int = Form(None),
        file: UploadFile = File(...),
        user: models.User = Depends(get_current_user),
        db: Session = Depends(get_db)):
    if kind not in ("br", "ci", "director_id", "shop_photo"):
        raise HTTPException(status_code=400, detail=f"未知檔案類別：{kind}")
    app = None
    if application_id is not None:
        app = _get_application_or_404(db, application_id)
        _require_owner_or_admin(db, user, app)
    # Item 3: per-user per-minute rate limit, then (for unattached
    # uploads) the count / total-bytes quota -- both checked before
    # anything is saved, both answered 429.
    check_upload_rate_limit(user.id)
    if app is None:
        _check_unattached_quota(db, user)
    stored_name, data, _ext = _save_merchant_file(file, kind)
    f = MerchantFile(
        application_id=app.id if app else None, uploader_id=user.id,
        kind=kind, stored_name=stored_name,
        original_filename=file.filename or "",
        content_type=file.content_type or "",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest())
    db.add(f)
    db.commit()
    db.refresh(f)
    return {"message": "文件已上傳", "file": _file_out(f)}


@router.post("/merchant-files/{file_id}/download-link")
def merchant_file_download_link(
        file_id: int,
        user: models.User = Depends(get_current_user),
        db: Session = Depends(get_db)):
    f = db.query(MerchantFile).filter(MerchantFile.id == file_id).first()
    if f is None:
        raise HTTPException(status_code=404, detail="找不到此文件")
    if not _can_read_file(db, user, f):
        raise HTTPException(status_code=403, detail="沒有權限")
    return _download_link_for(f.id)


@router.get("/merchant-files/{file_id}/download")
def merchant_file_download(file_id: int, exp: int = Query(None),
                           token: str = Query(None),
                           db: Session = Depends(get_db)):
    f = db.query(MerchantFile).filter(MerchantFile.id == file_id).first()
    if f is None:
        raise HTTPException(status_code=404, detail="找不到此文件")
    if exp is None or not _verify_token(file_id, exp, token or ""):
        raise HTTPException(status_code=403, detail="下載連結無效或已過期")
    # Item 4: Content-Type is server-detected from the stored bytes'
    # magic number, never the client-declared value in the database;
    # undetectable -> application/octet-stream. No new column.
    try:
        data = storage.open_bytes(f.stored_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="找不到此文件")
    media_type = storage.detect_content_type(data) \
        or "application/octet-stream"
    from fastapi.responses import Response
    return Response(
        content=data, media_type=media_type,
        headers={"Content-Disposition":
                 f'attachment; filename="{f.original_filename or f.stored_name}"'})


# ---------- e-contracts ----------
@router.get("/merchant-contracts/current")
def get_current_contract(user: models.User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    v = db.query(MerchantContractVersion).filter(
        MerchantContractVersion.is_current == True).first()  # noqa: E712
    if v is None:
        raise HTTPException(status_code=404, detail="尚無可簽署的合約版本")
    return {"id": v.id, "version": v.version, "title": v.title,
            "body_text": v.body_text,
            "test_only": "【待法務確認】" in (v.body_text or ""),
            "published_at": v.published_at.isoformat()
            if v.published_at else None}


@router.get("/merchant-contracts")
def list_contracts(admin: models.User = Depends(admin_only),
                   db: Session = Depends(get_db)):
    rows = db.query(MerchantContractVersion).order_by(
        MerchantContractVersion.id.desc()).all()
    return {"contracts": [
        {"id": v.id, "version": v.version, "title": v.title,
         "is_current": bool(v.is_current),
         "published_at": v.published_at.isoformat()
         if v.published_at else None} for v in rows]}


@router.post("/merchant-contracts", status_code=201)
def publish_contract(
        body: schemas_line_b.MerchantContractVersionCreateIn,
        admin: models.User = Depends(admin_only),
        db: Session = Depends(get_db)):
    existing = db.query(MerchantContractVersion).filter(
        MerchantContractVersion.version == body.version).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="合約版本已存在")
    db.query(MerchantContractVersion).filter(
        MerchantContractVersion.is_current == True).update(  # noqa: E712
            {"is_current": False})
    v = MerchantContractVersion(version=body.version, title=body.title,
                                body_text=body.body_text, is_current=True,
                                published_at=_now(), created_by=admin.id)
    db.add(v)
    db.flush()
    _log(db, admin.id, "merchant_contract.publish",
         "merchant_contract_version", v.id, f"version={v.version}")
    db.commit()
    db.refresh(v)
    return {"message": "合約版本已發布",
            "contract": {"id": v.id, "version": v.version,
                         "title": v.title, "is_current": True,
                         "published_at": v.published_at.isoformat()
                         if v.published_at else None}}


@router.post("/merchant-applications/{application_id}/sign-contract",
             status_code=201)
def sign_contract(application_id: int,
                  body: schemas_line_b.MerchantContractSignIn,
                  request: Request,
                  user: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    app = _get_application_or_404(db, application_id)
    if app.user_id != user.id:
        raise HTTPException(status_code=403, detail="沒有權限")
    if app.status != "pending":
        raise HTTPException(status_code=400, detail="此申請已審核，不可簽署合約")
    if body.contract_version_id is not None:
        version = db.query(MerchantContractVersion).filter(
            MerchantContractVersion.id == body.contract_version_id).first()
        if version is None:
            raise HTTPException(status_code=404, detail="找不到此合約版本")
        if not version.is_current:
            raise HTTPException(status_code=400,
                                detail="只能簽署當前版本合約")
    else:
        version = db.query(MerchantContractVersion).filter(
            MerchantContractVersion.is_current == True).first()  # noqa: E712
        if version is None:
            raise HTTPException(status_code=404, detail="尚無可簽署的合約版本")
    existing = db.query(MerchantContractSignature).filter(
        MerchantContractSignature.application_id == app.id,
        MerchantContractSignature.contract_version_id == version.id).first()
    if existing is not None:
        # Idempotent re-sign: the route defaults to 201, so answer
        # explicitly with 200 for the already-signed case.
        return JSONResponse(status_code=200, content={
            "message": "合約已簽署",
            "signature": _signature_out(db, existing)})
    ip = request.client.host if request.client else ""
    # Item 2: render BEFORE any DB write; a missing CJK font is a
    # 503 with a clear message and leaves no half-written state.
    try:
        pdf_bytes = render_contract_pdf(
            version=version.version, title=version.title,
            body_text=version.body_text, shop_name=app.shop_name,
            applicant_name=user.name or "", applicant_email=user.email,
            user_id=user.id, application_id=app.id, signed_at=_now(),
            ip_address=ip)
    except CJKFontUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    stored_name = f"{uuid.uuid4().hex}.pdf"
    storage.save(stored_name, pdf_bytes)
    pdf_file = MerchantFile(
        application_id=app.id, uploader_id=user.id, kind="contract_pdf",
        stored_name=stored_name,
        original_filename=f"contract-v{version.version}-app{app.id}.pdf",
        content_type="application/pdf", size_bytes=len(pdf_bytes),
        sha256=hashlib.sha256(pdf_bytes).hexdigest())
    db.add(pdf_file)
    db.flush()
    sig = MerchantContractSignature(
        application_id=app.id, user_id=user.id,
        contract_version_id=version.id, signed_at=_now(), ip_address=ip,
        pdf_file_id=pdf_file.id)
    db.add(sig)
    app.contract_version_id = version.id
    app.contract_signed_at = sig.signed_at
    app.contract_signed_ip = ip
    db.commit()
    db.refresh(sig)
    return {"message": "合約已簽署",
            "signature": _signature_out(db, sig)}


def _signature_out(db: Session, sig: MerchantContractSignature) -> dict:
    version = db.query(MerchantContractVersion).filter(
        MerchantContractVersion.id == sig.contract_version_id).first()
    return {
        "id": sig.id, "application_id": sig.application_id,
        "user_id": sig.user_id,
        "contract_version": version.version if version else "",
        "signed_at": sig.signed_at.isoformat() if sig.signed_at else None,
        "ip_address": sig.ip_address or "",
        "pdf_file_id": sig.pdf_file_id,
    }


@router.get("/merchant-applications/{application_id}/contract-pdf")
def get_contract_pdf(application_id: int,
                     user: models.User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    app = _get_application_or_404(db, application_id)
    _require_owner_or_admin(db, user, app)
    sig = (db.query(MerchantContractSignature)
           .filter(MerchantContractSignature.application_id == app.id)
           .order_by(MerchantContractSignature.signed_at.desc(),
                     MerchantContractSignature.id.desc()).first())
    if sig is None or sig.pdf_file_id is None:
        raise HTTPException(status_code=404, detail="此申請尚未簽署合約")
    return _download_link_for(sig.pdf_file_id)


# ---------- applicant self-service ----------
@router.get("/merchant-applications/mine")
def list_my_applications(page: int = Query(1, ge=1),
                         user: models.User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    from ..routers.admin import _application_out
    q = db.query(MerchantApplication).filter(
        MerchantApplication.user_id == user.id).order_by(
            MerchantApplication.created_at.desc(),
            MerchantApplication.id.desc())
    out = _page([_application_out(db, a) for a in q.all()], page)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "applications": out["items"]}


@router.get("/merchant-applications/{application_id}")
def get_my_application(application_id: int,
                       user: models.User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    from ..routers.admin import _application_out
    app = _get_application_or_404(db, application_id)
    _require_owner_or_admin(db, user, app)
    return {"application": _application_out(db, app)}
