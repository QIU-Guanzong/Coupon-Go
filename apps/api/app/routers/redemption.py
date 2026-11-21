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
- Dianping §4 (2026-11-21):
  - GET /merchant/redemptions/lookup — find an unused item by phone /
    code suffix / order id so a forgotten scan can be recovered (§4.1).
  - POST /merchant/redemptions/supplementary-scan — after-the-fact scan
    with no presented code, through the same consume chain as
    scan_coupon, with an AdminAuditLog entry (§4.1).
  - POST /admin/redemptions/{id}/force-revoke — over-window revoke by
    admin / support with a mandatory reason + audit log (§4.2). The
    reversal itself is the same _reverse_redemption as the 10-minute
    revoke; only the time check is waived.
  - POST /new-scan-coupon/batch — consume several presented codes in
    one transaction, all-or-nothing, one batch idempotency key (§4.3).
  - The export gains ?from=&to=&coupon_id=&revoked=&method= filters and
    reports the redemption method (§4.4) and the operator name (§4.4).
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session

from .. import config, models, prepare_token, schemas
from ..cashback_service import lock_account, void_for_revocation
from ..models_line_b import AdminAuditLog
from ..settlement_ledger import reverse_sale
from ..database import get_db
from ..deps import add_points, require_role
from .coupons import (_duplicate_response, _prior_redemption, _redeem_item,
                      _refresh_expiry, ensure_paid_voucher, ensure_redeemable,
                      presented_item)
from .reviews import hide_reviews_for_revoked_redemption

router = APIRouter()

REVOKE_WINDOW = timedelta(minutes=config.REVOKE_WINDOW_MINUTES)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _vendor_shop_ids(db: Session, vendor: models.User):
    return [s.id for s in db.query(models.Shop).filter(
        models.Shop.vendor_id == vendor.id).all()]


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
    if coupon is None or coupon.shop_id not in _vendor_shop_ids(db, vendor):
        return coupon, "無效的 QR 碼！此碼屬於其他商店。"
    try:
        ensure_paid_voucher(db, item)
        ensure_redeemable(coupon)
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


def _log(db: Session, actor_id: int, action: str, target_type: str,
         target_id, detail: str = ""):
    """One append-only audit row (same table admin_v2._log writes).

    The actor is whoever performed the override — a vendor for a
    supplementary scan, admin / support for a force-revoke — so the
    paper trail names a real user id even though the column is called
    admin_id. No commit: the caller commits together with the change.
    """
    db.add(AdminAuditLog(admin_id=actor_id, action=action,
                         target_type=target_type, target_id=target_id,
                         detail=detail))
    db.flush()


def _actor_shop_ids(db: Session, user: models.User):
    """Shop scope for the new endpoints: admin sees all (None), a vendor
    sees only their own shops — the same rule export_redemptions uses.
    (scan_coupon itself keeps its own stricter rule: it passes the
    vendor's shop list, so an admin scan is refused exactly as today.)
    """
    return None if user.role == "admin" else _vendor_shop_ids(db, user)


def _validate_item(db: Session, item: models.WalletItem, shop_ids):
    """Read-only eligibility for batch pre-validation. Returns
    (coupon, reason|None). Mirrors _check_item but takes an explicit
    shop scope (None = admin, no restriction) instead of reading the
    vendor's shops itself.
    """
    _refresh_expiry(db, item)
    coupon = db.query(models.Coupon).filter(
        models.Coupon.id == item.coupon_id).first()
    if item.status == 2:
        return coupon, "此優惠券已使用"
    if item.status == 3:
        return coupon, "此優惠券已失效"
    if item.qr_expires_at and item.qr_expires_at < _now():
        return coupon, "QR 碼已過期，請重新出示"
    if coupon is None or (shop_ids is not None and coupon.shop_id not in shop_ids):
        return coupon, "無效的 QR 碼！此碼屬於其他商店。"
    try:
        ensure_paid_voucher(db, item)
        ensure_redeemable(coupon)
    except HTTPException as exc:
        return coupon, exc.detail
    return coupon, None


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


def _reverse_redemption(db: Session, red: models.Redemption,
                        actor: models.User, now):
    """Apply the full reversal for one redemption row.

    Shared by the 10-minute merchant revoke and the over-window
    force-revoke (§4.2): the reversal is identical, only the time check
    differs. Returns the reversal summary dict, or None when a
    concurrent request already revoked this row (the caller decides the
    acknowledge_existing behaviour). No commit — the caller owns the
    transaction so force-revoke can add its audit row in the same
    commit. The bulk UPDATE is the concurrency guard (FIX-3 pattern);
    the ORM attributes are set to the same values so the returned
    summary reads the new state before the commit.
    """
    # The conditional write also serializes duplicate requests on SQLite.
    marked = db.query(models.Redemption).filter_by(id=red.id, revoked_at=None).update(
        {"revoked_at": now, "revoked_by": actor.id}, synchronize_session=False)
    if not marked:
        return None
    red.revoked_at = now
    red.revoked_by = actor.id
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
    award = db.query(models.PointTransaction).filter_by(
        user_id=item.user_id, reason="redeem", ref_id=item.id
    ).order_by(models.PointTransaction.id.desc()).first()
    if award is not None:
        add_points(db, item.user_id, -award.delta, "revoke", ref_id=red.id)
    # P1-1: void this redemption's own cashback earning and roll back
    # the dine-in unlocks it triggered; already-withdrawn money is
    # recorded as a negative adjustment (never a negative transaction).
    cashback = void_for_revocation(db, red)
    # M7: a review of this redemption is hidden and its +15 review
    # points (if any) are clawed back; no review -> None.
    review = hide_reviews_for_revoked_redemption(db, red)
    reverse_sale(db, red, "revoked")
    return {"message": "核銷已撤銷，優惠券已到期" if item.status == 3 else "核銷已撤銷，優惠券已退回未使用",
            "wallet_status": item.status,
            "redemption_id": red.id,
            "wallet_item_id": red.wallet_item_id,
            "revoked_at": red.revoked_at,
            "revoke_window_minutes": config.REVOKE_WINDOW_MINUTES,
            "cashback": cashback,
            "review": review}


def _already_revoked_response(red: models.Redemption):
    return {"message": "此筆核銷已撤銷", "redemption_id": red.id,
            "wallet_item_id": red.wallet_item_id, "revoked_at": red.revoked_at}


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
        raise HTTPException(status_code=404, detail="找不到此核銷記錄")
    if vendor.role != "admin" and red.shop_id not in _vendor_shop_ids(db, vendor):
        raise HTTPException(403, "沒有權限")
    now = _now()
    if red.revoked_at is not None:
        if acknowledge_existing:
            return _already_revoked_response(red)
        raise HTTPException(400, "此核銷已被撤销")
    if now - red.scanned_at > REVOKE_WINDOW:
        raise HTTPException(400, f"已超過 {config.REVOKE_WINDOW_MINUTES} 分鐘撤销窗口，請聯絡客服處理")

    result = _reverse_redemption(db, red, vendor, now)
    if result is None:
        if acknowledge_existing:
            db.refresh(red)
            return _already_revoked_response(red)
        raise HTTPException(400, "此核銷已被撤销")
    db.commit()
    return result


@router.post("/admin/redemptions/{redemption_id}/force-revoke")
def force_revoke_redemption(redemption_id: int, body: schemas.ForceRevokeIn,
                            admin: models.User = Depends(
                                require_role("admin", "support")),
                            db: Session = Depends(get_db)):
    """Dianping §4.2: over-window revoke by admin / support (客服代處理).

    Same reversal as the 10-minute revoke, with only the time check
    waived — coupon restored, redeem points / cashback / review / ledger
    all reversed by _reverse_redemption. A reason is mandatory and is
    written to AdminAuditLog with the original scan time, so the
    override is reviewable. Merchants have no self-serve path past the
    window: their button becomes "申請客服撤销" and support runs this.
    """
    reason = (body.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="請填寫撤销原因")
    red = (db.query(models.Redemption).filter(
        models.Redemption.id == redemption_id)
        .with_for_update(key_share=True).populate_existing().first())
    if red is None:
        raise HTTPException(status_code=404, detail="找不到此核銷記錄")
    now = _now()
    if red.revoked_at is not None:
        raise HTTPException(400, "此核銷已被撤销")
    minutes_over = max(
        0, int((now - red.scanned_at).total_seconds() // 60)
        - config.REVOKE_WINDOW_MINUTES)
    result = _reverse_redemption(db, red, admin, now)
    if result is None:
        raise HTTPException(400, "此核銷已被撤销")
    _log(db, admin.id, "redemption.force_revoke", "redemption", red.id,
         f"reason={reason} | scanned_at={red.scanned_at.isoformat()} | "
         f"minutes_over_window={minutes_over} | by_role={admin.role}")
    db.commit()
    return {**result, "forced": True, "reason": reason,
            "minutes_over_window": minutes_over}


# ---------- Dianping §4.1: code lookup + supplementary scan ----------

@router.get("/merchant/redemptions/lookup")
def lookup_redemption(phone: str = Query(None), code_suffix: str = Query(None),
                      order_id: int = Query(None),
                      vendor: models.User = Depends(
                          require_role("vendor", "admin")),
                      db: Session = Depends(get_db)):
    """Find an unused item so a forgotten scan can be recovered (§4.1).

    The merchant asks the customer for one verifiable identity — the
    phone number, the last digits of the presented code, or the package
    order id — and gets back the unused items of their own shops. The
    response never contains the full phone number or the live code: the
    merchant confirms the customer by the last-4 + coupon name + expiry,
    then consumes the item via supplementary-scan. At least one
    parameter is required.
    """
    if not phone and not code_suffix and order_id is None:
        raise HTTPException(
            status_code=400, detail="請提供 phone、code_suffix 或 order_id")
    shop_ids = _actor_shop_ids(db, vendor)
    q = db.query(models.WalletItem).join(
        models.Coupon, models.Coupon.id == models.WalletItem.coupon_id
    ).filter(models.WalletItem.status == 1)
    if shop_ids is not None:
        q = q.filter(models.Coupon.shop_id.in_(shop_ids)) if shop_ids else q.filter(False)
    if order_id is not None:
        q = q.filter(models.WalletItem.order_id == order_id)
    if code_suffix:
        suffix = "".join(ch for ch in code_suffix if ch.isdigit())
        if not suffix or len(suffix) > 6:
            raise HTTPException(status_code=400, detail="code_suffix 須為 1–6 位數字")
        q = q.filter(models.WalletItem.code.like(f"%{suffix}"))
    phone_digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    items = q.order_by(models.WalletItem.id).all()
    out = []
    for item in items:
        _refresh_expiry(db, item)
        if item.status != 1:
            continue
        coupon = item.coupon
        owner = db.query(models.User).filter(
            models.User.id == item.user_id).first()
        if phone:
            owner_digits = "".join(ch for ch in (owner.phone or "") if ch.isdigit())
            if not phone_digits or owner_digits != phone_digits:
                continue
        shop = db.query(models.Shop).filter(
            models.Shop.id == coupon.shop_id).first()
        order = db.get(models.Order, item.order_id) if item.source == "purchase" and item.order_id else None
        out.append({
            "wallet_item_id": item.id,
            "coupon_id": coupon.id,
            "coupon_name": coupon.coupon_name,
            "shop_id": coupon.shop_id,
            "shop_name": shop.name if shop else "",
            "customer_phone_last4": _phone_last4(owner.phone if owner else None),
            "valid_to": coupon.valid_to.isoformat() if coupon.valid_to else None,
            "order_id": item.order_id,
            "package_quantity": (order.quantity or 1) if order else None,
            "source": item.source,
            "status": item.status,
        })
    return {"items": out}


@router.post("/merchant/redemptions/supplementary-scan")
def supplementary_scan(body: schemas.SupplementaryScanIn,
                       vendor: models.User = Depends(
                           require_role("vendor", "admin")),
                       db: Session = Depends(get_db)):
    """After-the-fact scan (§4.1): the customer left without scanning.

    No presented code and no prepare token are needed — the merchant
    found the item via the lookup above and verified the customer some
    other way (phone / order). The consume itself is the SAME chain as
    scan_coupon (_redeem_item: shop + paid-voucher + redeemable checks,
    conditional consume, settlement_basis snapshot, redeem points,
    cashback, ledger), so a supplementary scan settles exactly like a
    normal scan. It is marked method="supplementary" and an
    AdminAuditLog entry records who did it and why, so the override is
    reviewable. Idempotent on idempotency_key, like scan_coupon.
    """
    item = db.query(models.WalletItem).filter_by(
        id=body.wallet_item_id).with_for_update().first()
    if item is None:
        raise HTTPException(status_code=404, detail="找不到此優惠券")
    shop_ids = _actor_shop_ids(db, vendor)
    if shop_ids is not None and item.coupon.shop_id not in shop_ids:
        raise HTTPException(403, "沒有權限")
    replay = _prior_redemption(db, body.idempotency_key, vendor)
    if replay is not None:
        resp = _duplicate_response(db, replay)
        resp["method"] = "supplementary"
        return resp
    red = _redeem_item(db, vendor, item,
                       idempotency_key=body.idempotency_key,
                       method="supplementary", note=body.note,
                       shop_ids=shop_ids)
    reason = (body.reason or "").strip() or "顧客離店後補核銷"
    _log(db, vendor.id, "redemption.supplementary_scan", "redemption", red.id,
         f"wallet_item_id={item.id} | reason={reason} | by_vendor={vendor.id}")
    db.commit()
    db.refresh(red)
    return {"message": "您的優惠券已成功使用",
            "coupon_name": db.get(models.Coupon, red.coupon_id).coupon_name,
            "shop_id": red.shop_id, "redemption_id": red.id,
            "wallet_item_id": red.wallet_item_id,
            "revoke_window_minutes": config.REVOKE_WINDOW_MINUTES,
            "scanned_at": red.scanned_at.replace(tzinfo=timezone.utc),
            "duplicate": False, "method": "supplementary", "note": red.note}


# ---------- Dianping §4.3: batch scan ----------

@router.post("/new-scan-coupon/batch")
def batch_scan(body: schemas.ScanBatchIn,
               vendor: models.User = Depends(require_role("vendor", "admin")),
               db: Session = Depends(get_db)):
    """Consume several presented codes in one call (§4.3).

    A family with a multi-person package, or a customer holding several
    coupons, is scanned once instead of once per code. One transaction:
    phase 1 resolves and validates every code (prepare tokens, when
    given, must line up with the codes); if any code fails, NOTHING is
    consumed and the 400 response reports every code as ok / error /
    duplicate. Phase 2 consumes each item through the same _redeem_item
    chain as scan_coupon (same checks, same hooks, same conditional
    consume). Idempotency: the batch key is required; item i is recorded
    under f"{idempotency_key}:{i}", so replaying the identical request
    returns the original redemptions (duplicate_count) instead of
    consuming again. A batch that replays with different codes in a
    different order is a new batch — send the identical request to
    replay. Capped at 20 codes per call.
    """
    if body.prepare_tokens is not None and len(body.prepare_tokens) != len(body.codes):
        raise HTTPException(
            status_code=422, detail="prepare_tokens 須與 codes 一一對應")
    shop_ids = _actor_shop_ids(db, vendor)
    results: list[dict] = []
    to_consume: list[tuple] = []  # (index, code, item, prepared_at)
    for i, code in enumerate(body.codes):
        key = f"{body.idempotency_key}:{i}"
        prior = _prior_redemption(db, key, vendor)
        if prior is not None:
            results.append({"code": code, "status": "duplicate",
                            "wallet_item_id": prior.wallet_item_id,
                            "redemption_id": prior.id,
                            "coupon_name": db.get(models.Coupon, prior.coupon_id).coupon_name,
                            "duplicate": True})
            continue
        item = presented_item(db, code, lock=True)
        if item is None:
            results.append({"code": code, "status": "error",
                            "reason": "無效的 QR 碼！",
                            "wallet_item_id": None})
            continue
        _coupon, reason = _validate_item(db, item, shop_ids)
        prepared_at = None
        if reason is None and body.prepare_tokens is not None:
            issued = prepare_token.verify_prepare_token(
                body.prepare_tokens[i], item.id, binding=f"{vendor.id}:{code}")
            if issued is None:
                reason = "預核銷已失效，請重新預核銷"
            else:
                prepared_at = datetime.fromtimestamp(
                    issued, tz=timezone.utc).replace(tzinfo=None)
        if reason is not None:
            results.append({"code": code, "status": "error", "reason": reason,
                            "wallet_item_id": item.id})
            continue
        results.append({"code": code, "status": "ok", "wallet_item_id": item.id})
        to_consume.append((i, code, item, prepared_at))
    if any(res["status"] == "error" for res in results):
        db.rollback()
        return JSONResponse(status_code=400, content={
            "total": len(body.codes), "redeemed_count": 0,
            "duplicate_count": sum(1 for res in results if res["status"] == "duplicate"),
            "results": results})
    # Phase 2 — consume. Any failure here (a race consumed the item
    # after phase 1) rolls the whole batch back: nothing is half-done.
    redeemed: list[dict] = []
    try:
        for i, code, item, prepared_at in to_consume:
            key = f"{body.idempotency_key}:{i}"
            red = _redeem_item(db, vendor, item, code=code,
                               prepared_at=prepared_at, idempotency_key=key,
                               method="batch", note=body.note, shop_ids=shop_ids)
            db.flush()
            redeemed.append({"code": code, "status": "redeemed",
                             "wallet_item_id": item.id, "redemption_id": red.id,
                             "coupon_name": db.get(models.Coupon, red.coupon_id).coupon_name,
                             "duplicate": False})
    except HTTPException as exc:
        db.rollback()
        failed_code = code
        for res in results:
            if res["status"] == "ok":
                res["status"] = "error" if res["code"] == failed_code else "ok"
                if res["code"] == failed_code:
                    res["reason"] = exc.detail
        return JSONResponse(status_code=400, content={
            "total": len(body.codes), "redeemed_count": 0,
            "duplicate_count": sum(1 for res in results if res["status"] == "duplicate"),
            "results": results})
    if redeemed:
        _log(db, vendor.id, "redemption.batch_scan", "redemption",
             redeemed[0]["redemption_id"],
             f"batch_key={body.idempotency_key} | count={len(redeemed)} | "
             f"redemption_ids={[r['redemption_id'] for r in redeemed]}")
    db.commit()
    final = []
    for res in results:
        if res["status"] == "ok":
            final.append(next(r for r in redeemed if r["code"] == res["code"]))
        else:
            final.append(res)
    return {"total": len(body.codes), "redeemed_count": len(redeemed),
            "duplicate_count": sum(1 for res in results if res["status"] == "duplicate"),
            "results": final}


@router.get("/vendor/redemptions/export")
def export_redemptions(format: str = Query("json", pattern="^(json|csv)$"),
                       date_from: str = Query(None, alias="from"),
                       date_to: str = Query(None, alias="to"),
                       coupon_id: int = Query(None),
                       revoked: bool = Query(None),
                       method: str = Query(None),
                       vendor: models.User = Depends(
                           require_role("vendor", "admin")),
                       db: Session = Depends(get_db)):
    """Vendor redemption records for reconciliation (§4.4 / §4.5).

    Filters: from/to are YYYY-MM-DD dates (inclusive, on scanned_at),
    coupon_id narrows to one coupon, revoked narrows to revoked /
    not-revoked, method narrows to scan / manual / supplementary /
    batch. Records carry the redemption method and the operator name so
    a handover can tell who scanned what, and how.
    """
    shop_ids = None if vendor.role == "admin" else _vendor_shop_ids(db, vendor)
    q = db.query(models.Redemption)
    if shop_ids is not None:
        q = q.filter(models.Redemption.shop_id.in_(shop_ids)) if shop_ids else q.filter(False)
    if date_from:
        try:
            start = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="from 須為 YYYY-MM-DD")
        q = q.filter(models.Redemption.scanned_at >= start)
    if date_to:
        try:
            end = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
        except ValueError:
            raise HTTPException(status_code=400, detail="to 須為 YYYY-MM-DD")
        q = q.filter(models.Redemption.scanned_at < end)
    if coupon_id is not None:
        q = q.filter(models.Redemption.coupon_id == coupon_id)
    if revoked is not None:
        q = q.filter(models.Redemption.revoked_at.is_not(None) if revoked
                     else models.Redemption.revoked_at.is_(None))
    if method is not None:
        q = q.filter(models.Redemption.method == method)
    reds = q.order_by(models.Redemption.scanned_at.desc()).all()
    names: dict[int, str] = {}

    def _operator_name(user_id):
        if user_id not in names:
            u = db.query(models.User).filter(models.User.id == user_id).first()
            names[user_id] = u.name if u else ""
        return names[user_id]

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
            "scanned_by_name": _operator_name(r.scanned_by_vendor_id),
            "method": r.method or "scan",
            "note": r.note,
            "revoked": r.revoked_at is not None,
            "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
            "revoked_by": r.revoked_by,
            "idempotency_key": r.idempotency_key,
        })
    by_shop: dict[str, int] = {}
    for rec in records:
        by_shop[rec["shop_name"]] = by_shop.get(rec["shop_name"], 0) + 1
    if format == "csv":
        lines = ["redemption_id,wallet_item_id,coupon_name,shop_name,"
                 "scanned_at,method,scanned_by_name,revoked,revoked_at,idempotency_key"]
        for rec in records:
            lines.append(",".join(str(rec[k] if rec[k] is not None else "")
                                  for k in ("redemption_id", "wallet_item_id",
                                            "coupon_name", "shop_name",
                                            "scanned_at", "method",
                                            "scanned_by_name", "revoked",
                                            "revoked_at", "idempotency_key")))
        return PlainTextResponse("\n".join(lines),
                                 media_type="text/csv; charset=utf-8")
    return {"total": len(records),
            "revoked_count": sum(1 for r in records if r["revoked"]),
            "by_shop": by_shop,
            "records": records}
