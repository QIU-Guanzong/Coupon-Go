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
  via the Transfer Reversal API records it.
- kind="payout": one row per merchant withdrawal, status paid_out,
  net_amount negative, redemption_id NULL.
- Balances are derived, never stored: pending = sum of pending
  transfer nets; available = sum of transferred transfer nets minus
  paid-out totals; paid_out = sum of payout magnitudes.
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
    details_submitted = Column(Boolean, default=False, nullable=False)
    onboarding_status = Column(String(20), default="pending", nullable=False)
    manual_payouts = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


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
    pending_reversal = Column(Boolean, default=False, nullable=False)
    note = Column(String(255), default="")
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
