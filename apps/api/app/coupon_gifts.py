"""Transfer eligibility and signed, one-time links for issued free coupons."""

import hashlib
import hmac
import re
import secrets

from sqlalchemy.orm import Session

from . import config, models
from .timeutil import utcnow


_TOKEN_FORMAT = re.compile(r"([A-Za-z0-9_-]{32})\.([0-9a-f]{64})")


def _signature(nonce: str) -> str:
    return hmac.new(config.SECRET_KEY.encode(), f"coupon-gift:{nonce}".encode(),
                    hashlib.sha256).hexdigest()


def new_nonce() -> str:
    return secrets.token_urlsafe(24)


def token_for(nonce: str) -> str:
    return f"{nonce}.{_signature(nonce)}"


def token_nonce(token: str) -> str | None:
    parts = _TOKEN_FORMAT.fullmatch(token)
    if parts and hmac.compare_digest(parts[2], _signature(parts[1])):
        return parts[1]
    return None


def link_ready(gift: models.CouponGift, now) -> bool:
    return bool(gift and gift.recipient_wallet_item_id is None and
                gift.cancelled_at is None and gift.expires_at > now)


def can_gift(db: Session, item: models.WalletItem, coupon: models.Coupon,
             shop: models.Shop, now) -> bool:
    if not (item.status == 1 and item.source == "claim" and item.order_id is None
            and coupon and coupon.kind == "coupon" and coupon.package_id is None
            and coupon.sale_price in (None, 0) and not coupon.is_points_exchange
            and coupon.status == "active" and coupon.valid_from <= now < coupon.valid_to
            and shop and shop.is_approved):
        return False
    # A revoked redemption can put the voucher back in the unused state.
    # Keep all past usage and financial history with that customer.
    return db.query(models.Redemption.id).filter_by(wallet_item_id=item.id).first() is None


def wallet_gift_flags(db, item, coupon, shop, gift) -> dict:
    now = utcnow()
    eligible = can_gift(db, item, coupon, shop, now)
    return {
        "gift_eligible": eligible,
        "gift_status": ("sent" if gift and gift.recipient_wallet_item_id is not None
                        else "pending" if eligible and link_ready(gift, now) else None),
    }
