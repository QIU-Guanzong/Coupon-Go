"""Freeze a homogeneous paid order's full voucher set and per-voucher shares.

Callers that will write must lock the order before any wallet row. A frozen
set is never recomputed: changes to the paid amount or the set need manual
reconciliation rather than a silent rewrite of an existing settlement.
"""
import hashlib
import json
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import models
from .models_line_f import SettlementItem
from .models_packages import Order


def _needs_reconciliation():
    raise HTTPException(409, "訂單券與付款分配需要人工核對，請聯絡客服")


def lock_order(db: Session, order_id: int):
    """Lock the order before locking any of its wallets (also on SQLite)."""
    if db.get_bind().dialect.name == "sqlite":
        db.query(Order).filter_by(id=order_id).update(
            {"paid_amount": Order.paid_amount}, synchronize_session=False)
    return (db.query(Order).filter_by(id=order_id).with_for_update()
            .populate_existing().first())


def _paid_cents(order):
    try:
        amount = Decimal(str(order.paid_amount))
        cents = amount * 100
        if not amount.is_finite() or amount < 0 or cents != cents.to_integral_value():
            _needs_reconciliation()
        return int(cents)
    except (InvalidOperation, ValueError, TypeError):
        _needs_reconciliation()


def _expected(order, rows):
    if order is None or order.payment_status != "paid":
        _needs_reconciliation()
    if not rows or any(row.source != "purchase" or row.user_id != order.user_id for row in rows):
        _needs_reconciliation()
    if len({row.coupon_id for row in rows}) != 1:
        _needs_reconciliation()
    total_cents = _paid_cents(order)
    fingerprint = hashlib.sha256(json.dumps({
        "order_id": order.id, "paid_cents": total_cents,
        "wallets": [[r.id, r.coupon_id, r.user_id, r.source] for r in rows],
    }, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    base, remainder = divmod(total_cents, len(rows))
    allocations = {r.id: Decimal(base + (i < remainder)) / Decimal(100)
                   for i, r in enumerate(rows)}
    return fingerprint, allocations


def read_frozen_allocations(db: Session, order):
    """Read a frozen set without taking write locks in reporting paths."""
    rows = (db.query(models.WalletItem).filter_by(order_id=order.id)
            .order_by(models.WalletItem.id).all())
    fingerprint, allocations = _expected(order, rows)
    if (order.wallet_allocation_fingerprint != fingerprint or
            any(r.settlement_allocated_amount is None or
                Decimal(str(r.settlement_allocated_amount)) != allocations[r.id]
                for r in rows)):
        _needs_reconciliation()
    return allocations


def ensure_order_allocations(db: Session, order_id: int):
    """Lock and freeze all purchase vouchers; never silently change history.

    The existing product has no per-line price/weight, so only identical
    coupon templates may be equally allocated. The deterministic remainder
    belongs to lower wallet IDs; the sum is exactly the paid amount.
    """
    order = lock_order(db, order_id)
    if order is None:
        _needs_reconciliation()
    rows = (db.query(models.WalletItem).filter_by(order_id=order.id)
            .order_by(models.WalletItem.id).with_for_update()
            .populate_existing().all())
    fingerprint, allocations = _expected(order, rows)
    if order.wallet_allocation_fingerprint is not None:
        if (order.wallet_allocation_fingerprint != fingerprint or
                any(r.settlement_allocated_amount is None or
                    Decimal(str(r.settlement_allocated_amount)) != allocations[r.id]
                    for r in rows)):
            _needs_reconciliation()
        return allocations
    if any(r.settlement_allocated_amount is not None for r in rows):
        _needs_reconciliation()
    if len(rows) > 1:
        # A legacy multi-voucher order might already have been booked at the
        # full order amount per item. Do not change those published amounts.
        reds = (db.query(models.Redemption).filter(
            models.Redemption.wallet_item_id.in_([r.id for r in rows])).all())
        for red in reds:
            expected = allocations[red.wallet_item_id]
            booked = db.query(SettlementItem).filter_by(redemption_id=red.id).first()
            if (red.face_value is None or Decimal(str(red.face_value)) != expected or
                    (booked is not None and Decimal(str(booked.amount)) != expected)):
                _needs_reconciliation()
    for row in rows:
        row.settlement_allocated_amount = allocations[row.id]
    order.wallet_allocation_fingerprint = fingerprint
    db.flush()
    return allocations
