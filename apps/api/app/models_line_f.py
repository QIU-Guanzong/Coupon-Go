"""Line F models: admin v2 (F4 v2) — settlements, reconciliation,
invoices, CMS content, roles / shop staff.

Kept in a separate module from app.models so line F only adds new tables
and never edits the v0.1 tables (except one additive column on
categories, see app.models.Category.is_active). Imported by app.models
(one line) so Base.metadata.create_all() and Alembic see them.

Rules (FEATURE_RESEARCH_20261116.md §5.1 / §11):
- Settlement is per-redemption (按核銷結算), not per-sale: a settlement
  is generated for one shop + one YYYY-MM period from that period's
  redemptions. gross = sum of redeemed coupons' face value
  (cash_amount / discount_price / regular_price, else 0);
  commission = gross * commission_rate (rate is a per-generation input —
  the real rate is negotiated per merchant, report §14 #6; v2 stores
  whatever rate was used); net = gross - commission.
  Status: pending -> approved / disputed -> paid. A disputed settlement
  returns to pending or approved via the resolve endpoint.
- Reconciliation compares the period's redemptions against the
  settlement lines and records every difference as a discrepancy row
  (missing settlement line / missing redemption / amount mismatch).
- Invoices: a merchant (or platform finance) requests an invoice against
  a settlement; finance approves, then marks issued. The invoiced total
  for one settlement (excluding rejected) may never exceed net_amount.
- CMS: banners / announcements are platform-operated content with a
  draft/active/archived lifecycle and a display window (start_at/end_at).
  Category content management reuses the v0.1 categories table.
- Roles: users.role gains merchant-admin / finance / auditor / support
  (see app.permissions_line_f). Shop staff membership is explicit:
  shop_members links a merchant-admin user to one shop. Platform admin
  keeps every permission, so the v1 single-admin setup upgrades without
  any change to existing accounts or v1 endpoints.
"""
import json
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text,
    UniqueConstraint,
)

from .database import Base


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_list(value):
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


class Settlement(Base):
    """Monthly (YYYY-MM) settlement statement for one shop.

    status: pending | approved | disputed | paid.
    """
    __tablename__ = "settlements"
    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    period = Column(String(7), nullable=False)  # YYYY-MM
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    redemption_count = Column(Integer, default=0, nullable=False)
    gross_amount = Column(Numeric(12, 2), default=0, nullable=False)
    commission_rate = Column(Numeric(5, 4), default=0, nullable=False)
    commission = Column(Numeric(12, 2), default=0, nullable=False)
    net_amount = Column(Numeric(12, 2), default=0, nullable=False)
    status = Column(String(20), default="pending", nullable=False)
    dispute_reason = Column(Text, default="")
    generated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("shop_id", "period", name="uq_settlement_shop_period"),
    )


class SettlementItem(Base):
    """One settlement line = one redemption. A redemption belongs to at
    most one settlement (unique redemption_id)."""
    __tablename__ = "settlement_items"
    id = Column(Integer, primary_key=True)
    settlement_id = Column(Integer, ForeignKey("settlements.id"), nullable=False)
    redemption_id = Column(Integer, ForeignKey("redemptions.id"),
                           nullable=False, unique=True)
    coupon_id = Column(Integer, ForeignKey("coupons.id"), nullable=False)
    amount = Column(Numeric(12, 2), default=0, nullable=False)
    commission = Column(Numeric(12, 2), default=0, nullable=False)
    created_at = Column(DateTime, default=_now)


class ReconciliationReport(Base):
    """One reconciliation run for a shop + period.

    status: open (has unresolved discrepancies) | resolved | no_settlement
    (ran before any settlement existed for the period).
    """
    __tablename__ = "reconciliation_reports"
    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    period = Column(String(7), nullable=False)
    settlement_id = Column(Integer, ForeignKey("settlements.id"), nullable=True)
    redemption_count = Column(Integer, default=0, nullable=False)
    settlement_line_count = Column(Integer, default=0, nullable=False)
    discrepancy_count = Column(Integer, default=0, nullable=False)
    status = Column(String(20), default="open", nullable=False)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("shop_id", "period", name="uq_recon_shop_period"),
    )


class ReconciliationDiscrepancy(Base):
    """One difference found by a reconciliation run.

    discrepancy_type: missing_settlement_line | missing_redemption |
    amount_mismatch. status: open | resolved.
    """
    __tablename__ = "reconciliation_discrepancies"
    id = Column(Integer, primary_key=True)
    report_id = Column(Integer, ForeignKey("reconciliation_reports.id"),
                       nullable=False)
    discrepancy_type = Column(String(40), nullable=False)
    redemption_id = Column(Integer, ForeignKey("redemptions.id"), nullable=True)
    settlement_item_id = Column(Integer, ForeignKey("settlement_items.id"),
                                nullable=True)
    expected_amount = Column(Numeric(12, 2), nullable=True)
    actual_amount = Column(Numeric(12, 2), nullable=True)
    status = Column(String(20), default="open", nullable=False)
    resolution = Column(Text, default="")
    resolved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)


class Invoice(Base):
    """Invoice request / record.

    status: pending | approved | rejected | issued. An invoice is
    normally raised against a settlement (settlement_id); a standalone
    invoice (settlement_id null) is allowed for non-settlement billing.
    """
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    settlement_id = Column(Integer, ForeignKey("settlements.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    title = Column(String(160), nullable=False)   # 抬頭
    tax_id = Column(String(60), default="")       # 統一編號 / 稅號
    invoice_type = Column(String(20), default="receipt", nullable=False)
    status = Column(String(20), default="pending", nullable=False)
    reject_reason = Column(Text, default="")
    decided_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    issued_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class CmsBanner(Base):
    """Homepage banner managed by platform ops (report §11: cms_banners).

    status: draft | active | archived. Only active banners inside their
    display window are served publicly.
    """
    __tablename__ = "cms_banners"
    id = Column(Integer, primary_key=True)
    title = Column(String(160), nullable=False)
    image = Column(String(255), default="")       # asset path / URL
    link_type = Column(String(20), default="none", nullable=False)
    link_target = Column(String(255), default="")
    sort_order = Column(Integer, default=0, nullable=False)
    start_at = Column(DateTime, nullable=True)
    end_at = Column(DateTime, nullable=True)
    status = Column(String(20), default="draft", nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class CmsAnnouncement(Base):
    """Platform announcement (公告), same lifecycle as banners."""
    __tablename__ = "cms_announcements"
    id = Column(Integer, primary_key=True)
    title = Column(String(160), nullable=False)
    body = Column(Text, default="")
    status = Column(String(20), default="draft", nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    start_at = Column(DateTime, nullable=True)
    end_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class ShopMember(Base):
    """Shop staff membership: links a merchant-admin user to one shop.

    A member may read that shop's settlements / reconciliation / invoices
    and raise invoice requests for it. Platform role of the user must be
    merchant-admin (set automatically on add when the user is a customer).
    """
    __tablename__ = "shop_members"
    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role = Column(String(20), default="merchant-admin", nullable=False)
    added_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_now)

    __table_args__ = (
        UniqueConstraint("shop_id", "user_id", name="uq_shop_member"),
    )
