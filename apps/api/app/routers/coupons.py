"""Coupon claim / QR / scan routes.

Original names: new-favorite-coupon (claim), store-scaned-qr-values
(generate QR), new-scan-coupon (vendor scan). Wallet status: 1 unused,
2 used, 3 expired. Points: claim +10, redeem +20.
"""
import math
import re
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import exists, text

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import cashback_service, config, connect_service, errors, models, points, schemas
from ..database import get_db
from ..deps import get_current_user, require_role
from ..serializers import coupon_out
from ..settlement_ledger import add_scanned_sale, lock_shop
from ..settlement_basis import settlement_basis
from ..shared import issue_wallet_item, vendor_shop_ids

router = APIRouter()


from ..timeutil import utcnow as _now


def _refresh_expiry(db: Session, item: models.WalletItem):
    if item.status == 1 and item.coupon.valid_to <= _now():
        # Do not overwrite a redemption committed while this wallet was loading.
        db.query(models.WalletItem).filter(
            models.WalletItem.id == item.id, models.WalletItem.status == 1
        ).update({"status": 3, "code": "", "qr_expires_at": None}, synchronize_session=False)
        db.commit()
        db.refresh(item)
    return item


def ensure_paid_voucher(db, item):
    if item.source == "purchase":
        order = db.get(models.Order, item.order_id) if item.order_id else None
        if not order or order.payment_status != "paid" or order.refund_status not in ("none", "rejected"):
            raise errors.voucher_unpaid()


def ensure_redeemable(coupon):
    if coupon.status != "active":
        raise errors.coupon_inactive()
    if coupon.valid_from > _now():
        raise errors.coupon_not_started()


def presented_item(db, code, *, lock=False):
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
        return None
    query = db.query(models.WalletItem).filter(
        models.WalletItem.code == code, models.WalletItem.status == 1,
        models.WalletItem.qr_expires_at.is_not(None))
    if lock:
        query = query.with_for_update()
    # Older releases could issue the same number to two items.
    live = query.filter(models.WalletItem.qr_expires_at > _now()).limit(2).all()
    if live:
        return live[0] if len(live) == 1 else None
    return query.first()


@router.post("/new-favorite-coupon")
def claim_coupon(coupon_id: int, user: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    coupon = db.query(models.Coupon).filter(models.Coupon.id == coupon_id).first()
    if coupon is None:
        raise errors.coupon_not_found()
    if coupon.status != "active" or coupon.valid_to <= _now():
        raise errors.coupon_expired()
    if coupon.kind == "package":
        # Line C: package vouchers are bought, not claimed for free.
        raise errors.coupon_package_must_purchase()
    if coupon.is_points_exchange:
        raise errors.coupon_points_only()
    existing = db.query(models.WalletItem).filter(
        models.WalletItem.user_id == user.id,
        models.WalletItem.coupon_id == coupon_id).first()
    if existing:
        raise errors.coupon_already_claimed()
    if coupon.remaining <= 0:
        raise errors.coupon_sold_out()
    # Serialize competing claims on the stock row before checking ownership
    # again. This write also serializes writers on SQLite.
    available = db.query(models.Coupon).filter(
        models.Coupon.id == coupon_id, models.Coupon.remaining > 0,
        models.Coupon.status == "active", models.Coupon.valid_to > _now(),
        ~exists().where((models.WalletItem.user_id == user.id) &
                        (models.WalletItem.coupon_id == coupon_id))).update({
        models.Coupon.remaining: models.Coupon.remaining - 1,
        models.Coupon.downloads: models.Coupon.downloads + 1,
    }, synchronize_session=False)
    if not available or db.query(models.WalletItem.id).filter_by(
            user_id=user.id, coupon_id=coupon_id).first():
        db.rollback()
        if db.query(models.WalletItem.id).filter_by(user_id=user.id, coupon_id=coupon_id).first():
            raise errors.coupon_already_claimed()
        raise errors.coupon_unavailable()
    item = issue_wallet_item(db, user.id, coupon.id, "claim")
    points.award_claim_points(db, user.id, coupon.id)
    db.commit()
    db.refresh(item)
    return {"message": "已存入 Q Wallet", "wallet_item_id": item.id,
            "coupon": coupon_out(db, coupon, user.id)}


@router.post("/store-scaned-qr-values", response_model=schemas.QrOut)
def generate_qr(wallet_item_id: int, user: models.User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    item = db.query(models.WalletItem).filter(
        models.WalletItem.id == wallet_item_id,
        models.WalletItem.user_id == user.id).first()
    if item is None:
        raise errors.coupon_not_found()
    _refresh_expiry(db, item)
    if item.status != 1:
        raise errors.coupon_not_usable()
    ensure_paid_voucher(db, item)
    ensure_redeemable(item.coupon)
    # Line A anti-screenshot: a fresh code is generated on EVERY present,
    # so a screenshot / forwarded photo of an older code is dead as soon
    # as the owner re-presents. Each code is single-use (cleared on scan).
    for _ in range(12):
        code = f"{secrets.randbelow(1_000_000):06d}"
        if code == item.code:
            continue
        if db.bind.dialect.name == "postgresql":
            # Serialize only issuers of this candidate. The following statement
            # sees the winner's commit before deciding whether it is available.
            db.execute(text("SELECT pg_advisory_xact_lock(18352, :code)"), {"code": int(code)})
        expiry = min(_now() + timedelta(seconds=config.QR_TTL_SECONDS), item.coupon.valid_to)
        other = models.WalletItem.__table__.alias("other_wallet")
        written = db.query(models.WalletItem).filter(
            models.WalletItem.id == item.id, models.WalletItem.status == 1,
            ~exists().where((other.c.code == code) & (other.c.status == 1) &
                            (other.c.qr_expires_at > _now()))
        ).update({"code": code, "qr_expires_at": expiry}, synchronize_session=False)
        if written:
            db.commit()
            break
        db.rollback()
        if item.status != 1:
            raise errors.coupon_not_usable()
    else:
        raise errors.qr_generate_failed()
    payload = f"coupon-go://redeem?code={code}&item={wallet_item_id}"
    return {"wallet_item_id": wallet_item_id, "code": code,
            "qr_payload": payload, "expires_in_seconds": min(config.QR_TTL_SECONDS, max(1, round((expiry - _now()).total_seconds())))}


def _redeem_item(db: Session, item: models.WalletItem,
                 vendor: models.User, code: str, *,
                 idempotency_key=None, prepared_at=None) -> models.Redemption:
    """The single consume chain (dianping unify).

    QR scan, manual code entry, supplementary scan and batch scan all
    consume through here — never a second copy of the consume. In
    order: lazy expiry flip -> status / QR-expiry / shop / paid-voucher
    / redeemable checks -> daily scan cap -> customer-account lock ->
    conditional UPDATE consume (atomic, anti-double-scan) ->
    ``Coupon.used_count`` -> ``settlement_basis()`` snapshot ->
    ``Redemption`` row -> redeem points (``points.award_redeem_points``,
    the P1-C seam) -> cashback ``record_spend()`` -> settlement ledger
    ``add_scanned_sale()``. Commits and returns the redemption.

    The caller resolves the item (``presented_item``) and verifies the
    prepare token; this function owns every check after that. The
    ``method`` / ``note`` columns (dianping redemption block) are not
    in main yet — when its migration lands, this function takes them
    and the batch / supplementary callers pass them here.
    """
    _refresh_expiry(db, item)
    if item.status == 2:
        raise errors.voucher_used()
    if item.status == 3:
        raise errors.voucher_expired()
    if item.qr_expires_at and item.qr_expires_at < _now():
        raise errors.qr_expired()
    coupon = db.query(models.Coupon).filter(models.Coupon.id == item.coupon_id).first()
    if coupon.shop_id not in vendor_shop_ids(db, vendor):
        raise errors.qr_wrong_shop()
    ensure_paid_voucher(db, item)
    ensure_redeemable(coupon)
    connect_service.ensure_prior_transfer_closed(db, item.id)
    today_start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    scans_today = db.query(models.Redemption).filter(
        models.Redemption.scanned_by_vendor_id == vendor.id,
        models.Redemption.scanned_at >= today_start).count()
    if scans_today >= config.SCAN_DAILY_LIMIT:
        raise errors.scan_daily_limit()
    cashback_service.lock_account(db, item.user_id)
    consumed = db.query(models.WalletItem).filter(
        models.WalletItem.id == item.id, models.WalletItem.status == 1,
        models.WalletItem.code == code, models.WalletItem.qr_expires_at > _now()
    ).update({"status": 2, "used_at": _now(), "code": "", "qr_expires_at": None},
             synchronize_session=False)
    if not consumed:
        raise errors.code_expired()
    # Line A: the presented code dies on consume — a screenshot of it
    # can never be scanned again, even before the status check.
    coupon.used_count = models.Coupon.used_count + 1
    # M6: snapshot the settlement basis at scan time via the one
    # shared function (see app.settlement_basis) — package = order
    # paid_amount, price coupon = discount_price. Cash / free / gift /
    # percentage-only / pure-threshold coupons contribute 0.
    # Settlement and reconciliation prefer the snapshot, except for
    # refunded orders and old cash-coupon snapshots. None means the order was already
    # refunded: snapshot 0, the redemption never settles anyway.
    _basis = settlement_basis(db, coupon, wallet_item=item)
    face_value = _basis if _basis is not None else Decimal("0")
    # The shop lock also serializes commission updates with the snapshot.
    lock_shop(db, coupon.shop_id)
    commission_rate_snapshot = connect_service.effective_commission_rate(db, coupon.shop_id)
    red = models.Redemption(
        wallet_item_id=item.id, coupon_id=coupon.id, shop_id=coupon.shop_id,
        scanned_by_vendor_id=vendor.id, face_value=face_value,
        idempotency_key=idempotency_key or None,
        commission_rate_snapshot=commission_rate_snapshot,
        prepared_at=prepared_at)
    db.add(red)
    points.award_redeem_points(db, item.user_id, item.id)
    # FIX-2: cashback is credited here, server-side, from this redemption
    # only — amount basis and rate come from the coupon row / server
    # config, never from the client. Same transaction as the redemption,
    # so a credit failure rolls the whole scan back (retryable). A fresh
    # redemption can never be revoked or already-credited, so the only
    # expected outcome is a new pending earning, or None when the coupon
    # carries no verifiable spend.
    db.flush()
    # M10: cashback master switch (default OFF). When off, skip the whole
    # record_spend call — including its dine-in unlock of earlier pending
    # earnings for this merchant; they unlock again once the switch
    # re-opens. Revocation still reverses old earnings (see
    # void_for_revocation, ungated by design).
    if config.CASHBACK_ENABLED:
        try:
            cashback_service.record_spend(db, red)
        except cashback_service.CashbackError as e:
            raise HTTPException(status_code=400, detail=str(e))
    add_scanned_sale(db, red)
    # P1-B: post-redemption transfer to the merchant's Connect account
    # (commission deducted, net transferred). A prior unresolved transfer
    # is rejected before consuming the wallet item; provider work is queued.
    connect_block = connect_service.record_redemption(db, red)
    db.commit()
    db.refresh(red)
    red._connect_result = connect_block
    return red


def _scan_response(red: models.Redemption, coupon, duplicate: bool) -> dict:
    return {"message": "您的優惠券已成功使用", "coupon_name": coupon.coupon_name,
            "shop_id": coupon.shop_id, "redemption_id": red.id,
            "revoke_window_minutes": config.REVOKE_WINDOW_MINUTES,
            "scanned_at": red.scanned_at.replace(tzinfo=timezone.utc),
            "duplicate": duplicate,
            "connect": getattr(red, "_connect_result", None)}


@router.post("/new-scan-coupon")
def scan_coupon(body: schemas.ScanIn,
                vendor: models.User = Depends(require_role("vendor", "admin")),
                db: Session = Depends(get_db)):
    # Line A idempotency: replaying the same idempotency_key returns the
    # original result once, never a second redemption / second +20 points.
    def prior_result():
        if not body.idempotency_key:
            return None
        prior = db.query(models.Redemption).filter_by(idempotency_key=body.idempotency_key).first()
        if prior is None:
            return None
        if prior.scanned_by_vendor_id != vendor.id or prior.revoked_at is not None:
            raise errors.redemption_replay_mismatch()
        coupon = db.get(models.Coupon, prior.coupon_id)
        return {"message": "您的優惠券已成功使用", "coupon_name": coupon.coupon_name,
                "shop_id": prior.shop_id, "redemption_id": prior.id,
                "revoke_window_minutes": config.REVOKE_WINDOW_MINUTES,
                "scanned_at": prior.scanned_at.replace(tzinfo=timezone.utc), "duplicate": True}

    replay = prior_result()
    if replay:
        return replay
    item = presented_item(db, body.code, lock=True)
    if item is None:
        replay = prior_result()
        if replay:
            return replay
    if item is None:
        raise errors.qr_invalid()
    # Line A: a prepare token, when supplied, must be valid for this item.
    prepared_at = None
    if body.prepare_token:
        from ..prepare_token import verify_prepare_token
        issued = verify_prepare_token(body.prepare_token, item.id, binding=f"{vendor.id}:{body.code}")
        if issued is None:
            raise errors.prepare_expired()
        prepared_at = datetime.fromtimestamp(issued, tz=timezone.utc).replace(
            tzinfo=None)
    try:
        red = _redeem_item(db, item, vendor, body.code,
                           idempotency_key=body.idempotency_key,
                           prepared_at=prepared_at)
    except errors.ApiError as exc:
        # A lost consume race against a replayed idempotency key: the
        # other request already committed this redemption — return it
        # instead of reporting the code dead.
        if exc.code == "code_expired":
            replay = prior_result()
            if replay:
                return replay
        raise
    coupon = db.get(models.Coupon, red.coupon_id)
    return _scan_response(red, coupon, duplicate=False)


def coupon_usage_stats(db: Session, shop_ids) -> list:
    """Per-coupon claim/use/remaining rows — the one builder for the
    merchant usage stats (``usage_data`` below; the stats-dashboard
    block reuses these counts for its per-coupon conversion rows
    instead of recounting them). Amounts stay out: settlement money
    always comes from ``settlement_basis()``, never from these rows.
    """
    coupons = db.query(models.Coupon).filter(
        models.Coupon.shop_id.in_(list(shop_ids))).all() if shop_ids else []
    return [{"coupon_id": c.id, "coupon_name": c.coupon_name,
             "downloads": c.downloads, "used": c.used_count,
             "remaining": c.remaining} for c in coupons]


@router.get("/get-vendor-usage-data-new")
def usage_data(vendor: models.User = Depends(require_role("vendor", "admin")),
               db: Session = Depends(get_db)):
    shop_ids = vendor_shop_ids(db, vendor)
    coupons = db.query(models.Coupon).filter(
        models.Coupon.shop_id.in_(shop_ids)).all() if shop_ids else []
    return {
        "total_downloads": sum(c.downloads for c in coupons),
        "total_usage": sum(c.used_count for c in coupons),
        "coupons": coupon_usage_stats(db, shop_ids),
    }


# ---------- coupon expiry: reminder + auto-expire (GAP §5.6) ----------
COUPON_EXPIRY_REMINDER_DAYS = 3
COUPON_EXPIRY_REMINDER_TYPE = "coupon_expiring"


def sweep_coupon_expiry(db: Session) -> dict:
    """One pass of the coupon-expiry job (GAP §5.6, item ①).

    - Reminder: every unredeemed wallet item whose coupon expires
      within ``COUPON_EXPIRY_REMINDER_DAYS`` gets one in-app
      notification (type ``coupon_expiring``). Dedup: a reminder for
      the same (user, coupon) inside the last 24h suppresses a repeat,
      so a daily run notifies once, not every cycle.
    - Auto-expire: unredeemed items whose coupon already passed
      ``valid_to`` flip to status 3 with the same conditional update
      ``_refresh_expiry`` uses (status == 1 only), so a redemption
      committed while the sweep runs is never overwritten.
    - Points expiry (§5.1②) is NOT here: it needs the
      ``PointTransaction.expires_at`` column P1-C owns.

    Idempotent, per-item commit (same pattern as the M1 sweeps):
    one bad item never blocks the rest. The scheduler and the admin
    manual endpoint both call this one function.
    """
    from ..notify import notify

    now = _now()
    stats = {"reminded": 0, "expired": 0, "failed": 0}
    remind_before = now + timedelta(days=COUPON_EXPIRY_REMINDER_DAYS)
    day_ago = now - timedelta(days=1)
    items = (db.query(models.WalletItem)
             .join(models.Coupon,
                   models.Coupon.id == models.WalletItem.coupon_id)
             .filter(models.WalletItem.status == 1,
                     models.Coupon.valid_to > now,
                     models.Coupon.valid_to <= remind_before)
             .order_by(models.WalletItem.id).all())
    for item in items:
        try:
            coupon = db.get(models.Coupon, item.coupon_id)
            recent = db.query(models.Notification).filter(
                models.Notification.user_id == item.user_id,
                models.Notification.notification_type
                == COUPON_EXPIRY_REMINDER_TYPE,
                models.Notification.target_coupon_id == coupon.id,
                models.Notification.created_at >= day_ago).first()
            if recent is None:
                days_left = max(
                    1, math.ceil((coupon.valid_to - now).total_seconds()
                                 / 86400))
                notify(db, item.user_id,
                       "您領取的優惠券即將到期",
                       f"您領取的「{coupon.coupon_name}」將於 "
                       f"{days_left} 天後到期，請盡快使用。",
                       notification_type=COUPON_EXPIRY_REMINDER_TYPE,
                       target_coupon_id=coupon.id,
                       target_shop_id=coupon.shop_id)
                stats["reminded"] += 1
            db.commit()
        except Exception:
            db.rollback()
            stats["failed"] += 1
    expired_items = (db.query(models.WalletItem)
                     .join(models.Coupon,
                           models.Coupon.id == models.WalletItem.coupon_id)
                     .filter(models.WalletItem.status == 1,
                             models.Coupon.valid_to <= now)
                     .order_by(models.WalletItem.id).all())
    for item in expired_items:
        try:
            marked = db.query(models.WalletItem).filter(
                models.WalletItem.id == item.id,
                models.WalletItem.status == 1
            ).update({"status": 3, "code": "", "qr_expires_at": None},
                     synchronize_session=False)
            if marked:
                stats["expired"] += 1
            db.commit()
        except Exception:
            db.rollback()
            stats["failed"] += 1
    return stats


@router.post("/admin/coupon-expiry-sweep")
def coupon_expiry_sweep(admin: models.User = Depends(require_role("admin")),
                        db: Session = Depends(get_db)):
    """M1 pattern: the admin manual backfill for the coupon-expiry
    scheduler job — same sweep function, explicit trigger."""
    result = sweep_coupon_expiry(db)
    return {"message": "已處理即將到期與已過期優惠券", **result}
