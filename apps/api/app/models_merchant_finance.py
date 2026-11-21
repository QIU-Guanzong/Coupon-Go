"""Merchant withdrawals (Dianping §2.2, P0-4).

A withdrawal draws from ONE approved settlement: the merchant asks for
part (or all) of that settlement's outstanding net, finance approves
or rejects it, and the money only moves when the payout executes
through P1-B Stripe Connect (see app.payouts). Until the payout
executes, the amount is *reserved* — it leaves the withdrawable
balance but is not yet withdrawn.

status: pending -> approved -> paid; pending -> rejected / cancelled;
paid -> reversed (the payout bounced after the fact; the money comes
back as a negative SettlementTransfer so the settlement is withdrawable
again). Rejected / cancelled / reversed withdrawals free their
reservation immediately.

Money never moves in this module: the payout itself lives in
app.payouts.execute_merchant_payout, which fails closed while P1-B
is not wired in. This table only records the request, the decision
and the payout reference.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Text

from .database import Base


def _now():
    return datetime.now()


class MerchantWithdrawal(Base):
    __tablename__ = "merchant_withdrawals"
    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    # The approved settlement this withdrawal draws from. One
    # withdrawal never spans two settlements — the frontend picks the
    # oldest approved settlement with outstanding balance first.
    settlement_id = Column(Integer, ForeignKey("settlements.id"),
                           nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    # pending | approved | paid | rejected | cancelled | reversed
    status = Column(String(20), default="pending", nullable=False)
    # Payout reference returned by the payout provider (P1-B Stripe
    # Connect transfer id). Empty until the payout executes.
    payout_reference = Column(String(120), default="")
    payout_provider = Column(String(40), default="")
    reject_reason = Column(Text, default="")
    decided_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
