"""Q Wallet, points, records, reviews."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import config, errors, models, points, points_service, schemas
from ..database import get_db
from ..deps import get_current_user, points_balance
from ..models_cashback import WithdrawalEvent
from ..serializers import wallet_out
from ..shared import issue_wallet_item, paginate
from .packages import sweep_expired_packages
from .coupons import _refresh_expiry

router = APIRouter()

# M10: POINTS_EXCHANGE_COST moved to app.config (single source of truth,
# env-configurable); wallet reads config.POINTS_EXCHANGE_COST below.


@router.get("/get-favorite-coupons-list")
def get_wallet(page: int = Query(1, ge=1),
               page_size: int = Query(20, ge=1, le=100),
               status: int = Query(None),
               sortby: str = Query("expiry"),
               user: models.User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    sweep_expired_packages(db, user_id=user.id)
    items = db.query(models.WalletItem).filter(
        models.WalletItem.user_id == user.id).all()
    for item in items:
        _refresh_expiry(db, item)
    if status in (1, 2, 3):
        items = [i for i in items if i.status == status]
    items.sort(key=lambda i: (
        i.coupon.valid_to if sortby == "expiry" else i.claimed_at, i.id
    ))
    out = paginate(items, page, page_size)
    return {**out, "items": [wallet_out(db, i) for i in out["items"]]}


@router.get("/wallet/revision")
def wallet_revision(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    counts = db.query(func.count(models.Redemption.id), func.count(models.Redemption.revoked_at)).join(
        models.WalletItem, models.WalletItem.id == models.Redemption.wallet_item_id
    ).filter(models.WalletItem.user_id == user.id).one()
    changes = db.query(WithdrawalEvent).filter_by(user_id=user.id).count()
    purchases = db.query(models.WalletItem).filter_by(user_id=user.id, source="purchase")
    refunds = db.query(models.Refund).filter_by(user_id=user.id)
    order_counts = (purchases.count(), purchases.filter_by(status=3).count(),
                    refunds.count(), refunds.filter_by(status="requested").count(),
                    refunds.filter_by(status="rejected").count(),
                    refunds.filter_by(status="refunded").count())
    suffix = ":p" + ":".join(map(str, order_counts)) if any(order_counts) else ""
    return {"revision": f"{counts[0]}:{counts[1]}" + (f":{changes}" if changes else "") + suffix}


@router.get("/wallet/items/{item_id}/status")
def wallet_item_status(item_id: int, redemption_id: int | None = None, user: models.User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    item = db.query(models.WalletItem).filter_by(id=item_id, user_id=user.id).first()
    if item is None:
        raise errors.coupon_not_found()
    _refresh_expiry(db, item)
    receipts = db.query(models.Redemption).filter_by(wallet_item_id=item.id)
    receipt = (receipts.filter_by(id=redemption_id).first() if redemption_id is not None
               else receipts.order_by(models.Redemption.id.desc()).first())
    if redemption_id is not None and receipt is None:
        raise errors.redemption_not_found()
    return {"id": item.id, "status": item.status, "reviewed": item.reviewed,
            "has_code": bool(item.code), "redemption_id": receipt.id if receipt else None,
            "revoked": receipt is not None and receipt.revoked_at is not None}


@router.get("/get-user-record-list")
def get_records(user: models.User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    reds = (db.query(models.Redemption)
            .join(models.WalletItem, models.Redemption.wallet_item_id == models.WalletItem.id)
            .filter(models.WalletItem.user_id == user.id)
            .order_by(models.Redemption.scanned_at.desc()).all())
    out = []
    for r in reds:
        c = db.query(models.Coupon).filter(models.Coupon.id == r.coupon_id).first()
        s = db.query(models.Shop).filter(models.Shop.id == r.shop_id).first()
        out.append({"id": r.id, "coupon_name": c.coupon_name if c else "",
                    "shop_name": s.name if s else "",
                    "scanned_at": r.scanned_at, "revoked_at": r.revoked_at,
                    "revoked": r.revoked_at is not None})
    return {"records": out}


@router.get("/points", response_model=schemas.PointsOut)
def get_points(user: models.User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    txs = (db.query(models.PointTransaction)
           .filter(models.PointTransaction.user_id == user.id)
           .order_by(models.PointTransaction.id.desc()).limit(50).all())
    return {"balance": points_balance(db, user.id),
            "expiring_soon": points_service.expiring_soon_points(db, user.id),
            "expiring_soon_days": config.POINTS_EXPIRE_SOON_DAYS,
            "transactions": [{"id": t.id, "delta": t.delta, "reason": t.reason,
                              "created_at": t.created_at.isoformat(),
                              "expires_at": t.expires_at.isoformat() if t.expires_at else None} for t in txs]}


@router.post("/review")
def review(wallet_item_id: int, rating: int = Query(5, ge=1, le=5),
           comment: str = Query(""),
           user: models.User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    item = db.query(models.WalletItem).filter(
        models.WalletItem.id == wallet_item_id,
        models.WalletItem.user_id == user.id).first()
    if item is None:
        raise errors.coupon_not_found()
    if item.status != 2:
        raise errors.review_not_redeemed()
    if item.reviewed:
        raise errors.review_already_done()
    if not db.query(models.WalletItem).filter_by(id=item.id, status=2, reviewed=False).update(
            {"reviewed": True}, synchronize_session=False):
        raise errors.bad_request("優惠券狀態已更新，請返回錢包")
    points.award_review_points(db, user.id, item.id)
    db.commit()
    return {"message": f"評價成功，+{config.POINTS_REVIEW} 分",
            "balance": points_balance(db, user.id)}


@router.post("/points/exchange")
def exchange_points(coupon_id: int, user: models.User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    coupon = db.query(models.Coupon).filter(models.Coupon.id == coupon_id).first()
    if coupon is None or not coupon.is_points_exchange:
        raise errors.points_coupon_not_found()
    points_service.require_spendable_balance(db, user.id)
    if coupon.sale_price is not None and coupon.sale_price > 0:
        raise errors.bad_request("付費券請購買，不可用積分兌換")
    if points_balance(db, user.id) < config.POINTS_EXCHANGE_COST:
        raise errors.points_insufficient()
    existing = db.query(models.WalletItem).filter(
        models.WalletItem.user_id == user.id,
        models.WalletItem.coupon_id == coupon_id).first()
    if existing:
        raise errors.points_coupon_already_exchanged()
    issue_wallet_item(db, user.id, coupon.id, "exchange")
    points.spend_points(db, user.id, config.POINTS_EXCHANGE_COST, "exchange",
                        coupon.id)
    db.commit()
    return {"message": "兌換成功，已存入 Q Wallet",
            "balance": points_balance(db, user.id)}
