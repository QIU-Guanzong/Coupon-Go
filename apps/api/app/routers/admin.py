"""Admin v1 (F4 v1, report §12: /api/v3/admin/*).

v1 scope (task contract):
- Merchant onboarding review: submit -> approve / reject (reject needs
  a reason; a rejected applicant resubmits as a NEW application).
- Read-only dashboard: merchants / coupons / redemptions / users.
- Single role: admin only. Role splitting (auditor / support, vendor
  staff roles) and write operations (settlements, banners, user/role
  management) are F line (admin v2) — v1 intentionally has no POST/PUT
  on dashboard resources, only the application review decision.
- Every application review is written to admin_audit_logs.

NOT in v1 (do not add here): orders (no orders table until the package
line lands), settlements, banners/CMS, invoice handling, role changes.
"""
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from .. import config, models, schemas_line_b
from ..database import get_db
from ..deps import get_current_user, require_role
from ..models_line_b import AdminAuditLog, MerchantApplication, Review
from ..permissions_line_f import ALL_ROLES
from ..serializers import coupon_out, shop_out
from ..shared import audit_log as _log, csv_response, paginate

router = APIRouter()
admin_only = require_role("admin")


from ..timeutil import utcnow as _now


def _file_meta(db: Session, file_id):
    """Metadata for one merchant file id (no URL — downloads need a
    signed link from POST /merchant-files/{id}/download-link)."""
    if not file_id:
        return None
    from ..models_line_b import MerchantFile
    f = db.query(MerchantFile).filter(MerchantFile.id == file_id).first()
    if f is None:
        return None
    return {
        "id": f.id, "original_filename": f.original_filename or "",
        "size_bytes": f.size_bytes or 0, "sha256": f.sha256 or "",
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


def _application_out(db: Session, a: MerchantApplication) -> dict:
    from ..models_line_b import (MerchantContractSignature,
                                 MerchantContractVersion)
    user = db.query(models.User).filter(models.User.id == a.user_id).first()
    contract = None
    if a.contract_version_id:
        v = db.query(MerchantContractVersion).filter(
            MerchantContractVersion.id == a.contract_version_id).first()
        sig = (db.query(MerchantContractSignature)
               .filter(MerchantContractSignature.application_id == a.id)
               .order_by(MerchantContractSignature.signed_at.desc(),
                         MerchantContractSignature.id.desc()).first())
        contract = {
            "version": v.version if v else "",
            "signed_at": a.contract_signed_at.isoformat()
            if a.contract_signed_at else None,
            "signed_ip": a.contract_signed_ip or "",
            "pdf_file_id": sig.pdf_file_id if sig else None,
        }
    return {
        "id": a.id, "user_id": a.user_id,
        "applicant_email": user.email if user else "",
        "shop_name": a.shop_name, "contact_name": a.contact_name or "",
        "phone": a.phone or "", "address": a.address or "",
        "business_license": a.business_license or "",
        "id_card": a.id_card or "",
        "shop_photos": a.shop_photos_list,
        "industry_license": a.industry_license or "",
        # P1-D Hong Kong fields
        "br_number": a.br_number or "",
        "documents": {
            "br": _file_meta(db, a.br_file_id),
            "ci": _file_meta(db, a.ci_file_id),
            "director_id": _file_meta(db, a.director_id_file_id),
        },
        "shop_photo_files": [
            m for m in (_file_meta(db, fid)
                        for fid in a.shop_photo_file_ids_list)
            if m is not None],
        "stripe_account_id": a.stripe_account_id or "",
        "stripe_charges_enabled": bool(a.stripe_charges_enabled),
        "stripe_payouts_enabled": bool(a.stripe_payouts_enabled),
        "stripe_status_source": ("simulated" if config.APP_ENV == "dev" else "unverified")
        if a.stripe_account_id else "not_started",
        "contract": contract,
        "status": a.status, "reject_reason": a.reject_reason or "",
        "reviewed_by": a.reviewed_by, "reviewed_at": a.reviewed_at,
        "created_at": a.created_at,
    }


# ---------- merchant onboarding ----------
def _resolve_application_file(db: Session, user: models.User, file_id: int,
                              expected_kind: str):
    """Validate one file reference on a new application: the file must
    exist, belong to the applicant, be of the expected kind, and be
    unattached (the application does not exist yet, so a file already
    attached to another application cannot be claimed here)."""
    from ..models_line_b import MerchantFile
    f = db.query(MerchantFile).filter(MerchantFile.id == file_id).first()
    if f is None:
        raise HTTPException(status_code=400, detail="找不到指定的文件")
    if f.uploader_id != user.id:
        raise HTTPException(status_code=400, detail="文件不属于申请人")
    if f.kind != expected_kind:
        raise HTTPException(
            status_code=400,
            detail=f"文件類別不符：需要 {expected_kind}，實際為 {f.kind}")
    if f.application_id is not None:
        raise HTTPException(status_code=400, detail="文件已被其他申請使用")
    return f


@router.post("/admin/merchant-applications", status_code=201)
def submit_application(body: schemas_line_b.MerchantApplicationCreateIn,
                       user: models.User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    pending = (db.query(MerchantApplication)
               .filter(MerchantApplication.user_id == user.id,
                       MerchantApplication.status == "pending").first())
    if pending is not None:
        raise HTTPException(status_code=400, detail="已有待審核的入駐申請")
    # P1-D: validate Hong Kong file references before creating anything.
    files_to_link = []
    for file_id, kind in ((body.br_file_id, "br"),
                          (body.ci_file_id, "ci"),
                          (body.director_id_file_id, "director_id")):
        if file_id is not None:
            files_to_link.append(
                _resolve_application_file(db, user, file_id, kind))
    for file_id in body.shop_photo_file_ids:
        files_to_link.append(
            _resolve_application_file(db, user, file_id, "shop_photo"))
    app = MerchantApplication(
        user_id=user.id, shop_name=body.shop_name,
        contact_name=body.contact_name, phone=body.phone,
        address=body.address, business_license=body.business_license,
        id_card=body.id_card,
        shop_photos=json.dumps(body.shop_photos, ensure_ascii=False),
        industry_license=body.industry_license, status="pending",
        br_number=body.br_number or "",
        br_file_id=body.br_file_id, ci_file_id=body.ci_file_id,
        director_id_file_id=body.director_id_file_id,
        shop_photo_file_ids=json.dumps(body.shop_photo_file_ids))
    db.add(app)
    db.flush()
    for f in files_to_link:
        f.application_id = app.id
    db.commit()
    db.refresh(app)
    return {"message": "入駐申請已提交，請等待審核",
            "application": _application_out(db, app)}


@router.get("/admin/merchant-applications")
def list_applications(status: str = Query(None),
                      page: int = Query(1, ge=1),
                      page_size: int = Query(20, ge=1, le=100),
                      admin: models.User = Depends(admin_only),
                      db: Session = Depends(get_db)):
    q = db.query(MerchantApplication).order_by(
        MerchantApplication.created_at.desc(), MerchantApplication.id.desc())
    if status in ("pending", "approved", "rejected"):
        q = q.filter(MerchantApplication.status == status)
    rows = q.all()
    out = paginate([_application_out(db, a) for a in rows], page, page_size)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "applications": out["items"]}


@router.get("/admin/merchant-applications/{application_id}")
def get_application(application_id: int,
                    admin: models.User = Depends(admin_only),
                    db: Session = Depends(get_db)):
    app = db.query(MerchantApplication).filter(
        MerchantApplication.id == application_id).first()
    if app is None:
        raise HTTPException(status_code=404, detail="找不到此入駐申請")
    return {"application": _application_out(db, app)}


@router.post("/admin/merchant-applications/{application_id}/review")
def review_application(application_id: int,
                       body: schemas_line_b.MerchantApplicationReviewIn,
                       admin: models.User = Depends(admin_only),
                       db: Session = Depends(get_db)):
    app = db.query(MerchantApplication).filter(
        MerchantApplication.id == application_id).with_for_update().first()
    if app is None:
        raise HTTPException(status_code=404, detail="找不到此入駐申請")
    if app.status != "pending":
        raise HTTPException(status_code=400, detail="此申請已審核，不可重複審核")
    if not body.approve and not body.reject_reason.strip():
        raise HTTPException(status_code=400, detail="拒絕時請填寫拒絕原因")
    if body.approve:
        # P1-D approval gates (rejection is not gated):
        # (1) the applicant signed the CURRENT contract version;
        # (2) Stripe onboarding is complete (P1-B writes these columns;
        #     until then admin records them via /stripe-onboarding).
        from ..models_line_b import (MerchantContractSignature,
                                     MerchantContractVersion)
        current = db.query(MerchantContractVersion).filter(
            MerchantContractVersion.is_current == True).first()  # noqa: E712
        sig = None
        if current is not None:
            sig = db.query(MerchantContractSignature).filter(
                MerchantContractSignature.application_id == app.id,
                MerchantContractSignature.contract_version_id == current.id
            ).first()
        if current is None or sig is None:
            any_sig = db.query(MerchantContractSignature).filter(
                MerchantContractSignature.application_id == app.id).first()
            if any_sig is not None:
                raise HTTPException(
                    status_code=400,
                    detail="合約版本已更新，請簽署當前版本")
            raise HTTPException(status_code=400,
                                detail="尚未簽署當前版本合約")
        if config.APP_ENV != "dev" and "【待法務確認】" in (current.body_text or ""):
            raise HTTPException(status_code=409,
                                detail="測試合約不能用於正式入駐")
        # These columns can currently only be entered manually in the
        # development simulator; they are not evidence of a real account.
        # Connect must replace this guard with a verified provider lookup.
        if config.APP_ENV != "dev":
            raise HTTPException(status_code=409,
                                detail="正式 Stripe 開戶核驗尚未接通，暫不能批准")
        if (not (app.stripe_account_id or "").strip()
                or not app.stripe_charges_enabled
                or not app.stripe_payouts_enabled):
            raise HTTPException(status_code=400,
                                detail="尚未完成 Stripe 開戶")
    app.status = "approved" if body.approve else "rejected"
    app.reject_reason = "" if body.approve else body.reject_reason.strip()
    app.reviewed_by = admin.id
    app.reviewed_at = _now()
    applicant = db.query(models.User).filter(models.User.id == app.user_id).first()
    if body.approve and applicant is not None and applicant.role == "customer":
        applicant.role = "vendor"
    action = ("merchant_application.approve" if body.approve
              else "merchant_application.reject")
    _log(db, admin.id, action, "merchant_application", app.id,
         f"shop_name={app.shop_name}" + ("" if body.approve
                                         else f"; reason={app.reject_reason}"))
    db.commit()
    return {"message": "入駐申請已通過" if body.approve else "入駐申請已拒絕",
            "application": _application_out(db, app)}


@router.post("/admin/merchant-applications/{application_id}/stripe-onboarding")
def record_stripe_onboarding(
        application_id: int,
        body: schemas_line_b.StripeOnboardingIn,
        admin: models.User = Depends(admin_only),
        db: Session = Depends(get_db)):
    """Development-only mock; never represents a provider verification."""
    if config.APP_ENV != "dev":
        raise HTTPException(status_code=409,
                            detail="模擬開戶僅供測試環境使用")
    app = db.query(MerchantApplication).filter(
        MerchantApplication.id == application_id).with_for_update().first()
    if app is None:
        raise HTTPException(status_code=404, detail="找不到此入駐申請")
    if app.status != "pending":
        raise HTTPException(status_code=409, detail="已審核的申請不能修改開戶資料")
    account_id = body.stripe_account_id.strip()
    if not account_id:
        raise HTTPException(status_code=422, detail="請填寫模擬帳戶編號")
    if app.stripe_account_id and app.stripe_account_id != account_id:
        raise HTTPException(status_code=409, detail="申請已綁定其他模擬帳戶")
    if db.bind.dialect.name == "postgresql":
        # Serialize claims for one account across concurrent applications.
        key = int.from_bytes(hashlib.sha256(account_id.encode()).digest()[:8],
                             byteorder="big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    else:
        # SQLite serializes writers rather than supporting FOR UPDATE.
        app.stripe_onboarding_updated_at = _now()
        db.flush()
    claimed = db.query(MerchantApplication.id).filter(
        MerchantApplication.stripe_account_id == account_id,
        MerchantApplication.user_id != app.user_id).first()
    if claimed:
        raise HTTPException(status_code=409, detail="模擬帳戶已綁定其他申請人")
    app.stripe_account_id = account_id
    app.stripe_charges_enabled = bool(body.charges_enabled)
    app.stripe_payouts_enabled = bool(body.payouts_enabled)
    app.stripe_onboarding_updated_at = _now()
    _log(db, admin.id, "merchant_application.stripe_onboarding",
         "merchant_application", app.id,
         f"stripe_account_id={app.stripe_account_id}; "
         f"charges={app.stripe_charges_enabled}; "
         f"payouts={app.stripe_payouts_enabled}")
    db.commit()
    db.refresh(app)
    return {"message": "已登記模擬 Stripe 開戶狀態，僅供測試",
            "application": _application_out(db, app)}


# ---------- read-only dashboard ----------
@router.get("/admin/dashboard")
def dashboard(admin: models.User = Depends(admin_only),
              db: Session = Depends(get_db)):
    coupon_status = {
        s: c for s, c in
        db.query(models.Coupon.status, func.count(models.Coupon.id))
        .group_by(models.Coupon.status).all()
    }
    wallet_status = {
        s: c for s, c in
        db.query(models.WalletItem.status, func.count(models.WalletItem.id))
        .group_by(models.WalletItem.status).all()
    }
    return {
        "total_users": db.query(models.User).count(),
        "total_shops": db.query(models.Shop).count(),
        "total_coupons": db.query(models.Coupon).count(),
        "coupons_by_status": coupon_status,
        "wallet_by_status": {str(k): v for k, v in wallet_status.items()},
        "total_redemptions": db.query(models.Redemption).count(),
        "total_reviews": db.query(Review).count(),
        "rated_reviews": db.query(Review).filter(
            Review.status == "visible").count(),
        "pending_applications": db.query(MerchantApplication).filter(
            MerchantApplication.status == "pending").count(),
        "pending_coupons": db.query(models.Coupon).filter(
            models.Coupon.status == "pending_review").count(),
    }


@router.get("/admin/shops")
def admin_shops(page: int = Query(1, ge=1),
                page_size: int = Query(20, ge=1, le=100),
                admin: models.User = Depends(admin_only),
                db: Session = Depends(get_db)):
    from ..models_line_b import ShopRating
    shops = db.query(models.Shop).order_by(models.Shop.id).all()
    rows = []
    for s in shops:
        vendor = db.query(models.User).filter(models.User.id == s.vendor_id).first()
        rating = db.query(ShopRating).filter(ShopRating.shop_id == s.id).first()
        rows.append({
            **shop_out(db, s),
            "vendor_email": vendor.email if vendor else "",
            "coupon_count": db.query(models.Coupon).filter(
                models.Coupon.shop_id == s.id).count(),
            "redemption_count": db.query(models.Redemption).filter(
                models.Redemption.shop_id == s.id).count(),
            "rating_overall": rating.overall if rating else None,
            "review_count": rating.review_count if rating else 0,
        })
    out = paginate(rows, page, page_size)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "shops": out["items"]}


@router.get("/admin/coupons")
def admin_coupons(status: str = Query(None), shop_id: int = Query(None),
                  page: int = Query(1, ge=1),
                  page_size: int = Query(20, ge=1, le=100),
                  admin: models.User = Depends(admin_only),
                  db: Session = Depends(get_db)):
    q = db.query(models.Coupon).order_by(models.Coupon.id.desc())
    if status:
        q = q.filter(models.Coupon.status == status)
    if shop_id:
        q = q.filter(models.Coupon.shop_id == shop_id)
    rows = [coupon_out(db, c) for c in q.all()]
    out = paginate(rows, page, page_size)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "coupons": out["items"]}


@router.get("/admin/redemptions")
def admin_redemptions(shop_id: int = Query(None), page: int = Query(1, ge=1),
                      page_size: int = Query(20, ge=1, le=100),
                      format: str = Query("json"),
                      admin: models.User = Depends(admin_only),
                      db: Session = Depends(get_db)):
    q = (db.query(models.Redemption)
         .order_by(models.Redemption.scanned_at.desc(),
                   models.Redemption.id.desc()))
    if shop_id:
        q = q.filter(models.Redemption.shop_id == shop_id)
    rows = []
    for r in q.all():
        c = db.query(models.Coupon).filter(models.Coupon.id == r.coupon_id).first()
        s = db.query(models.Shop).filter(models.Shop.id == r.shop_id).first()
        rows.append({
            "id": r.id, "wallet_item_id": r.wallet_item_id,
            "coupon_id": r.coupon_id,
            "coupon_name": c.coupon_name if c else "",
            "shop_id": r.shop_id, "shop_name": s.name if s else "",
            "scanned_by_vendor_id": r.scanned_by_vendor_id,
            "scanned_at": r.scanned_at.isoformat() if r.scanned_at else None,
        })
    if format == "csv":
        columns = ["id", "wallet_item_id", "coupon_id", "coupon_name",
                   "shop_id", "shop_name", "scanned_by_vendor_id",
                   "scanned_at"]
        return csv_response("redemptions.csv", columns,
                            [[row[k] for k in columns] for row in rows])
    out = paginate(rows, page, page_size)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "redemptions": out["items"]}


@router.get("/admin/users")
def admin_users(role: str = Query(None), page: int = Query(1, ge=1),
                page_size: int = Query(20, ge=1, le=100),
                admin: models.User = Depends(admin_only),
                db: Session = Depends(get_db)):
    q = db.query(models.User).order_by(models.User.id)
    if role is not None:
        if role not in ALL_ROLES:
            raise HTTPException(status_code=400,
                                detail="未知角色：%s" % role)
        q = q.filter(models.User.role == role)
    rows = [{"id": u.id, "email": u.email, "name": u.name or "",
             "role": u.role, "is_active": u.is_active,
             "created_at": u.created_at.isoformat() if u.created_at else None}
            for u in q.all()]
    out = paginate(rows, page, page_size)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "users": out["items"]}


@router.get("/admin/audit-logs")
def admin_audit_logs(page: int = Query(1, ge=1),
                     page_size: int = Query(20, ge=1, le=100),
                     admin_id: int = Query(None),
                     action: str = Query(None),
                     target_type: str = Query(None),
                     admin: models.User = Depends(admin_only),
                     db: Session = Depends(get_db)):
    """Audit-log list (GAP §5.9): filter by the admin who acted,
    the action, or the target type — the log is now searchable
    instead of page-through only."""
    q = db.query(AdminAuditLog).order_by(
        AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
    if admin_id is not None:
        q = q.filter(AdminAuditLog.admin_id == admin_id)
    if action:
        q = q.filter(AdminAuditLog.action == action)
    if target_type:
        q = q.filter(AdminAuditLog.target_type == target_type)
    rows = q.all()
    out = paginate([{"id": l.id, "admin_id": l.admin_id, "action": l.action,
                  "target_type": l.target_type, "target_id": l.target_id,
                  "detail": l.detail or "",
                  "created_at": l.created_at.isoformat()
                  if l.created_at else None} for l in rows], page, page_size)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "logs": out["items"]}
