"""P1-B models: Stripe Connect (Accounts v2) accounts + merchant balance ledger.

Imported by app.models (one line) so Base.metadata.create_all() and
Alembic see them. No bank-account data is stored anywhere here: Stripe
collects the merchant's identity and bank account during onboarding;
we keep only the Stripe account id and the readiness flags Stripe
reports (charges_enabled / payouts_enabled / details_submitted).

Ledger semantics (docs/P1B_API.md): transfer and payout requests are
reserved before external I/O; uncertain outcomes remain frozen until
verified against a matching Stripe object. pending_reversal reserves
funds immediately, including while a transfer result is in flight.
"""
from datetime import datetime, timezone
from uuid import uuid4

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
    status: pending | processing | unknown | transferred | reversed |
            reversal_processing | reversal_unknown | submitted |
            payout_unknown | paid_out | failed.
    For kind=transfer, gross/commission/net are the redemption's
    settlement basis / platform commission / merchant net (HKD).
    For kind=payout, gross/commission are 0 and net is negative.
    note records why a transfer is still pending (not_configured /
    no_account / account_not_ready / failed: …) — never a silent state.
    """
    __tablename__ = "connect_ledger_entries"
    __table_args__ = (
        UniqueConstraint("shop_id", "kind", "client_request_id",
                         name="uq_connect_ledger_client_request"),
    )
    id = Column(Integer, primary_key=True)
    # Globally unique across databases/environments; never derived from a
    # local integer sequence that can be reused after a restore or clone.
    operation_id = Column(String(32), nullable=False, unique=True,
                          default=lambda: uuid4().hex)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False, index=True)
    redemption_id = Column(Integer, ForeignKey("redemptions.id"),
                           nullable=True, unique=True)
    # The current issuance model grants one wallet item per paid order.
    # Prevent paying the same order twice through duplicate redemptions.
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True, unique=True)
    payment_intent_id = Column(String(80), nullable=False, default="")
    stripe_charge_id = Column(String(80), nullable=False, default="")
    kind = Column(String(20), nullable=False)  # transfer | payout
    status = Column(String(32), default="pending", nullable=False)
    gross_amount = Column(Numeric(12, 2), default=0, nullable=False)
    commission = Column(Numeric(12, 2), default=0, nullable=False)
    net_amount = Column(Numeric(12, 2), default=0, nullable=False)
    stripe_transfer_id = Column(String(60), default="")
    stripe_reversal_id = Column(String(60), default="")
    stripe_payout_id = Column(String(60), default="")
    # Client operation identity and destination are persisted before any
    # network call, so a timeout cannot create a second withdrawal.
    client_request_id = Column(String(64), nullable=True)
    destination_account_id = Column(String(60), nullable=False, default="")
    first_attempt_at = Column(DateTime, nullable=True)
    last_attempt_at = Column(DateTime, nullable=True)
    last_polled_at = Column(DateTime, nullable=True, index=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    pending_reversal = Column(Boolean, default=False, nullable=False)
    note = Column(String(255), default="")
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class ConnectReconciliationEvent(Base):
    """Append-only provider reconciliation history for automated and admin checks."""
    __tablename__ = "connect_reconciliation_events"
    id = Column(Integer, primary_key=True)
    entry_id = Column(Integer, ForeignKey("connect_ledger_entries.id"), nullable=False, index=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    source = Column(String(20), nullable=False)
    prior_status = Column(String(32), nullable=False)
    new_status = Column(String(32), nullable=False)
    provider_id = Column(String(80), nullable=False)
    created_at = Column(DateTime, nullable=False, default=_now)
