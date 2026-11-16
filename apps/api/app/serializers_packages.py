"""Serializers for line C (packages / orders / payments / refunds)."""
import json

from sqlalchemy.orm import Session

from . import models
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


def package_out(db: Session, p: Package) -> dict:
    shop = db.query(models.Shop).filter(models.Shop.id == p.shop_id).first()
    original = float(p.original_price or 0)
    price = float(p.package_price or 0)
    save = max(0.0, original - price)
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
        "usage_rules": _parse_json(p.usage_rules, {}),
        "stock_total": p.stock_total,
        "stock_left": p.stock_left,
        "sold_count": max(0, (p.stock_total or 0) - (p.stock_left or 0)),
        "refund_policy": p.refund_policy,
        "refund_policy_text": REFUND_POLICY_TEXT.get(p.refund_policy, ""),
        "status": p.status,
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
    wallet_item_id = None
    if o.id:
        item = (db.query(models.WalletItem)
                .filter(models.WalletItem.order_id == o.id)
                .first())
        wallet_item_id = item.id if item else None
    payments = (db.query(Payment).filter(Payment.order_id == o.id)
                .order_by(Payment.id).all())
    return {
        "id": o.id,
        "order_id": o.id,
        "user_id": o.user_id,
        "order_type": o.order_type,
        "package_id": o.package_id,
        "package_name": pkg.name if pkg else "",
        "shop_name": shop.name if shop else "",
        "amount": float(o.amount or 0),
        "paid_amount": float(o.paid_amount or 0),
        "payment_method": o.payment_method or "",
        "payment_status": o.payment_status,
        "refund_status": o.refund_status,
        "settlement_status": o.settlement_status,
        "commission_amount": float(o.commission_amount or 0),
        "net_amount": float(o.net_amount or 0),
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
        "gateway_ref": r.gateway_ref or "",
        "expected_arrival": "原路返回，預計 1–3 個工作日到賬",
        "created_at": r.created_at,
    }
