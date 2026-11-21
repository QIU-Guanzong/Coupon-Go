"""Coupon claim / QR / scan routes.

Original names: new-favorite-coupon (claim), store-scaned-qr-values
(generate QR), new-scan-coupon (vendor scan). Wallet status: 1 unused,
2 used, 3 expired. Points: claim +10, redeem +20.
"""
import re
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import exists, text

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import cashback_service, config, models, schemas
from ..database import get_db
from ..deps import add_points, get_current_user, require_role
from ..serializers import coupon_out
from ..settlement_ledger import add_scanned_sale
from ..settlement_basis import settlement_basis

router = APIRouter()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
            raise HTTPException(400, "套餐券付款或退款狀態不允許核銷")


def ensure_redeemable(coupon):
    if coupon.status != "active":
        raise HTTPException(400, "此優惠券已停用")
    if coupon.valid_from > _now():
        raise HTTPException(400, "此優惠券尚未到使用日期")


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
        raise HTTPException(status_code=404, detail="找不到此優惠券")
    if coupon.status != "active" or coupon.valid_to <= _now():
        raise HTTPException(status_code=400, detail="此優惠券已失效")
    if coupon.kind == "package":
        # Line C: package vouchers are bought, not claimed for free.
        raise HTTPException(status_code=400, detail="套餐券請購買，不可免費領取")
    if coupon.sale_price is not None and float(coupon.sale_price) > 0:
        # P1-A: paid coupons are bought, not claimed for free.
        raise HTTPException(status_code=400, detail="付費券請購買，不可免費領取")
    if coupon.is_points_exchange:
        raise HTTPException(status_code=400, detail="積分券請用積分兌換")
    existing = db.query(models.WalletItem).filter(
        models.WalletItem.user_id == user.id,
        models.WalletItem.coupon_id == coupon_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="您已領取此 Coupon")
    if coupon.remaining <= 0:
        raise HTTPException(status_code=400, detail="優惠券已達下載上限")
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
            raise HTTPException(status_code=409, detail="您已領取此 Coupon")
        raise HTTPException(status_code=400, detail="此優惠券已不能領取")
    item = models.WalletItem(user_id=user.id, coupon_id=coupon.id, status=1)
    db.add(item)
    add_points(db, user.id, config.POINTS_CLAIM, "claim", ref_id=coupon.id)
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
        raise HTTPException(status_code=404, detail="找不到此優惠券")
    _refresh_expiry(db, item)
    if item.status != 1:
        raise HTTPException(status_code=400, detail="此優惠券不能使用")
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
            raise HTTPException(status_code=400, detail="此優惠券不能使用")
    else:
        raise HTTPException(status_code=503, detail="暫時未能產生兌換碼，請重試")
    payload = f"coupon-go://redeem?code={code}&item={wallet_item_id}"
    return {"wallet_item_id": wallet_item_id, "code": code,
            "qr_payload": payload, "expires_in_seconds": min(config.QR_TTL_SECONDS, max(1, round((expiry - _now()).total_seconds())))}


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
            raise HTTPException(status_code=409, detail="核銷記錄不符，請重新掃碼")
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
        raise HTTPException(status_code=400, detail="無效的 QR 碼！")
    # Line A: a prepare token, when supplied, must be valid for this item.
    prepared_at = None
    if body.prepare_token:
        from ..prepare_token import verify_prepare_token
        issued = verify_prepare_token(body.prepare_token, item.id, binding=f"{vendor.id}:{body.code}")
        if issued is None:
            raise HTTPException(status_code=400,
                                detail="預核銷已失效，請重新預核銷")
        prepared_at = datetime.fromtimestamp(issued, tz=timezone.utc).replace(
            tzinfo=None)
    _refresh_expiry(db, item)
    if item.status == 2:
        raise HTTPException(status_code=400, detail="此優惠券已使用")
    if item.status == 3:
        raise HTTPException(status_code=400, detail="此優惠券已失效")
    if item.qr_expires_at and item.qr_expires_at < _now():
        raise HTTPException(status_code=400, detail="QR 碼已過期，請重新出示")
    coupon = db.query(models.Coupon).filter(models.Coupon.id == item.coupon_id).first()
    if coupon.shop_id not in [s.id for s in
                              db.query(models.Shop).filter(
                                  models.Shop.vendor_id == vendor.id).all()]:
        raise HTTPException(status_code=400, detail="無效的 QR 碼！此碼屬於其他商店。")
    ensure_paid_voucher(db, item)
    ensure_redeemable(coupon)
    today_start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    scans_today = db.query(models.Redemption).filter(
        models.Redemption.scanned_by_vendor_id == vendor.id,
        models.Redemption.scanned_at >= today_start).count()
    if scans_today >= config.SCAN_DAILY_LIMIT:
        raise HTTPException(status_code=400, detail="今日掃碼已達上限")
    cashback_service.lock_account(db, item.user_id)
    consumed = db.query(models.WalletItem).filter(
        models.WalletItem.id == item.id, models.WalletItem.status == 1,
        models.WalletItem.code == body.code, models.WalletItem.qr_expires_at > _now()
    ).update({"status": 2, "used_at": _now(), "code": "", "qr_expires_at": None},
             synchronize_session=False)
    if not consumed:
        replay = prior_result()
        if replay:
            return replay
        raise HTTPException(status_code=400, detail="兌換碼已失效，請重新出示")
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
    red = models.Redemption(
        wallet_item_id=item.id, coupon_id=coupon.id, shop_id=coupon.shop_id,
        scanned_by_vendor_id=vendor.id, face_value=face_value,
        idempotency_key=body.idempotency_key or None,
        prepared_at=prepared_at)
    db.add(red)
    add_points(db, item.user_id, config.POINTS_REDEEM, "redeem", ref_id=item.id)
    # FIX-2: cashback is credited here, server-side, from this redemption
    # only — amount basis and rate come from the coupon row / server
    # config, never from the client. Same transaction as the redemption,
    # so a credit failure rolls the whole scan back (retryable). A fresh
    # redemption can never be revoked or already-credited, so the only
    # expected outcome is a new pending earning, or None when the coupon
    # carries no verifiable spend.
    db.flush()
    try:
        cashback_service.record_spend(db, red)
    except cashback_service.CashbackError as e:
        raise HTTPException(status_code=400, detail=str(e))
    add_scanned_sale(db, red)
    db.commit()
    db.refresh(red)
    return {"message": "您的優惠券已成功使用", "coupon_name": coupon.coupon_name,
            "shop_id": coupon.shop_id, "redemption_id": red.id,
            "revoke_window_minutes": config.REVOKE_WINDOW_MINUTES,
            "scanned_at": red.scanned_at.replace(tzinfo=timezone.utc), "duplicate": False}


@router.get("/get-vendor-usage-data-new")
def usage_data(vendor: models.User = Depends(require_role("vendor", "admin")),
               db: Session = Depends(get_db)):
    shop_ids = [s.id for s in db.query(models.Shop).filter(
        models.Shop.vendor_id == vendor.id).all()]
    coupons = db.query(models.Coupon).filter(
        models.Coupon.shop_id.in_(shop_ids)).all() if shop_ids else []
    return {
        "total_downloads": sum(c.downloads for c in coupons),
        "total_usage": sum(c.used_count for c in coupons),
        "coupons": [{"coupon_id": c.id, "coupon_name": c.coupon_name,
                     "downloads": c.downloads, "used": c.used_count,
                     "remaining": c.remaining} for c in coupons],
    }
