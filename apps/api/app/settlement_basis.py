"""M6: the single settlement-basis function.

Settlement generation, reconciliation and the scan-time face-value
snapshot all read this one function — never three copies of the
basis. It replaces admin_v2._face_value (cash_amount, else
discount_price, else regular_price) and the inline copy of that
order that scan_coupon used to carry.

Basis (actual paid amount, not coupon face value):
- package redemption (its wallet item links an order) -> the order's
  paid_amount. If that order's refund_status is "refunded" the
  redemption must not settle at all: return None (the caller skips
  it in settlement generation; reconciliation treats an existing
  settlement line for it as amount_mismatch with expected 0).
  A refund that is only "requested" still settles — the money has
  not been returned yet.
- price coupon -> discount_price.
- cash coupon -> cash_amount (the merchant-issued voucher
  denomination; e.g. "滿 $200 減 $30" settles the $30 the merchant
  gave up, not the customer's spend).
- anything else (gift / percentage-only / pure-threshold coupon) ->
  0. regular_price is NEVER a basis: a free coupon settling at
  regular_price was the platform paying the merchant for a freebie.

Snapshot rule (F6 mechanism kept): when a redemption row is passed
and its face_value snapshot is not NULL, the snapshot wins over the
live coupon / order rows, so a later price change cannot drift the
basis. The refund check above runs BEFORE the snapshot: a refunded
order's redemption is excluded even if it carries a snapshot.
Legacy NULL snapshots fall back to the live rows below. Migration
0014 rewrites pre-M6 snapshots (old basis) to this basis.
"""
from decimal import Decimal

from sqlalchemy.orm import Session

from . import models
from .models_packages import Order


def settlement_basis(db: Session, coupon: models.Coupon = None,
                     redemption: models.Redemption = None,
                     wallet_item: models.WalletItem = None):
    """Amount one redemption contributes to a settlement.

    Returns Decimal, or None when the redemption must not settle
    (its package order was refunded).
    """
    item = wallet_item
    if item is None and redemption is not None:
        item = db.query(models.WalletItem).filter(
            models.WalletItem.id == redemption.wallet_item_id).first()
    order = None
    if item is not None and getattr(item, "order_id", None) is not None:
        order = db.query(Order).filter(Order.id == item.order_id).first()
    # Refunded orders never settle, snapshot or not.
    if order is not None and order.refund_status == "refunded":
        return None
    # F6: prefer the snapshot taken at scan time.
    if redemption is not None and getattr(redemption, "face_value", None) \
            is not None:
        return Decimal(str(redemption.face_value))
    # Legacy NULL snapshot (or scan time, where no redemption exists
    # yet): compute from the live rows under the new basis.
    if order is not None:
        return Decimal(str(order.paid_amount or 0))
    if coupon is not None:
        if getattr(coupon, "discount_price", None) is not None:
            return Decimal(str(coupon.discount_price))
        if getattr(coupon, "cash_amount", None) is not None:
            return Decimal(str(coupon.cash_amount))
    return Decimal("0")
