"""Q Wallet, points, records, reviews."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..database import get_db
from ..deps import add_points, get_current_user, points_balance
from ..serializers import wallet_out
from .coupons import _refresh_expiry

router = APIRouter()

POINTS_EXCHANGE_COST = 9


@router.get("/get-favorite-coupons-list")
def get_wallet(page: int = Query(1, ge=1), status: int = Query(None),
               sortby: str = Query("expiry"),
               user: models.User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    items = db.query(models.WalletItem).filter(
        models.WalletItem.user_id == user.id).all()
    for item in items:
        _refresh_expiry(db, item)
    if status in (1, 2, 3):
        items = [i for i in items if i.status == status]
    items.sort(key=lambda i: (
        i.coupon.valid_to if sortby == "expiry" else i.claimed_at, i.id
    ))
    per_page = 20
    total = len(items)
    page_items = items[(page - 1) * per_page: page * per_page]
    return {
        "current_page": page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "total": total,
        "items": [wallet_out(db, i) for i in page_items],
    }


@router.get("/wallet/items/{item_id}/status")
def wallet_item_status(item_id: int, user: models.User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    item = db.query(models.WalletItem).filter_by(id=item_id, user_id=user.id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="找不到此優惠券")
    return {"id": item.id, "status": _refresh_expiry(db, item).status}


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
                    "scanned_at": r.scanned_at})
    return {"records": out}


@router.get("/points", response_model=schemas.PointsOut)
def get_points(user: models.User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    txs = (db.query(models.PointTransaction)
           .filter(models.PointTransaction.user_id == user.id)
           .order_by(models.PointTransaction.id.desc()).limit(50).all())
    return {"balance": points_balance(db, user.id),
            "transactions": [{"id": t.id, "delta": t.delta, "reason": t.reason,
                              "created_at": t.created_at.isoformat()} for t in txs]}


@router.post("/review")
def review(wallet_item_id: int, rating: int = Query(5, ge=1, le=5),
           comment: str = Query(""),
           user: models.User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    item = db.query(models.WalletItem).filter(
        models.WalletItem.id == wallet_item_id,
        models.WalletItem.user_id == user.id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="找不到此優惠券")
    if item.status != 2:
        raise HTTPException(status_code=400, detail="只有已使用的優惠券可以評價")
    if item.reviewed:
        raise HTTPException(status_code=400, detail="已經評價過")
    item.reviewed = True
    add_points(db, user.id, config.POINTS_REVIEW, "review", ref_id=item.id)
    db.commit()
    return {"message": f"評價成功，+{config.POINTS_REVIEW} 分",
            "balance": points_balance(db, user.id)}


@router.post("/points/exchange")
def exchange_points(coupon_id: int, user: models.User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    coupon = db.query(models.Coupon).filter(models.Coupon.id == coupon_id).first()
    if coupon is None or not coupon.is_points_exchange:
        raise HTTPException(status_code=404, detail="找不到此積分券")
    if points_balance(db, user.id) < POINTS_EXCHANGE_COST:
        raise HTTPException(status_code=400, detail="積分不足")
    existing = db.query(models.WalletItem).filter(
        models.WalletItem.user_id == user.id,
        models.WalletItem.coupon_id == coupon_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="已經兌換過此券")
    item = models.WalletItem(user_id=user.id, coupon_id=coupon.id, status=1)
    db.add(item)
    add_points(db, user.id, -POINTS_EXCHANGE_COST, "exchange", ref_id=coupon.id)
    db.commit()
    return {"message": "兌換成功，已存入 Q Wallet",
            "balance": points_balance(db, user.id)}
