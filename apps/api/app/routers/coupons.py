"""Coupon claim / QR / scan routes.

Original names: new-favorite-coupon (claim), store-scaned-qr-values
(generate QR), new-scan-coupon (vendor scan). Wallet status: 1 unused,
2 used, 3 expired. Points: claim +10, redeem +20.
"""
import random
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..database import get_db
from ..deps import add_points, get_current_user, require_role
from ..serializers import coupon_out

router = APIRouter()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _refresh_expiry(db: Session, item: models.WalletItem):
    coupon = db.query(models.Coupon).filter(models.Coupon.id == item.coupon_id).first()
    if item.status == 1 and coupon and coupon.valid_to < _now():
        item.status = 3
        db.commit()
    return item


@router.post("/new-favorite-coupon")
def claim_coupon(coupon_id: int, user: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    coupon = db.query(models.Coupon).filter(models.Coupon.id == coupon_id).first()
    if coupon is None:
        raise HTTPException(status_code=404, detail="找不到此優惠券")
    if coupon.status != "active" or coupon.valid_to < _now():
        raise HTTPException(status_code=400, detail="此優惠券已失效")
    if coupon.is_points_exchange:
        raise HTTPException(status_code=400, detail="積分券請用積分兌換")
    existing = db.query(models.WalletItem).filter(
        models.WalletItem.user_id == user.id,
        models.WalletItem.coupon_id == coupon_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="您已領取此 Coupon")
    if coupon.remaining <= 0:
        raise HTTPException(status_code=400, detail="優惠券已達下載上限")
    item = models.WalletItem(user_id=user.id, coupon_id=coupon.id, status=1)
    db.add(item)
    coupon.remaining -= 1
    coupon.downloads += 1
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
    # Line A anti-screenshot: a fresh code is generated on EVERY present,
    # so a screenshot / forwarded photo of an older code is dead as soon
    # as the owner re-presents. Each code is single-use (cleared on scan).
    item.code = "".join(random.choices("0123456789", k=6))
    item.qr_expires_at = _now() + timedelta(seconds=config.QR_TTL_SECONDS)
    db.commit()
    payload = f"coupon-go://redeem?code={item.code}&item={item.id}"
    return {"wallet_item_id": item.id, "code": item.code,
            "qr_payload": payload, "expires_in_seconds": config.QR_TTL_SECONDS}


@router.post("/new-scan-coupon")
def scan_coupon(body: schemas.ScanIn,
                vendor: models.User = Depends(require_role("vendor", "admin")),
                db: Session = Depends(get_db)):
    # Line A idempotency: replaying the same idempotency_key returns the
    # original result once, never a second redemption / second +20 points.
    if body.idempotency_key:
        prior = db.query(models.Redemption).filter(
            models.Redemption.idempotency_key == body.idempotency_key).first()
        if prior is not None:
            coupon = db.query(models.Coupon).filter(
                models.Coupon.id == prior.coupon_id).first()
            return {"message": "您的優惠券已成功使用", "coupon_name":
                    coupon.coupon_name if coupon else "",
                    "shop_id": prior.shop_id,
                    "redemption_id": prior.id, "duplicate": True}
    item = db.query(models.WalletItem).filter(
        models.WalletItem.code == body.code).first()
    if item is None:
        raise HTTPException(status_code=400, detail="無效的 QR 碼！")
    # Line A: a prepare token, when supplied, must be valid for this item.
    prepared_at = None
    if body.prepare_token:
        from ..prepare_token import verify_prepare_token
        issued = verify_prepare_token(body.prepare_token, item.id)
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
    today_start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    scans_today = db.query(models.Redemption).filter(
        models.Redemption.scanned_by_vendor_id == vendor.id,
        models.Redemption.scanned_at >= today_start).count()
    if scans_today >= config.SCAN_DAILY_LIMIT:
        raise HTTPException(status_code=400, detail="今日掃碼已達上限")
    item.status = 2
    item.used_at = _now()
    # Line A: the presented code dies on consume — a screenshot of it
    # can never be scanned again, even before the status check.
    item.code = ""
    item.qr_expires_at = None
    coupon.used_count += 1
    red = models.Redemption(
        wallet_item_id=item.id, coupon_id=coupon.id, shop_id=coupon.shop_id,
        scanned_by_vendor_id=vendor.id,
        idempotency_key=body.idempotency_key or None,
        prepared_at=prepared_at)
    db.add(red)
    add_points(db, item.user_id, config.POINTS_REDEEM, "redeem", ref_id=item.id)
    db.commit()
    db.refresh(red)
    return {"message": "您的優惠券已成功使用", "coupon_name": coupon.coupon_name,
            "shop_id": coupon.shop_id, "redemption_id": red.id,
            "duplicate": False}


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
