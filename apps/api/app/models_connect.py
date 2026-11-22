"""P1-B models: Stripe Connect (Accounts v2) accounts + merchant balance ledger.

Imported by app.models (one line) so Base.metadata.create_all() and
Alembic see them. No bank-account data is stored anywhere here: Stripe
collects the merchant's identity and bank account during onboarding;
we keep only the Stripe account id and the readiness flags Stripe
reports (charges_enabled / payouts_enabled / details_submitted).

Ledger semantics (docs/P1B_API.md):
- kind="transfer": one row per redemption at most (redemption_id
  unique when set). status pending (待结算) -> transferred (已转帐)
  -> reversed (已冲回). A pending entry reversed before any Stripe
  transfer has no stripe_reversal_id; a transferred entry reversed
  via the Transfer Reversal API records it. After
  CONNECT_MAX_TRANSFER_RETRIES (default 5) failed transfer attempts
  the entry goes needs_manual (需人工) and an admin notification is
  raised once (manual_notified); the manual retry endpoint is the
  recovery path. retry_count counts failed transfer AND reversal
  attempts on the entry (reset on success).
- kind="payout": one row per merchant withdrawal, status processing
  (处理中, balance already deducted at write time) -> paid_out, or
  failed (gateway failed, balance released). net_amount negative,
  redemption_id NULL.
- Balances are derived, never stored: pending = sum of pending AND
  needs_manual transfer nets; available = sum of transferred transfer
  nets minus paid-out totals minus processing payouts; paid_out =
  sum of payout magnitudes. Available MAY go negative (a transfer
  reversed after the money was paid out); while negative, new
  payouts are refused — see docs/P1B_API.md §F6.
- pending_reversal: a transferred entry whose reversal was requested
  but the gateway call failed — the retry endpoint picks it up.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String,
    UniqueConstraint,
)

from .database import Base


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ConnectAccount(Base):
    """One Stripe Accounts v2 connected account per shop.

    onboarding_status: pending | incomplete | active. active iff
    charges_enabled and payouts_enabled (Stripe is the source of
    truth; the return_url redirect is not a success event).
    """
    __tablename__ = "connect_accounts"
    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False,
                     unique=True)
    stripe_account_id = Column(String(60), nullable=False)
    charges_enabled = Column(Boolean, default=False, nullable=False)
    payouts_enabled = Column(Boolean, default=False, nullable=False)
    # NULL on legacy rows means it has never been verified with Stripe.
    transfers_enabled = Column(Boolean, nullable=True)
    details_submitted = Column(Boolean, default=False, nullable=False)
    onboarding_status = Column(String(20), default="pending", nullable=False)
    manual_payouts = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class ApplicationConnectAccount(Base):
    """Durable provider operation, owned by one merchant application."""
    __tablename__ = "application_connect_accounts"
    id = Column(Integer, primary_key=True)
    application_id = Column(Integer, ForeignKey("merchant_applications.id"),
                            nullable=False, unique=True)
    operation_key = Column(String(80), nullable=False, unique=True)
    contact_email = Column(String(255), nullable=False)
    state = Column(String(20), nullable=False, default="creating")
    started_at = Column(DateTime, nullable=False, default=_now)
    stripe_account_id = Column(String(60), nullable=True, unique=True)
    simulated = Column(Boolean, nullable=False, default=False)
    verified_at = Column(DateTime, nullable=True)
    charges_enabled = Column(Boolean, nullable=False, default=False)
    payouts_enabled = Column(Boolean, nullable=False, default=False)
    transfers_enabled = Column(Boolean, nullable=False, default=False)
    details_submitted = Column(Boolean, nullable=False, default=False)
    manual_payouts = Column(Boolean, nullable=False, default=False)


class ShopConnectOperation(Base):
    """A legacy shop's durable, reusable provider account operation."""
    __tablename__ = "shop_connect_operations"
    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False, unique=True)
    operation_key = Column(String(80), nullable=False, unique=True)
    contact_email = Column(String(255), nullable=False)
    started_at = Column(DateTime, nullable=False, default=_now)


class ConnectLedgerEntry(Base):
    """One merchant-balance ledger event.

    kind: transfer | payout.
    status: pending | transferred | reversed | paid_out.
    For kind=transfer, gross/commission/net are the redemption's
    settlement basis / platform commission / merchant net (HKD).
    For kind=payout, gross/commission are 0 and net is negative.
    note records why a transfer is still pending (not_configured /
    no_account / account_not_ready / failed: …) — never a silent state.
    """
    __tablename__ = "connect_ledger_entries"
    __table_args__ = (
        UniqueConstraint("shop_id", "kind", "payout_request_id", name="uq_connect_payout_request"),
        UniqueConstraint("payout_operation_id", name="uq_connect_payout_operation"),
    )
    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False, index=True)
    redemption_id = Column(Integer, ForeignKey("redemptions.id"),
                           nullable=True, unique=True)
    kind = Column(String(20), nullable=False)  # transfer | payout
    status = Column(String(20), default="pending", nullable=False)
    gross_amount = Column(Numeric(12, 2), default=0, nullable=False)
    commission = Column(Numeric(12, 2), default=0, nullable=False)
    net_amount = Column(Numeric(12, 2), default=0, nullable=False)
    stripe_transfer_id = Column(String(60), default="")
    stripe_reversal_id = Column(String(60), default="")
    stripe_payout_id = Column(String(60), default="")
    # NULL is preserved on legacy rows: their dispatch history is unknown.
    payout_request_id = Column(String(64), nullable=True)
    payout_operation_id = Column(String(32), nullable=True)
    payout_account_id = Column(String(60), nullable=True)
    payout_started_at = Column(DateTime, nullable=True)
    payout_last_polled_at = Column(DateTime, nullable=True)
    # None marks a legacy entry whose dispatch history is not provable.
    # New transfer intents start False and become True before provider I/O.
    dispatch_started = Column(Boolean, nullable=True)
    pending_reversal = Column(Boolean, default=False, nullable=False)
    retry_count = Column(Integer, default=0, nullable=False)
    manual_notified = Column(Boolean, default=False, nullable=False)
    note = Column(String(255), default="")
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
