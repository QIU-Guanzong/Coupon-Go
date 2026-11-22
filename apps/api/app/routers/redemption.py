"""Redemption hardening (line A, report §5.4 / §7.1 / §10 F7).

Two-step redemption: POST /new-scan-coupon/prepare (read-only check,
returns a short-lived prepare token) -> POST /new-scan-coupon (consume,
in routers/coupons.py; accepts idempotency_key + prepare_token).

This module adds:
- POST /new-scan-coupon/prepare — pre-redemption, read-only, no state change.
- POST /redemptions/{id}/revoke — merchant revoke within 10 minutes of
  scan (report §5.4 item 11: 美团 10 分钟窗口). The coupon returns to
  unused and the code is cleared, so the next present generates a fresh
  code (anti-screenshot rotation); the +20 redeem points are reversed.
  P1-1: the cashback this redemption earned is voided, and dine-in
  unlocks it triggered roll back to pending; already-withdrawn money
  is recorded as a negative adjustment (see
  cashback_service.void_for_revocation).
  M7: a review of this redemption is hidden and its +15 review points
  (if any) are clawed back (reviews.hide_reviews_for_revoked_redemption);
  the revoke response reports it under "review" (null when no review).
- GET /vendor/redemptions/export — vendor-scoped redemption records for
  reconciliation (F7-5; JSON default, ?format=csv). Admin sees all shops.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import config, errors, models, points, prepare_token
from ..cashback_service import lock_account, void_for_revocation
from ..settlement_ledger import reverse_sale
from ..database import get_db
from ..deps import require_role
from ..shared import csv_response, vendor_shop_ids
from .coupons import _refresh_expiry, ensure_paid_voucher, ensure_redeemable, presented_item
from .reviews import hide_reviews_for_revoked_redemption

router = APIRouter()

REVOKE_WINDOW = timedelta(minutes=config.REVOKE_WINDOW_MINUTES)


from ..timeutil import utcnow as _now


def _check_item(db: Session, item: models.WalletItem, vendor: models.User):
    """Shared eligibility check for prepare. Returns (coupon, reason|None)."""
    _refresh_expiry(db, item)
    coupon = db.query(models.Coupon).filter(
        models.Coupon.id == item.coupon_id).first()
    if item.status == 2:
        return coupon, "此優惠券已使用"
    if item.status == 3:
        return coupon, "此優惠券已失效"
    if item.qr_expires_at and item.qr_expires_at < _now():
        return coupon, "QR 碼已過期，請重新出示"
    if coupon is None or coupon.shop_id not in vendor_shop_ids(db, vendor):
        return coupon, "無效的 QR 碼！此碼屬於其他商店。"
    try:
        ensure_paid_voucher(db, item)
        ensure_redeemable(coupon, purchased_package=(item.source == "purchase" and coupon.kind == "package"))
    except HTTPException as exc:
        return coupon, exc.detail
    return coupon, None


def _phone_last4(phone):
    """Customer phone last-4 for the merchant check screen.

    Returns None when the customer has no phone on file — never invent
    digits. Non-digit characters (spaces, dashes) are stripped first.
    """
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return digits[-4:] if digits else None


@router.post("/new-scan-coupon/prepare")
def prepare_scan(body: dict,
                 vendor: models.User = Depends(require_role("vendor", "admin")),
                 db: Session = Depends(get_db)):
    code = (body or {}).get("code", "")
    item = presented_item(db, code)
    if item is None:
        return {"can_redeem": False, "reason": "無效的 QR 碼！",
                "prepare_token": None, "wallet_item_id": None}
    coupon, reason = _check_item(db, item, vendor)
    if reason is not None:
        return {"can_redeem": False, "reason": reason,
                "prepare_token": None, "wallet_item_id": item.id,
                "coupon_name": coupon.coupon_name if coupon else "",
                "shop_id": coupon.shop_id if coupon else None}
    token, _issued = prepare_token.issue_prepare_token(item.id, binding=f"{vendor.id}:{code}")
    shop = db.query(models.Shop).filter(
        models.Shop.id == coupon.shop_id).first()
    # Gap 5: customer phone last-4 (User.phone, via the wallet owner)
    # and the coupon validity (Coupon.valid_to). The merchant page reads
    # customer_phone_last4 / valid_to; phone_last4 / valid_until /
    # expires_at are the same values under the task's names.
    owner = db.query(models.User).filter(
        models.User.id == item.user_id).first()
    last4 = _phone_last4(owner.phone if owner else None)
    valid_to = coupon.valid_to.isoformat() if coupon.valid_to else None
    order = db.get(models.Order, item.order_id) if item.source == "purchase" and item.order_id else None
    return {"can_redeem": True, "reason": "",
            "package_quantity": (order.quantity or 1) if order else None,
            "prepare_token": token, "wallet_item_id": item.id,
            "coupon_name": coupon.coupon_name,
            "shop_id": coupon.shop_id,
            "shop_name": shop.name if shop else "",
            "phone_last4": last4,
            "customer_phone_last4": last4,
            "valid_until": valid_to,
            "expires_at": valid_to,
            "valid_to": valid_to,
            "expires_in_seconds": config.PREPARE_TOKEN_TTL_SECONDS}


@router.post("/redemptions/{redemption_id}/revoke")
def revoke_redemption(redemption_id: int, acknowledge_existing: bool = False,
                      vendor: models.User = Depends(
                          require_role("vendor", "admin")),
                      db: Session = Depends(get_db)):
    # FIX-3: lock the redemption row before the revoked_at check so a
    # second concurrent revoke blocks here and sees revoked_at set.
    # Serialize revocations, while allowing a statement being generated to
    # take its foreign-key reference to this redemption before we credit it.
    red = (db.query(models.Redemption).filter(
        models.Redemption.id == redemption_id)
        .with_for_update(key_share=True).populate_existing().first())
    if red is None:
        raise errors.redemption_not_found()
    if vendor.role != "admin" and red.shop_id not in vendor_shop_ids(db, vendor):
        raise errors.forbidden()
    now = _now()
    if red.revoked_at is not None:
        if acknowledge_existing:
            return {"message": "此筆核銷已撤銷", "redemption_id": red.id,
                    "wallet_item_id": red.wallet_item_id, "revoked_at": red.revoked_at}
        raise errors.redemption_already_revoked()
    if now - red.scanned_at > REVOKE_WINDOW:
        raise errors.revoke_window_expired(config.REVOKE_WINDOW_MINUTES)

    # The conditional write also serializes duplicate requests on SQLite.
    marked = db.query(models.Redemption).filter_by(id=red.id, revoked_at=None).update(
        {"revoked_at": now, "revoked_by": vendor.id}, synchronize_session=False)
    if not marked:
        if acknowledge_existing:
            db.refresh(red)
            return {"message": "此筆核銷已撤銷", "redemption_id": red.id,
                    "wallet_item_id": red.wallet_item_id, "revoked_at": red.revoked_at}
        raise errors.redemption_already_revoked()
    item = db.query(models.WalletItem).filter_by(id=red.wallet_item_id).with_for_update().one()
    lock_account(db, item.user_id)
    item.status = 3 if item.coupon.valid_to <= now else 1
    item.used_at = None
    item.code = ""
    item.qr_expires_at = None
    db.query(models.Coupon).filter(models.Coupon.id == red.coupon_id,
                                   models.Coupon.used_count > 0).update(
        {models.Coupon.used_count: models.Coupon.used_count - 1}, synchronize_session=False)
    # Refund the actual ledger award even if the points policy changed since the scan.
    points.clawback_points(db, item.user_id, "redeem", item.id,
                           revoke_reason="revoke", revoke_ref_id=red.id)
    # P1-1: void this redemption's own cashback earning and roll back
    # the dine-in unlocks it triggered; already-withdrawn money is
    # recorded as a negative adjustment (never a negative transaction).
    cashback = void_for_revocation(db, red)
    # M7: a review of this redemption is hidden and its +15 review
    # points (if any) are clawed back; no review -> None.
    review = hide_reviews_for_revoked_redemption(db, red)
    reverse_sale(db, red, "revoked")
    db.commit()
    return {"message": "核銷已撤銷，優惠券已到期" if item.status == 3 else "核銷已撤銷，優惠券已退回未使用",
            "wallet_status": item.status,
            "redemption_id": red.id,
            "wallet_item_id": red.wallet_item_id,
            "revoked_at": red.revoked_at,
            "revoke_window_minutes": config.REVOKE_WINDOW_MINUTES,
            "cashback": cashback,
            "review": review}


@router.get("/vendor/redemptions/export")
def export_redemptions(format: str = Query("json", pattern="^(json|csv)$"),
                       from_: str | None = Query(None, alias="from"),
                       to: str | None = Query(None),
                       coupon_id: int | None = Query(None),
                       revoked: bool | None = Query(None),
                       vendor: models.User = Depends(
                           require_role("vendor", "admin")),
                       db: Session = Depends(get_db)):
    """Redemption export (GAP §4.5 filters, §5.9 shared CSV writer).

    ``from`` / ``to`` are ``YYYY-MM-DD`` on ``scanned_at``, inclusive
    on both ends; ``coupon_id`` narrows to one coupon; ``revoked``
    to revoked / not-revoked only. JSON shape is unchanged; CSV uses
    the one shared writer (``shared.csv_response``) with the same
    column order as before — quoting is now correct for names that
    contain commas.
    """
    try:
        from_date = (datetime.strptime(from_, "%Y-%m-%d")
                     if from_ else None)
        to_date = (datetime.strptime(to, "%Y-%m-%d")
                   if to else None)
    except ValueError:
        raise errors.bad_request("日期格式不正確，請用 YYYY-MM-DD")
    shop_ids = None if vendor.role == "admin" else vendor_shop_ids(db, vendor)
    q = db.query(models.Redemption)
    if shop_ids is not None:
        q = q.filter(models.Redemption.shop_id.in_(shop_ids)) if shop_ids else q.filter(False)
    if from_date is not None:
        q = q.filter(models.Redemption.scanned_at >= from_date)
    if to_date is not None:
        q = q.filter(models.Redemption.scanned_at
                     < to_date + timedelta(days=1))
    if coupon_id is not None:
        q = q.filter(models.Redemption.coupon_id == coupon_id)
    if revoked is True:
        q = q.filter(models.Redemption.revoked_at.is_not(None))
    elif revoked is False:
        q = q.filter(models.Redemption.revoked_at.is_(None))
    reds = q.order_by(models.Redemption.scanned_at.desc()).all()
    records = []
    for r in reds:
        c = db.query(models.Coupon).filter(models.Coupon.id == r.coupon_id).first()
        s = db.query(models.Shop).filter(models.Shop.id == r.shop_id).first()
        records.append({
            "redemption_id": r.id,
            "wallet_item_id": r.wallet_item_id,
            "coupon_name": c.coupon_name if c else "",
            "shop_id": r.shop_id,
            "shop_name": s.name if s else "",
            "scanned_at": r.scanned_at.isoformat() if r.scanned_at else None,
            "revoke_until": (r.scanned_at + REVOKE_WINDOW).replace(tzinfo=timezone.utc).isoformat(),
            "scanned_by_vendor_id": r.scanned_by_vendor_id,
            "revoked": r.revoked_at is not None,
            "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
            "revoked_by": r.revoked_by,
            "idempotency_key": r.idempotency_key,
        })
    by_shop: dict[str, int] = {}
    for rec in records:
        by_shop[rec["shop_name"]] = by_shop.get(rec["shop_name"], 0) + 1
    if format == "csv":
        columns = ("redemption_id", "wallet_item_id", "coupon_name",
                   "shop_name", "scanned_at", "revoked", "revoked_at",
                   "idempotency_key")
        rows = [[str(rec[k] if rec[k] is not None else "")
                 for k in columns] for rec in records]
        return csv_response("redemptions.csv", list(columns), rows)
    return {"total": len(records),
            "revoked_count": sum(1 for r in records if r["revoked"]),
            "by_shop": by_shop,
            "records": records}
