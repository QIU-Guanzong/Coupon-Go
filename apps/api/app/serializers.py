"""Shared model -> dict serializers (keeps routers thin)."""
from sqlalchemy.orm import Session

from . import models
from .geo import haversine_km


def coupon_out(db: Session, c: models.Coupon, user_id=None,
               lat: float | None = None, lng: float | None = None):
    shop = db.query(models.Shop).filter(models.Shop.id == c.shop_id).first()
    vendor = db.query(models.User).filter(models.User.id == c.vendor_id).first()
    fav = False
    if user_id:
        fav = (
            db.query(models.Favorite)
            .filter(models.Favorite.user_id == user_id,
                    models.Favorite.coupon_id == c.id)
            .first()
            is not None
        )
    # Gap 10: straight-line distance from the caller's location to the
    # coupon's shop; None when no location was sent or the shop has no
    # coordinates (the client hides the distance row then).
    distance_km = None
    if lat is not None and lng is not None:
        shop_for_dist = shop
        if shop_for_dist is not None and shop_for_dist.latitude is not None \
                and shop_for_dist.longitude is not None:
            distance_km = round(haversine_km(
                lat, lng, float(shop_for_dist.latitude),
                float(shop_for_dist.longitude)), 2)
    return {
        "id": c.id,
        "coupon_id": c.id,
        "package_id": c.package_id if c.kind == "package" else None,
        "shop_id": c.shop_id,
        "vendor_name": vendor.name if vendor else "",
        "shop_name": shop.name if shop else "",
        "coupon_name": c.coupon_name,
        "name_en": c.name_en,
        "coupon_type": c.coupon_type,
        "cash_amount": float(c.cash_amount) if c.cash_amount is not None else None,
        "percentage": float(c.percentage) if c.percentage is not None else None,
        "gift": c.gift or "",
        "discount_price": float(c.discount_price) if c.discount_price is not None else None,
        "regular_price": float(c.regular_price) if c.regular_price is not None else None,
        "min_spend": float(c.min_spend or 0),
        "image": c.image or "",
        "valid_from": c.valid_from,
        "valid_to": c.valid_to,
        "remaining": c.remaining,
        "total": c.total,
        "downloads": c.downloads,
        "used_count": c.used_count,
        "status": c.status,
        "additional_terms": c.additional_terms or "",
        "is_coupon_favorite": fav,
        "distance_km": distance_km,
    }


def shop_out(db: Session, s: models.Shop, user_id=None):
    vendor = db.query(models.User).filter(models.User.id == s.vendor_id).first()
    cat = db.query(models.Category).filter(models.Category.id == s.category_id).first()
    area = db.query(models.Area).filter(models.Area.id == s.area_id).first()
    branches = db.query(models.Branch).filter(models.Branch.shop_id == s.id).count()
    fav = False
    if user_id:
        fav = (
            db.query(models.Favorite)
            .filter(models.Favorite.user_id == user_id,
                    models.Favorite.shop_id == s.id)
            .first()
            is not None
        )
    return {
        "id": s.id,
        "shop_id": s.id,
        "vendor_name": vendor.name if vendor else "",
        "shop_name": s.name,
        "name_en": s.name_en,
        "category_id": s.category_id,
        "category_name": cat.name if cat else "",
        "area_name": area.name if area else "",
        "address": s.address or "",
        "phone": s.phone or "",
        "description": s.description or "",
        "image": s.image or "",
        "latitude": float(s.latitude) if s.latitude is not None else None,
        "longitude": float(s.longitude) if s.longitude is not None else None,
        "branch_count": branches,
        "is_shop_favorite": fav,
    }


def wallet_out(db: Session, w: models.WalletItem):
    c = db.query(models.Coupon).filter(models.Coupon.id == w.coupon_id).first()
    shop = db.query(models.Shop).filter(models.Shop.id == c.shop_id).first() if c else None
    return {
        "id": w.id,
        "coupon_id": w.coupon_id,
        "coupon_name": c.coupon_name if c else "",
        "shop_name": shop.name if shop else "",
        "coupon_type": c.coupon_type if c else "",
        "status": w.status,
        "code": w.code or "",
        "qr_expires_at": w.qr_expires_at,
        "claimed_at": w.claimed_at,
        "used_at": w.used_at,
        "reviewed": w.reviewed,
        "valid_to": c.valid_to if c else None,
        "discount_price": float(c.discount_price) if c and c.discount_price is not None else None,
        "regular_price": float(c.regular_price) if c and c.regular_price is not None else None,
        # line C: package vouchers ride the same wallet states
        "source": w.source or "claim",
        "order_id": w.order_id,
        "is_package": bool(c and c.kind == "package"),
        "refund_policy": c.refund_policy if c else "anytime",
    }


def amount_text(c: models.Coupon) -> str:
    if c.coupon_type == "Cash" and c.cash_amount is not None:
        return f"減 ${c.cash_amount:g}"
    if c.coupon_type == "Discount" and c.percentage is not None:
        return f"{c.percentage:g} 折"
    if c.coupon_type == "Gift":
        return f"送{c.gift}"
    if c.coupon_type == "Price" and c.discount_price is not None:
        return f"${c.discount_price:g}"
    return c.coupon_name
