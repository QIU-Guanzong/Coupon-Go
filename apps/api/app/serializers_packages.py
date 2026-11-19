"""Serializers for line C (packages / orders / payments / refunds)."""
import json

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import config, models
from .models_line_b import Review, ShopRating
from .models_line_f import Settlement, SettlementItem, SettlementReversal
from .models_packages import Order, Package, Payment, Refund

REFUND_POLICY_TEXT = {
    "anytime": "未使用隨時退，過期自動退",
    "expiry_only": "僅過期自動退，過期前不設手動退款",
    "non_refundable": "特價券，不設退款",
}


def _parse_json(raw, default):
    try:
        value = json.loads(raw) if raw else default
        return value if value is not None else default
    except (ValueError, TypeError):
        return default


def _branch_out(b) -> dict:
    # Same shape as home.shop_details / get-nearest-branchs.
    return {"id": b.id, "branch_id": b.id, "shop_id": b.shop_id,
            "branch_name": b.name, "address": b.address or "",
            "phone": b.phone or ""}


def _review_out(db: Session, r: Review) -> dict:
    # Same shape as routers.reviews.review_out (kept inline here to
    # avoid importing a router module from a serializer).
    user = db.query(models.User).filter(models.User.id == r.user_id).first()
    return {
        "id": r.id, "user_id": r.user_id,
        "user_name": user.name if user else "",
        "shop_id": r.shop_id, "redemption_id": r.redemption_id,
        "wallet_item_id": r.wallet_item_id,
        "taste": r.taste, "ambience": r.ambience, "service": r.service,
        "overall": r.overall, "content": r.content or "",
        "images": r.images_list, "tags": r.tags_list,
        "points_given": r.points_given,
        "counts_toward_rating": r.counts_toward_rating,
        "status": r.status, "reply_text": r.reply_text or "",
        "replied_at": r.replied_at, "created_at": r.created_at,
    }


def purchase_allowance(db, package, user_id):
    rules = _parse_json(package.usage_rules, {})
    try:
        limit = int(rules["per_user_limit"])
    except (KeyError, TypeError, ValueError):
        return None, package.stock_left
    reserved = 0
    if user_id is not None:
        reserved = db.query(func.coalesce(func.sum(Order.quantity), 0)).filter(
            Order.user_id == user_id, Order.package_id == package.id,
            Order.payment_status.in_(("pending", "paid")), Order.refund_status != "refunded"
        ).scalar()
    return limit, max(0, min(package.stock_left, limit - reserved))


def package_out(db: Session, p: Package, user_id: int | None = None) -> dict:
    shop = db.query(models.Shop).filter(models.Shop.id == p.shop_id).first()
    original = float(p.original_price or 0)
    price = float(p.package_price or 0)
    save = max(0.0, original - price)
    rules = _parse_json(p.usage_rules, {})
    if not isinstance(rules, dict):
        rules = {}
    # Gap 10 — images: the package coupon template's image plus the
    # shop image, de-duplicated, non-empty only. There is no separate
    # package gallery table; no image is invented when both are empty.
    images: list[str] = []
    coupon = (db.query(models.Coupon)
              .filter(models.Coupon.package_id == p.id,
                      models.Coupon.kind == "package")
              .first())
    for src in (coupon.image if coupon else "",
                shop.image if shop else ""):
        if src and src not in images:
            images.append(src)
    # Gap 10 — branches: the shop's real Branch rows.
    branches = (db.query(models.Branch)
                .filter(models.Branch.shop_id == p.shop_id)
                .order_by(models.Branch.id).all())
    # Gap 10 — rating: the ShopRating aggregate (counted, visible
    # reviews only — see routers.reviews.recompute_rating). None while
    # the shop has no rating row / no counted review; never invented.
    rating_row = db.query(ShopRating).filter(
        ShopRating.shop_id == p.shop_id).first()
    rating = None
    if rating_row is not None:
        rating = {"shop_id": rating_row.shop_id,
                  "overall": rating_row.overall,
                  "taste": rating_row.taste,
                  "ambience": rating_row.ambience,
                  "service": rating_row.service,
                  "review_count": rating_row.review_count,
                  "updated_at": rating_row.updated_at}
    # Gap 10 — latest_reviews: newest visible reviews for the shop.
    latest = (db.query(Review)
              .filter(Review.shop_id == p.shop_id,
                      Review.status == "visible")
              .order_by(Review.created_at.desc(), Review.id.desc())
              .limit(5).all())
    per_user_limit, limit_remaining = purchase_allowance(db, p, user_id)
    sold = db.query(func.coalesce(func.sum(Order.quantity), 0)).filter(
        Order.package_id == p.id, Order.payment_status == "paid",
        Order.refund_status != "refunded").scalar()
    time_slots = rules.get("time_slots") or []
    if not isinstance(time_slots, list):
        time_slots = []
    time_slots = [str(t) for t in time_slots]
    stackable = rules.get("stackable")  # None = not stated by the vendor
    return {
        "id": p.id,
        "package_id": p.id,
        "shop_id": p.shop_id,
        "shop_name": shop.name if shop else "",
        "name": p.name,
        "name_en": p.name_en or "",
        "items": _parse_json(p.items, []),
        "person_count": p.person_count,
        "original_price": original,
        "package_price": price,
        "save_amount": round(save, 2),
        "save_percent": round(save / original * 100, 1) if original else 0,
        "valid_from": p.valid_from,
        "valid_to": p.valid_to,
        "usage_rules": rules,
        "stock_total": p.stock_total,
        "stock_left": p.stock_left,
        "sold_count": int(sold),
        "refund_policy": p.refund_policy,
        "refund_policy_text": REFUND_POLICY_TEXT.get(p.refund_policy, ""),
        "status": p.status,
        "images": images,
        "branches": [_branch_out(b) for b in branches],
        "rating": rating,
        "latest_reviews": [_review_out(db, r) for r in latest],
        "limit_remaining": limit_remaining,
        "time_slots": time_slots,
        "stackable": stackable,
        "per_user_limit": per_user_limit,
    }


def payment_out(pay: Payment) -> dict:
    # FIX-1: a simulator-minted pi_test_… id is forgeable and must never
    # reach the client (it is the key to forging a webhook). The id stays
    # in the DB server-side so the webhook can match it. Real Stripe
    # pi_… ids are not simulator-minted and are left as-is.
    ref = pay.gateway_ref or ""
    if ref.startswith("pi_test_"):
        ref = ""
    return {
        "id": pay.id,
        "order_id": pay.order_id,
        "gateway": pay.gateway,
        "gateway_ref": ref,
        "method": pay.method or "",
        "amount": float(pay.amount or 0),
        "currency": pay.currency or "HKD",
        "status": pay.status,
        "failure_message": pay.failure_message or "",
        "created_at": pay.created_at,
    }


def order_out(db: Session, o: Order) -> dict:
    pkg = None
    if o.package_id:
        pkg = db.query(Package).filter(Package.id == o.package_id).first()
    shop = None
    if pkg:
        shop = db.query(models.Shop).filter(models.Shop.id == pkg.shop_id).first()
    wallet_item = None
    if o.id:
        wallet_item = (db.query(models.WalletItem)
                       .filter(models.WalletItem.order_id == o.id)
                       .first())
    wallet_item_id = wallet_item.id if wallet_item else None
    payments = (db.query(Payment).filter(Payment.order_id == o.id)
                .order_by(Payment.id).all())
    # Gap 10 — order_no: there is no stored order-number column; the
    # number is derived deterministically from the order id, so it is
    # stable and unique without inventing a separate sequence.
    order_no = f"CG-{o.id:08d}" if o.id else ""
    # Gap 10 — refund_amount: sum of this order's Refund rows whose
    # gateway refund actually succeeded (status 'refunded'); 0 while
    # no refund exists. Requested/rejected rows contribute nothing.
    refund_amount = float(db.query(
        func.coalesce(func.sum(Refund.amount), 0))
        .filter(Refund.order_id == o.id,
                Refund.status == "refunded").scalar() or 0)
    # Gap 10 — voucher_redeemed: the order's voucher (WalletItem) is
    # used (status 2) — the same fact the redeem path records.
    voucher_redeemed = bool(wallet_item is not None and (
        wallet_item.status == 2 or wallet_item.used_at is not None))
    # Gap 10 — refundable: mirrors the manual-refund gate in
    # routers.packages._refund_order_checked (paid + no refund yet +
    # voucher still unused + policy 'anytime'). expiry_only /
    # non_refundable, used/voided vouchers, and unpaid orders are
    # not manually refundable.
    refundable = False
    if (o.payment_status == "paid" and o.refund_status in ("none", "rejected")
            and wallet_item is not None and wallet_item.status == 1):
        coupon = (db.query(models.Coupon).filter(
            models.Coupon.id == wallet_item.coupon_id).first()
            if wallet_item.coupon_id else None)
        policy = (coupon.refund_policy if coupon else None) or "anytime"
        refundable = policy == "anytime"
    fee, net, settlement_status = o.commission_amount, o.net_amount, o.settlement_status
    if wallet_item and o.settlement_status != "void":
        # A returned voucher may be scanned again in a later month. Only the
        # latest redemption determines the checkout's current statement status.
        line = (db.query(models.Redemption.revoked_at, SettlementItem, SettlementReversal, Settlement.status)
                .select_from(models.Redemption)
                .outerjoin(SettlementItem, SettlementItem.redemption_id == models.Redemption.id)
                .outerjoin(Settlement, Settlement.id == SettlementItem.settlement_id)
                .outerjoin(SettlementReversal, SettlementReversal.item_id == SettlementItem.id)
                .filter(models.Redemption.wallet_item_id == wallet_item.id)
                .order_by(models.Redemption.id.desc()).first())
        if line:
            revoked_at, sale, reversal, status = line
            if sale is not None:
                settlement_status = status
                fee = sale.commission + (reversal.commission if reversal else 0)
                net = sale.amount + (reversal.amount if reversal else 0) - fee
            if reversal or (revoked_at and sale is None):
                settlement_status = "reversed"
                if sale is None:
                    fee = net = 0
    return {
        "id": o.id,
        "order_id": o.id,
        "order_no": order_no,
        "demo_payment": bool(config.APP_ENV == "dev" and not config.STRIPE_SECRET_KEY and payments
                             and (payments[-1].gateway_ref or "").startswith("pi_test_")),
        "user_id": o.user_id,
        "order_type": o.order_type,
        "package_id": o.package_id,
        "quantity": o.quantity or 1,
        "package_name": pkg.name if pkg else "",
        "shop_name": shop.name if shop else "",
        "amount": float(o.amount or 0),
        "paid_amount": float(o.paid_amount or 0),
        "payment_method": o.payment_method or "",
        "payment_status": o.payment_status,
        "refund_status": o.refund_status,
        "refund_amount": refund_amount,
        "refundable": refundable,
        "voucher_redeemed": voucher_redeemed,
        "settlement_status": settlement_status,
        "commission_amount": float(fee or 0),
        "net_amount": float(net or 0),
        "wallet_item_id": wallet_item_id,
        "created_at": o.created_at,
        "paid_at": o.paid_at,
        "payments": [payment_out(p) for p in payments],
    }


def refund_out(r: Refund) -> dict:
    return {
        "id": r.id,
        "refund_id": r.id,
        "order_id": r.order_id,
        "amount": float(r.amount or 0),
        "status": r.status,
        "gateway_ref": "" if (r.gateway_ref or "").startswith("request-") else r.gateway_ref or "",
        "expected_arrival": "原路返回，預計 1–3 個工作日到賬" if r.status == "refunded" else "",
        "created_at": r.created_at,
    }
