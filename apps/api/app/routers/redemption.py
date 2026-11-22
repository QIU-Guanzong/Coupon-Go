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
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import config, connect_service, errors, models, points, prepare_token, schemas
from ..cashback_service import lock_account, void_for_revocation
from ..settlement_ledger import lock_shop, reverse_sale
from ..database import get_db
from ..deps import get_current_user, require_role
from ..permissions_line_f import SHOP_ROLE_PERMISSIONS, can_redeem_shop, shop_member_role
from ..serializers import coupon_branch_ids
from ..shared import audit_log, csv_response, vendor_shop_ids
from .coupons import _redeem_item, _refresh_expiry, check_redemption_branch, ensure_paid_voucher, ensure_redeemable, presented_item
from .reviews import hide_reviews_for_revoked_redemption

router = APIRouter()

REVOKE_WINDOW = timedelta(minutes=config.REVOKE_WINDOW_MINUTES)


from ..timeutil import utcnow as _now


def _check_item(db: Session, item: models.WalletItem, vendor: models.User, branch_id=None):
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
    if coupon is None:
        return coupon, "無效的 QR 碼！此碼屬於其他商店。"
    shop = db.get(models.Shop, coupon.shop_id)
    if shop is None or not can_redeem_shop(db, vendor, shop):
        if shop is not None and shop_member_role(db, vendor, shop) is not None:
            return coupon, "沒有核銷權限"
        return coupon, "無效的 QR 碼！此碼屬於其他商店。"
    reason, _allowed = check_redemption_branch(db, coupon, branch_id)
    if reason:
        return coupon, reason
    try:
        ensure_paid_voucher(db, item)
        ensure_redeemable(coupon, purchased_order=item.source == "purchase")
        connect_service.ensure_prior_transfer_closed(db, item.id)
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
                 vendor: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    code = (body or {}).get("code", "")
    item = presented_item(db, code)
    if item is None:
        return {"can_redeem": False, "reason": "無效的 QR 碼！",
                "prepare_token": None, "wallet_item_id": None}
    branch_id = (body or {}).get("branch_id")
    coupon, reason = _check_item(db, item, vendor, branch_id=branch_id)
    shop = db.get(models.Shop, coupon.shop_id) if coupon else None
    allowed = (coupon_branch_ids(db, coupon.id)
               if shop and can_redeem_shop(db, vendor, shop) else [])
    branches = (db.query(models.Branch).filter(
        models.Branch.shop_id == shop.id, models.Branch.id.in_(allowed)
    ).order_by(models.Branch.id).all() if allowed else [])
    options = [{"id": b.id, "name": b.name, "address": b.address or ""}
               for b in branches]
    if reason is not None:
        return {"can_redeem": False, "reason": reason,
                "prepare_token": None, "wallet_item_id": item.id,
                "coupon_name": coupon.coupon_name if coupon else "",
                "shop_id": coupon.shop_id if coupon else None,
                "branch_selection_required": bool(
                    allowed and branch_id is None
                    and reason == "此優惠券限指定分店使用，請先選擇分店"),
                "allowed_branches": options}
    token, _issued = prepare_token.issue_prepare_token(
        item.id, binding=prepare_token.redemption_binding(vendor.id, code, branch_id))
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
            "shop_name": shop.name if shop else "", "branch_id": branch_id,
            "branch_selection_required": False, "allowed_branches": options,
            "phone_last4": last4,
            "customer_phone_last4": last4,
            "valid_until": valid_to,
            "expires_at": valid_to,
            "valid_to": valid_to,
            "expires_in_seconds": config.PREPARE_TOKEN_TTL_SECONDS}


def _revoke_redemption(db: Session, redemption_id: int, vendor: models.User,
                       acknowledge_existing: bool = False, force_reason: str | None = None):
    # Discover identity without locking, then use the same customer -> order
    # -> wallet -> redemption order as scan/refund before rechecking state.
    candidate = (db.query(models.Redemption.wallet_item_id, models.Redemption.shop_id)
                 .filter_by(id=redemption_id).first())
    if candidate is None:
        raise errors.redemption_not_found()
    if force_reason is None and vendor.role != "admin" and candidate.shop_id not in vendor_shop_ids(db, vendor):
        raise errors.forbidden()
    owner = (db.query(models.WalletItem.user_id, models.WalletItem.order_id)
             .filter_by(id=candidate.wallet_item_id).first())
    if owner is None:
        raise errors.redemption_not_found()
    lock_account(db, owner.user_id)
    if owner.order_id is not None:
        from ..order_allocation import ensure_order_allocations
        ensure_order_allocations(db, owner.order_id)
    item = (db.query(models.WalletItem).filter_by(id=candidate.wallet_item_id)
            .with_for_update().populate_existing().one())
    if item.user_id != owner.user_id or item.order_id != owner.order_id:
        raise HTTPException(409, "券資料需要核對")
    red = (db.query(models.Redemption).filter(
        models.Redemption.id == redemption_id)
        .with_for_update(key_share=True).populate_existing().first())
    if red is None:
        raise errors.redemption_not_found()
    if force_reason is None and vendor.role != "admin" and red.shop_id not in vendor_shop_ids(db, vendor):
        raise errors.forbidden()
    now = _now()
    if red.revoked_at is not None:
        if acknowledge_existing:
            return {"message": "此筆核銷已撤銷", "redemption_id": red.id,
                    "wallet_item_id": red.wallet_item_id, "revoked_at": red.revoked_at}
        raise errors.redemption_already_revoked()
    if force_reason is None and now - red.scanned_at > REVOKE_WINDOW:
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
    if red.wallet_item_id != item.id:
        raise HTTPException(409, "核銷資料需要核對")
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
    connect_reversal = connect_service.reverse_redemption(db, red, "revoked")
    minutes_over = max(0, int((now - red.scanned_at).total_seconds() // 60)
                       - config.REVOKE_WINDOW_MINUTES)
    if force_reason is not None:
        audit_log(db, vendor.id, "redemption.force_revoke", "redemption", red.id,
                  f"reason={force_reason} | scanned_at={red.scanned_at.isoformat()} | "
                  f"minutes_over_window={minutes_over} | by_role={vendor.role}")
    db.commit()
    result = {"message": "核銷已撤銷，優惠券已到期" if item.status == 3 else "核銷已撤銷，優惠券已退回未使用",
            "wallet_status": item.status,
            "redemption_id": red.id,
            "wallet_item_id": red.wallet_item_id,
            "revoked_at": red.revoked_at,
            "revoke_window_minutes": config.REVOKE_WINDOW_MINUTES,
            "cashback": cashback,
            "review": review, "connect": connect_reversal}
    if force_reason is not None:
        result.update(forced=True, reason=force_reason, minutes_over_window=minutes_over)
    return result


@router.post("/redemptions/{redemption_id}/revoke")
def revoke_redemption(redemption_id: int, acknowledge_existing: bool = False,
                      vendor: models.User = Depends(require_role("vendor", "admin")),
                      db: Session = Depends(get_db)):
    return _revoke_redemption(db, redemption_id, vendor, acknowledge_existing)


@router.post("/admin/redemptions/{redemption_id}/force-revoke")
def force_revoke_redemption(redemption_id: int, body: schemas.ForceRevokeIn,
                            admin: models.User = Depends(require_role("admin", "support")),
                            db: Session = Depends(get_db)):
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(400, "請填寫撤銷原因")
    return _revoke_redemption(db, redemption_id, admin, force_reason=reason)


def _require_redemption_merchant(db, user):
    if user.role == 'admin' or db.query(models.Shop.id).filter_by(vendor_id=user.id).first():
        return
    roles = [role for role, perms in SHOP_ROLE_PERMISSIONS.items() if 'redeem' in perms]
    if db.query(models.ShopMember.id).filter_by(user_id=user.id, status='active').filter(
            models.ShopMember.role.in_(roles)).first():
        return
    raise HTTPException(403, '沒有核銷權限')


@router.get("/merchant/redemptions/lookup")
def lookup_redemption(phone: str | None = None, code_suffix: str | None = None,
                      order_id: int | None = None,
                      vendor: models.User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    _require_redemption_merchant(db, vendor)
    if not phone and not code_suffix and order_id is None:
        raise HTTPException(400, "請提供 phone、code_suffix 或 order_id")
    suffix = ''.join(c for c in (code_suffix or '') if c.isdigit())
    if code_suffix and (not suffix or len(suffix) > 6):
        raise HTTPException(400, "code_suffix 須為 1–6 位數字")
    digits = ''.join(c for c in (phone or '') if c.isdigit())
    if phone and not digits:
        raise HTTPException(400, "電話號碼不正確")
    query = (db.query(models.WalletItem).join(models.Coupon)
             .filter(models.WalletItem.status == 1))
    if vendor.role != 'admin':
        shop_ids = set(vendor_shop_ids(db, vendor))
        shop_ids.update(row.shop_id for row in db.query(models.ShopMember.shop_id).filter(
            models.ShopMember.user_id == vendor.id, models.ShopMember.status == 'active',
            models.ShopMember.role.in_([role for role, perms in SHOP_ROLE_PERMISSIONS.items()
                                       if 'redeem' in perms])))
        query = query.filter(models.Coupon.shop_id.in_(shop_ids))
    if order_id is not None:
        query = query.filter(models.WalletItem.order_id == order_id)
    if suffix:
        query = query.filter(models.WalletItem.code.like(f'%{suffix}'),
                             models.WalletItem.qr_expires_at > _now())
    out = []
    for item in query.order_by(models.WalletItem.id).all():
        owner, coupon = db.get(models.User, item.user_id), item.coupon
        if phone and (owner is None or ''.join(c for c in (owner.phone or '') if c.isdigit()) != digits):
            continue
        try:
            ensure_paid_voucher(db, item)
            ensure_redeemable(coupon, purchased_order=item.source == 'purchase')
            connect_service.ensure_prior_transfer_closed(db, item.id)
        except HTTPException:
            continue
        shop = db.get(models.Shop, coupon.shop_id)
        order = db.get(models.Order, item.order_id) if item.order_id else None
        allowed = coupon_branch_ids(db, coupon.id)
        branches = (db.query(models.Branch).filter(
            models.Branch.shop_id == coupon.shop_id,
            models.Branch.id.in_(allowed)).order_by(models.Branch.id).all() if allowed else [])
        out.append({'wallet_item_id': item.id, 'coupon_id': coupon.id,
                    'coupon_name': coupon.coupon_name, 'shop_id': coupon.shop_id,
                    'shop_name': shop.name if shop else '',
                    'branch_required': bool(allowed),
                    'branches': [{'id': b.id, 'name': b.name, 'address': b.address or ''} for b in branches],
                    'customer_phone_last4': _phone_last4(owner.phone if owner else None),
                    'valid_to': coupon.valid_to.isoformat() if coupon.valid_to else None,
                    'order_id': item.order_id, 'package_quantity': (order.quantity or 1) if order else None,
                    'source': item.source, 'status': item.status})
    return {'items': out}


def _supplementary_replay(db, key, vendor, wallet_id, branch_id):
    if not key:
        return None
    red = db.query(models.Redemption).filter_by(idempotency_key=key).first()
    if red is None:
        return None
    if (red.scanned_by_vendor_id != vendor.id or red.wallet_item_id != wallet_id
            or red.method != 'supplementary' or red.revoked_at is not None
            or red.branch_id != branch_id):
        raise errors.redemption_replay_mismatch()
    return {'message': '此筆核銷已完成', 'redemption_id': red.id,
            'wallet_item_id': red.wallet_item_id, 'shop_id': red.shop_id,
            'coupon_name': db.get(models.Coupon, red.coupon_id).coupon_name,
            'method': red.method, 'note': red.note, 'duplicate': True}


@router.post("/merchant/redemptions/supplementary-scan")
def supplementary_scan(body: schemas.SupplementaryScanIn,
                       vendor: models.User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    _require_redemption_merchant(db, vendor)
    replay = _supplementary_replay(db, body.idempotency_key, vendor, body.wallet_item_id, body.branch_id)
    if replay:
        return replay
    candidate = db.query(models.WalletItem.user_id, models.WalletItem.order_id).filter_by(id=body.wallet_item_id).first()
    if candidate is None:
        raise HTTPException(404, '找不到此優惠券')
    lock_account(db, candidate.user_id)
    if candidate.order_id is not None:
        from ..order_allocation import ensure_order_allocations
        ensure_order_allocations(db, candidate.order_id)
    item = (db.query(models.WalletItem).filter_by(id=body.wallet_item_id)
            .with_for_update().populate_existing().first())
    if item is None or item.user_id != candidate.user_id or item.order_id != candidate.order_id:
        raise HTTPException(409, '券資料需要核對')
    replay = _supplementary_replay(db, body.idempotency_key, vendor, body.wallet_item_id, body.branch_id)
    if replay:
        return replay
    red = _redeem_item(db, item, vendor, None, idempotency_key=body.idempotency_key,
                       method='supplementary', note=body.note, branch_id=body.branch_id, commit=False)
    audit_log(db, vendor.id, 'redemption.supplementary_scan', 'redemption', red.id,
              f'wallet_item_id={item.id} | reason={(body.reason or "顧客離店後補核銷").strip()}')
    db.commit()
    return {'message': '您的優惠券已成功使用', 'coupon_name': item.coupon.coupon_name,
            'shop_id': red.shop_id, 'redemption_id': red.id, 'wallet_item_id': item.id,
            'revoke_window_minutes': config.REVOKE_WINDOW_MINUTES,
            'scanned_at': red.scanned_at.replace(tzinfo=timezone.utc),
            'duplicate': False, 'method': red.method, 'note': red.note}


def _batch_fingerprint(body):
    fields = {'codes': body.codes, 'note': body.note}
    # Keep the existing fingerprint for old requests and an explicit all-null
    # selection: both mean no branch was supplied for any coupon.
    if body.branch_ids is not None and any(branch_id is not None for branch_id in body.branch_ids):
        fields['branch_ids'] = body.branch_ids
    value = json.dumps(fields, ensure_ascii=False,
                       separators=(',', ':'), sort_keys=True).encode()
    return hmac.new(config.SECRET_KEY.encode(), value, hashlib.sha256).hexdigest()


def _batch_replay(db, batch, fingerprint, codes):
    if not hmac.compare_digest(batch.request_fingerprint, fingerprint):
        raise HTTPException(409, '批次識別碼已用於不同內容')
    try:
        ids = json.loads(batch.redemption_ids)
    except (TypeError, ValueError):
        raise HTTPException(409, '批次記錄不完整，請聯絡客服') from None
    if not isinstance(ids, list) or any(not isinstance(rid, int) for rid in ids):
        raise HTTPException(409, '批次記錄不完整，請聯絡客服')
    reds = [db.get(models.Redemption, rid) for rid in ids]
    if len(ids) != len(codes) or not ids or any(red is None for red in reds):
        raise HTTPException(409, '批次記錄不完整，請聯絡客服')
    if any(red.revoked_at is not None for red in reds):
        raise HTTPException(409, '批次中的核銷已撤銷，請重新出示並建立新批次')
    return {'total': len(reds), 'redeemed_count': 0, 'duplicate_count': len(reds),
            'results': [{'code': code, 'status': 'duplicate', 'wallet_item_id': red.wallet_item_id,
                         'redemption_id': red.id, 'coupon_name': db.get(models.Coupon, red.coupon_id).coupon_name,
                         'duplicate': True} for code, red in zip(codes, reds)]}


@router.post('/new-scan-coupon/batch')
def batch_scan(body: schemas.ScanBatchIn,
               vendor: models.User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    _require_redemption_merchant(db, vendor)
    if body.prepare_tokens is not None and len(body.prepare_tokens) != len(body.codes):
        raise HTTPException(422, 'prepare_tokens 須與 codes 一一對應')
    if body.branch_ids is not None and len(body.branch_ids) != len(body.codes):
        raise HTTPException(422, 'branch_ids 須與 codes 一一對應')
    if len(set(body.codes)) != len(body.codes):
        raise HTTPException(422, '同一批次不能重複掃描相同碼')
    fingerprint = _batch_fingerprint(body)
    prior = db.query(models.RedemptionBatch).filter_by(vendor_id=vendor.id, request_key=body.idempotency_key).first()
    if prior:
        return _batch_replay(db, prior, fingerprint, body.codes)
    batch = models.RedemptionBatch(vendor_id=vendor.id, request_key=body.idempotency_key,
                                   request_fingerprint=fingerprint, redemption_ids='[]')
    db.add(batch)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        prior = db.query(models.RedemptionBatch).filter_by(vendor_id=vendor.id, request_key=body.idempotency_key).first()
        if prior:
            return _batch_replay(db, prior, fingerprint, body.codes)
        raise
    candidates = [presented_item(db, code) for code in body.codes]
    if any(item is None for item in candidates):
        results = [{'code': code, 'status': 'ok' if item else 'error',
                    'wallet_item_id': item.id if item else None,
                    **({} if item else {'reason': '無效的 QR 碼！'})}
                   for code, item in zip(body.codes, candidates)]
        db.rollback()
        return JSONResponse(status_code=400, content={'total': len(results), 'redeemed_count': 0,
                                                      'duplicate_count': 0, 'results': results})
    for user_id in sorted({item.user_id for item in candidates}):
        lock_account(db, user_id)
    from ..order_allocation import ensure_order_allocations
    for order_id in sorted({item.order_id for item in candidates if item.order_id is not None}):
        ensure_order_allocations(db, order_id)
    ids = [item.id for item in candidates]
    locked = (db.query(models.WalletItem).filter(models.WalletItem.id.in_(ids))
              .order_by(models.WalletItem.id).with_for_update().populate_existing().all())
    items = {item.id: item for item in locked}
    if len(items) != len(ids):
        raise HTTPException(409, '券資料需要核對')
    for shop_id in sorted({items[item_id].coupon.shop_id for item_id in ids}):
        lock_shop(db, shop_id)
    branch_ids = body.branch_ids if body.branch_ids is not None else [None] * len(ids)
    branch_errors = []
    for code, item_id, branch_id in zip(body.codes, ids, branch_ids):
        item = items[item_id]
        shop = db.get(models.Shop, item.coupon.shop_id)
        if shop is None or not can_redeem_shop(db, vendor, shop):
            db.rollback()
            raise errors.qr_wrong_shop()
        reason, allowed = check_redemption_branch(db, item.coupon, branch_id)
        options = (db.query(models.Branch).filter(models.Branch.shop_id == shop.id,
                   models.Branch.id.in_(allowed)).order_by(models.Branch.id).all() if allowed else [])
        branch_errors.append({'code': code, 'status': 'error' if reason else 'ok',
                              'wallet_item_id': item.id, 'reason': reason or '',
                              'branch_selection_required': bool(allowed and branch_id is None),
                              'allowed_branches': [{'id': b.id, 'name': b.name, 'address': b.address or ''} for b in options]})
    if any(result['status'] == 'error' for result in branch_errors):
        db.rollback()
        return JSONResponse(status_code=400, content={'total': len(ids), 'redeemed_count': 0,
                                                      'duplicate_count': 0, 'results': branch_errors})
    results = []
    try:
        for index, (code, item_id) in enumerate(zip(body.codes, ids)):
            item = items[item_id]
            prepared_at = None
            if body.prepare_tokens is not None:
                issued = prepare_token.verify_prepare_token(
                    body.prepare_tokens[index], item.id,
                    binding=prepare_token.redemption_binding(vendor.id, code, branch_ids[index]))
                if issued is None:
                    raise HTTPException(400, '預核銷已失效，請重新預核銷')
                prepared_at = datetime.fromtimestamp(issued, tz=timezone.utc).replace(tzinfo=None)
            red = _redeem_item(db, item, vendor, code, idempotency_key=f'batch:{batch.id}:{index}',
                               prepared_at=prepared_at, method='batch', note=body.note,
                               branch_id=branch_ids[index], commit=False)
            results.append({'code': code, 'status': 'redeemed', 'wallet_item_id': item.id,
                            'redemption_id': red.id, 'coupon_name': item.coupon.coupon_name, 'duplicate': False})
        batch.redemption_ids = json.dumps([item['redemption_id'] for item in results])
        audit_log(db, vendor.id, 'redemption.batch_scan', 'redemption_batch', batch.id,
                  f'count={len(results)}')
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    return {'total': len(results), 'redeemed_count': len(results), 'duplicate_count': 0, 'results': results}


@router.get("/vendor/redemptions/export")
def export_redemptions(format: str = Query("json", pattern="^(json|csv)$"),
                       from_: str | None = Query(None, alias="from"),
                       to: str | None = Query(None),
                       coupon_id: int | None = Query(None),
                       revoked: bool | None = Query(None),
                       method: str | None = Query(None, pattern='^(scan|manual|supplementary|batch)$'),
                       vendor: models.User = Depends(get_current_user),
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
    if vendor.role == "admin":
        shop_ids = None
    else:
        shop_ids = vendor_shop_ids(db, vendor)
        for member in db.query(models.ShopMember).filter_by(user_id=vendor.id, status="active"):
            if "view_redemptions" in SHOP_ROLE_PERMISSIONS.get(member.role, set()):
                shop_ids.append(member.shop_id)
        shop_ids = sorted(set(shop_ids))
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
    if method is not None:
        q = q.filter(models.Redemption.method == method)
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
            "scanned_by_name": (db.get(models.User, r.scanned_by_vendor_id).name
                                if db.get(models.User, r.scanned_by_vendor_id) else ''),
            "method": r.method or 'scan', "note": r.note,
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
                   "shop_name", "scanned_at", "method", "scanned_by_name", "revoked", "revoked_at",
                   "idempotency_key")
        rows = [[str(rec[k] if rec[k] is not None else "")
                 for k in columns] for rec in records]
        return csv_response("redemptions.csv", list(columns), rows)
    return {"total": len(records),
            "revoked_count": sum(1 for r in records if r["revoked"]),
            "by_shop": by_shop,
            "records": records}
