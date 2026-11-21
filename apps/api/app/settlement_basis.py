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
- cash coupon -> 0. A face-value discount (e.g. "滿 $200 減 $30")
  is not a platform collection, even for legacy positive snapshots.
- anything else (gift / percentage-only / pure-threshold coupon) ->
  0. regular_price is NEVER a basis: a free coupon settling at
  regular_price was the platform paying the merchant for a freebie.

Snapshot rule (F6 mechanism kept): when a redemption row is passed
and its face_value snapshot is not NULL, the snapshot wins over the
live coupon / order rows, so a later price change cannot drift the
basis. The refund and M8 cash-coupon rules run before the snapshot;
existing issued settlement entries remain unchanged.
Legacy NULL snapshots fall back to the live rows below. Migration
0014 rewrites pre-M6 snapshots to the historical M6 basis; the M8
zero-cash rule is enforced at runtime without rewriting history.
"""
from decimal import Decimal

from sqlalchemy.orm import Session

from . import models
from .models_packages import Order
from .order_allocation import read_frozen_allocations


def settlement_basis_note(db: Session, coupon: models.Coupon = None,
                          redemption: models.Redemption = None,
                          wallet_item: models.WalletItem = None) -> str:
    """One-word reason why a redemption settles at its basis (§2.5).

    Mirrors the settlement_basis branches so the merchant can see WHY
    a line is what it is, without the basis itself changing:
    - refunded_skip: its package order was refunded — never settles.
    - cash_zero: cash coupon — a discount, not money collected.
    - package_paid: package redemption — the order's paid_amount.
    - price_coupon: price coupon — the coupon's discount_price.
    - free_zero: gift / percentage-only / pure-threshold coupon — 0.
    """
    from .models_packages import Order

    item = wallet_item
    if item is None and redemption is not None:
        item = db.query(models.WalletItem).filter(
            models.WalletItem.id == redemption.wallet_item_id).first()
    order = None
    if item is not None and getattr(item, "order_id", None) is not None:
        order = db.query(Order).filter(Order.id == item.order_id).first()
    if order is not None and order.refund_status == "refunded":
        return "refunded_skip"
    if order is None:
        if coupon is None and redemption is not None:
            coupon = db.get(models.Coupon, redemption.coupon_id)
        if coupon is not None and (coupon.coupon_type or "").strip().lower() == "cash":
            return "cash_zero"
    if order is not None:
        return "package_paid"
    if coupon is not None:
        if getattr(coupon, "discount_price", None) is not None:
            return "price_coupon"
    return "free_zero"


BASIS_EXPLAINER = (
    "结算基数只认实付金额：套餐按订单实付金额，价格券按券面优惠价；"
    "现金券、免费券、纯门槛券不进结算（0 元）；已退款订单不结算。"
    "佣金按商户生效费率从基数中扣除。")


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
    if order is not None:
        count = db.query(models.WalletItem).filter_by(order_id=order.id).count()
        if count > 1 or order.wallet_allocation_fingerprint is not None:
            allocations = read_frozen_allocations(db, order)
            amount = allocations[item.id]
            if redemption is not None and redemption.face_value is not None:
                if Decimal(str(redemption.face_value)) != amount:
                    from fastapi import HTTPException
                    raise HTTPException(409, "歷史核銷金額需要人工核對，請聯絡客服")
            return amount
    # A Cash coupon is a discount, not money collected by the platform.
    # Also apply this rule to pre-M8 positive face-value snapshots.
    if order is None:
        if coupon is None and redemption is not None:
            coupon = db.get(models.Coupon, redemption.coupon_id)
        if coupon is not None and (coupon.coupon_type or "").strip().lower() == "cash":
            return Decimal("0")
    # F6: prefer the snapshot taken at scan time for other coupons.
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
    return Decimal("0")
