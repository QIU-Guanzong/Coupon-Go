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
import csv
import io
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas_line_b
from ..database import get_db
from ..deps import get_current_user, require_role
from ..models_line_b import AdminAuditLog, MerchantApplication, Review
from ..permissions_line_f import ALL_ROLES
from ..serializers import coupon_out, shop_out

router = APIRouter()
admin_only = require_role("admin")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _log(db: Session, admin_id: int, action: str, target_type: str,
         target_id, detail: str = ""):
    db.add(AdminAuditLog(admin_id=admin_id, action=action,
                         target_type=target_type, target_id=target_id,
                         detail=detail))
    db.flush()


def _application_out(db: Session, a: MerchantApplication) -> dict:
    user = db.query(models.User).filter(models.User.id == a.user_id).first()
    return {
        "id": a.id, "user_id": a.user_id,
        "applicant_email": user.email if user else "",
        "shop_name": a.shop_name, "contact_name": a.contact_name or "",
        "phone": a.phone or "", "address": a.address or "",
        "business_license": a.business_license or "",
        "id_card": a.id_card or "",
        "shop_photos": a.shop_photos_list,
        "industry_license": a.industry_license or "",
        "status": a.status, "reject_reason": a.reject_reason or "",
        "reviewed_by": a.reviewed_by, "reviewed_at": a.reviewed_at,
        "created_at": a.created_at,
    }


def _page(items, page: int, per_page: int = 20):
    total = len(items)
    return {
        "current_page": page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "total": total,
        "items": items[(page - 1) * per_page: page * per_page],
    }


# ---------- merchant onboarding ----------
@router.post("/admin/merchant-applications", status_code=201)
def submit_application(body: schemas_line_b.MerchantApplicationCreateIn,
                       user: models.User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    pending = (db.query(MerchantApplication)
               .filter(MerchantApplication.user_id == user.id,
                       MerchantApplication.status == "pending").first())
    if pending is not None:
        raise HTTPException(status_code=400, detail="已有待審核的入駐申請")
    app = MerchantApplication(
        user_id=user.id, shop_name=body.shop_name,
        contact_name=body.contact_name, phone=body.phone,
        address=body.address, business_license=body.business_license,
        id_card=body.id_card,
        shop_photos=json.dumps(body.shop_photos, ensure_ascii=False),
        industry_license=body.industry_license, status="pending")
    db.add(app)
    db.commit()
    db.refresh(app)
    return {"message": "入駐申請已提交，請等待審核",
            "application": _application_out(db, app)}


@router.get("/admin/merchant-applications")
def list_applications(status: str = Query(None),
                      page: int = Query(1, ge=1),
                      admin: models.User = Depends(admin_only),
                      db: Session = Depends(get_db)):
    q = db.query(MerchantApplication).order_by(
        MerchantApplication.created_at.desc(), MerchantApplication.id.desc())
    if status in ("pending", "approved", "rejected"):
        q = q.filter(MerchantApplication.status == status)
    rows = q.all()
    out = _page([_application_out(db, a) for a in rows], page)
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
        MerchantApplication.id == application_id).first()
    if app is None:
        raise HTTPException(status_code=404, detail="找不到此入駐申請")
    if app.status != "pending":
        raise HTTPException(status_code=400, detail="此申請已審核，不可重複審核")
    if not body.approve and not body.reject_reason.strip():
        raise HTTPException(status_code=400, detail="拒絕時請填寫拒絕原因")
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
    out = _page(rows, page)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "shops": out["items"]}


@router.get("/admin/coupons")
def admin_coupons(status: str = Query(None), shop_id: int = Query(None),
                  page: int = Query(1, ge=1),
                  admin: models.User = Depends(admin_only),
                  db: Session = Depends(get_db)):
    q = db.query(models.Coupon).order_by(models.Coupon.id.desc())
    if status:
        q = q.filter(models.Coupon.status == status)
    if shop_id:
        q = q.filter(models.Coupon.shop_id == shop_id)
    rows = [coupon_out(db, c) for c in q.all()]
    out = _page(rows, page)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "coupons": out["items"]}


@router.get("/admin/redemptions")
def admin_redemptions(shop_id: int = Query(None), page: int = Query(1, ge=1),
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
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "wallet_item_id", "coupon_id", "coupon_name",
                    "shop_id", "shop_name", "scanned_by_vendor_id",
                    "scanned_at"])
        for row in rows:
            w.writerow([row[k] for k in
                        ("id", "wallet_item_id", "coupon_id", "coupon_name",
                         "shop_id", "shop_name", "scanned_by_vendor_id",
                         "scanned_at")])
        return PlainTextResponse(
            buf.getvalue(), media_type="text/csv",
            headers={"Content-Disposition":
                     "attachment; filename=redemptions.csv"})
    out = _page(rows, page)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "redemptions": out["items"]}


@router.get("/admin/users")
def admin_users(role: str = Query(None), page: int = Query(1, ge=1),
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
    out = _page(rows, page)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "users": out["items"]}


@router.get("/admin/audit-logs")
def admin_audit_logs(page: int = Query(1, ge=1),
                     admin: models.User = Depends(admin_only),
                     db: Session = Depends(get_db)):
    rows = (db.query(AdminAuditLog)
            .order_by(AdminAuditLog.created_at.desc(),
                      AdminAuditLog.id.desc()).all())
    out = _page([{"id": l.id, "admin_id": l.admin_id, "action": l.action,
                  "target_type": l.target_type, "target_id": l.target_id,
                  "detail": l.detail or "",
                  "created_at": l.created_at.isoformat()
                  if l.created_at else None} for l in rows], page)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "logs": out["items"]}
