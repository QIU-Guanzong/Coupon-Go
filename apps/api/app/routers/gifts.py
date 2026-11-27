"""Q Wallet gift links and atomic coupon hand-off."""

from datetime import timedelta, timezone

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, SecretStr
from sqlalchemy.orm import Session

from .. import coupon_gifts, errors, models
from ..database import get_db
from ..deps import require_role
from ..shared import issue_wallet_item
from ..timeutil import utcnow


router = APIRouter(prefix="/wallet")
customer = require_role("customer")
link_lifetime = timedelta(hours=24)


class GiftLinkIn(BaseModel):
    token: SecretStr


def _wallet_item(db, item_id, *, user_id=None, lock=False):
    query = db.query(models.WalletItem).filter_by(id=item_id)
    if user_id is not None:
        query = query.filter_by(user_id=user_id)
    # Customer write auth already acquires SQLite's writer lock.
    if lock:
        query = query.with_for_update(key_share=True)
    return query.populate_existing().first()


def _unavailable():
    return errors.ApiError(404, "gift_unavailable", "轉贈連結無效、已失效或已被領取")


def _gift_and_item(db, token, *, lock=False):
    nonce = coupon_gifts.token_nonce(token.get_secret_value())
    gift = db.query(models.CouponGift).filter_by(nonce=nonce).first() if nonce else None
    if gift is None:
        raise _unavailable()
    item = _wallet_item(db, gift.sender_wallet_item_id, lock=lock)
    if lock:
        db.refresh(gift)
    # A sender may have replaced an expired/cancelled link while we waited.
    if item is None or gift.nonce != nonce:
        raise _unavailable()
    return gift, item


def _recipient_item_id(db, gift, user_id):
    if gift.recipient_wallet_item_id is None:
        return None
    return db.query(models.WalletItem.id).filter_by(
        id=gift.recipient_wallet_item_id, user_id=user_id).scalar()


def _coupon_and_shop(db, item):
    coupon = db.get(models.Coupon, item.coupon_id)
    shop = db.get(models.Shop, coupon.shop_id) if coupon else None
    return coupon, shop


def _available_gift(db, gift, item, user_id, now):
    if item.user_id == user_id:
        raise errors.ApiError(409, "gift_self", "不能領取自己轉贈的優惠券")
    coupon, shop = _coupon_and_shop(db, item)
    if not (coupon_gifts.link_ready(gift, now)
            and coupon_gifts.can_gift(db, item, coupon, shop, now)):
        raise _unavailable()
    return coupon, shop


@router.post("/items/{item_id}/gift")
def create_gift(item_id: int, response: Response,
                user: models.User = Depends(customer), db: Session = Depends(get_db)):
    item = _wallet_item(db, item_id, user_id=user.id, lock=True)
    if item is None:
        raise errors.coupon_not_found()
    now = utcnow()
    coupon, shop = _coupon_and_shop(db, item)
    if not coupon_gifts.can_gift(db, item, coupon, shop, now):
        raise errors.ApiError(409, "gift_not_eligible", "這張優惠券不能轉贈")
    gift = db.get(models.CouponGift, item.id)
    if gift is None:
        gift = models.CouponGift(sender_wallet_item_id=item.id)
        db.add(gift)
    if gift.expires_at is None or not coupon_gifts.link_ready(gift, now):
        gift.nonce = coupon_gifts.new_nonce()
        gift.expires_at = min(now + link_lifetime, coupon.valid_to)
        gift.cancelled_at = None
    result = {"token": coupon_gifts.token_for(gift.nonce),
              "expires_at": min(gift.expires_at, coupon.valid_to).replace(tzinfo=timezone.utc)}
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.delete("/items/{item_id}/gift", status_code=204)
def cancel_gift(item_id: int, user: models.User = Depends(customer),
                db: Session = Depends(get_db)):
    item = _wallet_item(db, item_id, user_id=user.id, lock=True)
    if item is None:
        raise errors.coupon_not_found()
    gift = db.get(models.CouponGift, item.id)
    if gift and gift.recipient_wallet_item_id is not None:
        raise errors.ApiError(409, "gift_already_claimed", "好友已領取，無法取消轉贈")
    if gift and gift.cancelled_at is None:
        gift.cancelled_at = utcnow()
    db.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/gifts/preview")
def preview_gift(body: GiftLinkIn, response: Response,
                 user: models.User = Depends(customer), db: Session = Depends(get_db)):
    gift, item = _gift_and_item(db, body.token)
    received_id = _recipient_item_id(db, gift, user.id)
    if received_id is not None:
        coupon, shop = _coupon_and_shop(db, item)
    else:
        coupon, shop = _available_gift(db, gift, item, user.id, utcnow())
    response.headers["Cache-Control"] = "no-store"
    return {"coupon_name": coupon.coupon_name, "shop_name": shop.name,
            "expires_at": min(gift.expires_at, coupon.valid_to).replace(tzinfo=timezone.utc),
            **({"wallet_item_id": received_id} if received_id is not None else {})}


@router.post("/gifts/accept")
def accept_gift(body: GiftLinkIn, response: Response,
                user: models.User = Depends(customer), db: Session = Depends(get_db)):
    # Auth locks the recipient account before checking its claim limit.
    # Both the sender's redemption and all gift actions lock the sender's
    # wallet item before updating it. The earlier action wins.
    gift, item = _gift_and_item(db, body.token, lock=True)
    received_id = _recipient_item_id(db, gift, user.id)
    if received_id is None:
        coupon, _ = _available_gift(db, gift, item, user.id, utcnow())
        received_count = db.query(models.WalletItem).filter_by(
            user_id=user.id, coupon_id=item.coupon_id).count()
        if received_count >= (coupon.per_user_limit or 1):
            raise errors.coupon_already_claimed()
        item.status = 3
        item.code = ""
        item.qr_expires_at = None
        received = issue_wallet_item(db, user.id, item.coupon_id, "gift")
        # The voucher keeps its original acquisition cohort in merchant reports.
        received.claimed_at = item.claimed_at
        received_id = gift.recipient_wallet_item_id = received.id
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"wallet_item_id": received_id}
