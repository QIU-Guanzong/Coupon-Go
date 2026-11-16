"""Line C (套餐 2A) models: packages / orders / payments / refunds.

Contract source: docs/FEATURE_RESEARCH_20261116.md §11 (data model) and
§12 (interface list). Field names follow the report: packages / orders
keep the report's fields verbatim; settlement bookkeeping fields on orders
(settlement_status / commission_amount / net_amount) are additive — the
reconciliation itself is line F, this line only records the ledger fields.
`payments` and `refunds` are additive tables (the report only defines
orders.payment_status / orders.refund_status as aggregates); they exist so
a payment can fail / time out / be retried and a refund has an audit row.

Money is HKD. No real gateway keys live here or anywhere in the repo:
payment goes through app.payments_gateway (Stripe test mode).
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, Numeric, String, Text,
)

from .database import Base


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Refund policies (packages.refund_policy, coupons.refund_policy):
#   anytime       — 未核销随时退 + 过期自动退（默认）
#   expiry_only   — 仅过期自动退，过期前不接受手动退款
#   non_refundable — 特价券：不退（过期只作废，不退款）
REFUND_POLICIES = ("anytime", "expiry_only", "non_refundable")

# Order payment statuses:
#   pending — 已下单待支付；paid — 支付成功；
#   failed — 支付失败（可重试）；timeout — 支付超时（可重试）；
#   cancelled — 用户/系统取消（可重新下单）
PAYMENT_STATUSES = ("pending", "paid", "failed", "timeout", "cancelled")
# Order refund statuses: none | requested | refunded | rejected
REFUND_STATUSES = ("none", "requested", "refunded", "rejected")
# Settlement ledger (reconciliation is line F; this line only books):
#   pending — 待结算；settled — 已结算（F 线对账后）；void — 已退款/作废，不结算
SETTLEMENT_STATUSES = ("pending", "settled", "void")


class Package(Base):
    """A paid set-meal product (套餐商品). Buying one generates a
    package coupon (coupons.kind='package') into the wallet — the coupon
    itself is the voucher; the package is the product, not the voucher."""
    __tablename__ = "packages"
    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    name = Column(String(160), nullable=False)
    name_en = Column(String(160), default="")
    # 菜品组合 JSON: [{"name": "...", "qty": 1}, ...]
    items = Column(Text, default="[]")
    person_count = Column(Integer, default=2, nullable=False)  # 适用人数
    original_price = Column(Numeric(10, 2), nullable=False)    # 原价
    package_price = Column(Numeric(10, 2), nullable=False)     # 套餐价
    valid_from = Column(DateTime, nullable=False)
    valid_to = Column(DateTime, nullable=False)
    # 使用规则 JSON: {"weekday_only": false, "holiday": true,
    #   "need_booking": true, "includes": [...], "excludes": [...]}
    usage_rules = Column(Text, default="{}")
    stock_total = Column(Integer, default=0, nullable=False)
    stock_left = Column(Integer, default=0, nullable=False)
    refund_policy = Column(String(20), default="anytime", nullable=False)
    # active | inactive | sold_out
    status = Column(String(20), default="active", nullable=False)
    created_at = Column(DateTime, default=_now)


class Order(Base):
    """A paid package order. Free coupon claims never create orders."""
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    # package (report also reserves 'cashback-eligible' for line B)
    order_type = Column(String(30), default="package", nullable=False)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=True)
    amount = Column(Numeric(10, 2), nullable=False)       # 应付金额
    paid_amount = Column(Numeric(10, 2), default=0)       # 实付金额
    payment_method = Column(String(20), default="")       # fps | card
    payment_status = Column(String(20), default="pending", nullable=False)
    refund_status = Column(String(20), default="none", nullable=False)
    # --- settlement ledger (line C books, line F reconciles) ---
    settlement_status = Column(String(20), default="pending", nullable=False)
    commission_amount = Column(Numeric(10, 2), default=0)  # 佣金（费率未定，默认 0）
    net_amount = Column(Numeric(10, 2), default=0)         # 应结算净额
    client_ref = Column(String(64), default="")  # 客户端幂等键（下单重试去重）
    created_at = Column(DateTime, default=_now)
    paid_at = Column(DateTime, nullable=True)


class Payment(Base):
    """One gateway payment attempt for an order. A failed / timed-out
    order is retried by creating a NEW attempt, never by mutating the
    old one — the attempt history is the audit trail."""
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    gateway = Column(String(20), default="stripe", nullable=False)
    gateway_ref = Column(String(80), default="")  # Stripe payment_intent id
    client_secret = Column(String(255), default="")  # 供 App 完成支付；幂等重放时复用
    method = Column(String(20), default="")       # fps | card
    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(10), default="HKD", nullable=False)
    # requires_payment | processing | succeeded | failed | cancelled |
    # timeout | refunded
    status = Column(String(20), default="requires_payment", nullable=False)
    failure_message = Column(String(255), default="")
    created_at = Column(DateTime, default=_now)


class Refund(Base):
    """One refund record for an order (原路返回，1–3 个工作日到账）。"""
    __tablename__ = "refunds"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    reason = Column(String(255), default="")
    # requested | refunded | rejected
    status = Column(String(20), default="requested", nullable=False)
    gateway_ref = Column(String(80), default="")  # Stripe refund id
    created_at = Column(DateTime, default=_now)
