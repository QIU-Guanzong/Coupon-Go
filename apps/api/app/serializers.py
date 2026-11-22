"""Shared model -> dict serializers (keeps routers thin)."""
import json

from sqlalchemy.orm import Session

from . import models
from .geo import haversine_km


def _json_value(text, default):
    """Parse a JSON text column; return default on empty/bad input."""
    if not text:
        return default
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return default
    return parsed


def _json_list(text):
    parsed = _json_value(text, [])
    return parsed if isinstance(parsed, list) else []


def coupon_branch_ids(db: Session, coupon_id: int) -> list:
    """Branch ids a coupon is restricted to (dianping §1.2).

    Empty = usable at every branch of the coupon's shop."""
    rows = (db.query(models.CouponBranch)
            .filter(models.CouponBranch.coupon_id == coupon_id)
            .order_by(models.CouponBranch.branch_id).all())
    return [r.branch_id for r in rows]


def coupon_branches_out(db: Session, coupon_id: int) -> list:
    ids = coupon_branch_ids(db, coupon_id)
    if not ids:
        return []
    branches = (db.query(models.Branch)
                .filter(models.Branch.id.in_(ids))
                .order_by(models.Branch.id).all())
    return [{"id": b.id, "branch_id": b.id, "name": b.name} for b in branches]


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
        # §3.1 calibre (P1-A compatible): sale_price > 0 = paid coupon.
        "sale_price": float(c.sale_price) if getattr(c, "sale_price", None) is not None else None,
        "is_paid": bool(getattr(c, "sale_price", None) or 0) > 0,
        "refund_policy": c.refund_policy or "anytime",
        # §3.4: max claims per user; None (legacy row) = 1.
        "per_user_limit": c.per_user_limit if getattr(c, "per_user_limit", None) else 1,
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
        # Dianping §1.2: branches this coupon is valid at; [] = all
        # branches of the shop.
        "branch_ids": coupon_branch_ids(db, c.id),
        "branches": coupon_branches_out(db, c.id),
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
        "is_approved": bool(s.is_approved),
        # Dianping §1.4: merchant-maintained shop profile fields.
        "business_hours": _json_value(s.business_hours, {}),
        "gallery": _json_list(s.gallery),
        "announcement": s.announcement or "",
    }


def wallet_out(db: Session, w: models.WalletItem):
    c = db.query(models.Coupon).filter(models.Coupon.id == w.coupon_id).first()
    shop = db.query(models.Shop).filter(models.Shop.id == c.shop_id).first() if c else None
    order = db.get(models.Order, w.order_id) if w.order_id else None
    items = (db.query(models.WalletItem).filter_by(order_id=order.id).all() if order else [])
    if order:
        from .serializers_packages import order_wallet_refundable
        from .order_allocation import wallet_order_consistent
        order_refundable = order_wallet_refundable(db, order, items)
        order_wallet_status = "ok" if wallet_order_consistent(order, items) else "needs_review"
    else:
        order_refundable = False
        order_wallet_status = "ok"
    return {
        "quantity": (order.quantity or 1) if order and len(items) == 1 else 1,
        "order_quantity": (order.quantity or 1) if order else None,
        "order_paid_amount": (float(order.paid_amount) if order and order.payment_status == "paid" and order.paid_amount is not None else None),
        "order_refundable": order_refundable,
        "order_wallet_quantity_status": order_wallet_status,
        "refund_status": order.refund_status if order else "none",
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
        # P1-A: the price paid for this voucher (None for free claims).
        "sale_price": float(c.sale_price) if c and getattr(c, "sale_price", None) is not None else None,
        "is_paid": bool(c and (getattr(c, "sale_price", None) or 0)) > 0,
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
