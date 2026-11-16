"""Line D — cashback models (返现 2B).

Contract: FEATURE_RESEARCH_20261116.md §11.1 (data model) / §12 (interfaces).
Tables follow the report's names and fields; the additions beyond the report
are marked with "D-extension" and documented in README §返现.

Two separate state machines — never merged into one:
  * online / general: tracked -> pending -> confirmed -> withdrawable.
    Confirmation is time-based: the merchant's return/cancel window
    (expected_confirm_at, default 30 days).
  * dine-in (Miso's rule, report §3.2b): pending -> confirmed -> withdrawable,
    unlocked ONLY by a next spend at the same merchant within 90 days.
    No next visit within 90 days -> void (expired). Tracking is by
    redemption record (redemption_id), never by click Cookie.

Cashback is booked separately from points: never touch point_transactions.
Funding default: merchant commission (funded_by="merchant_commission").
"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Text

from .database import Base


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# D-extension: "withdrawn" is a terminal state the report's enum does not
# list. It marks a transaction fully consumed by a withdrawal. Partial
# consumption is tracked in withdrawn_amount (a withdrawable transaction
# stays withdrawable until fully consumed).
CASHBACK_STATUSES = (
    "tracked", "pending", "confirmed", "withdrawable", "void", "withdrawn",
)
CASHBACK_CHANNELS = ("dinein", "online")
CASHBACK_VOID_REASONS = ("cancelled", "returned", "expired_90d")


class CashbackTransaction(Base):
    """One cashback earning. Mirrors report table cashback_transactions."""
    __tablename__ = "cashback_transactions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # merchant_id in the report = shop id (shops are the merchants).
    merchant_id = Column(Integer, ForeignKey("shops.id"), nullable=False, index=True)
    # FIX-2: redemption_id is REQUIRED and UNIQUE — a cashback earning
    # only ever comes from one redemption record, and one redemption
    # credits at most once. There is no user-supplied record path anymore
    # (POST /cashback/record was deleted); the only writer is
    # cashback_service.record_spend(), called from the
    # successful vendor scan. Legacy rows created before FIX-2 may still
    # hold NULL here; the 0004 migration enforces NOT NULL only when no
    # NULL rows exist (see alembic/versions/0004_*).
    redemption_id = Column(Integer, ForeignKey("redemptions.id"),
                           nullable=False, unique=True)
    # dinein = 线下餐饮 (next-visit unlock) | online = 线上/通用 (time-based confirm)
    channel = Column(String(20), default="dinein", nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)          # HK$ cashback earned
    rate = Column(Numeric(5, 4), nullable=False)             # e.g. 0.0500 = 5%
    # tracked | pending | confirmed | withdrawable | void | withdrawn (D-ext)
    status = Column(String(20), default="pending", nullable=False)
    funded_by = Column(String(30), default="merchant_commission", nullable=False)
    expected_confirm_at = Column(DateTime, nullable=True)     # online: confirm window
    expires_at = Column(DateTime, nullable=True)              # dinein: unlock deadline (+90d)
    confirmed_at = Column(DateTime, nullable=True)
    withdrawn_amount = Column(Numeric(10, 2), default=Decimal("0"), nullable=False)
    withdrawal_id = Column(Integer, ForeignKey("cashback_withdrawals.id"), nullable=True)
    void_reason = Column(String(30), nullable=True)          # cancelled | returned | expired_90d
    created_at = Column(DateTime, default=_now)


class CashbackWithdrawal(Base):
    """One withdrawal request. Mirrors report table cashback_withdrawals."""
    __tablename__ = "cashback_withdrawals"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    amount = Column(Numeric(10, 2), nullable=False)
    method = Column(String(20), nullable=False)              # fps | bank
    account_name = Column(String(120), nullable=False)       # must match registered name
    account_ref = Column(String(120), default="")            # FPS ID / bank account (v1 demo field)
    # pending | paid | failed. v1: pending until ops pays out manually —
    # there is no live FPS/bank payout integration in this build.
    status = Column(String(20), default="pending", nullable=False)
    created_at = Column(DateTime, default=_now)
    processed_at = Column(DateTime, nullable=True)


class CashbackAppeal(Base):
    """Missing Cashback appeal (report §3.5 / §12 POST /cashback/missing).

    Table name is a D-line choice — the report specifies the interface, not
    the table name. Submit within 30 days of the spend; investigation
    window up to 120 days (report §3.5).
    """
    __tablename__ = "cashback_appeals"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    merchant_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    order_no = Column(String(80), nullable=False)            # 订单号 / 核销凭证号
    spent_at = Column(DateTime, nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)          # 消费金额 (HK$)
    evidence = Column(Text, default="")                      # 订单确认截图链接/描述
    note = Column(Text, default="")
    # submitted | under_review | resolved | rejected
    status = Column(String(20), default="submitted", nullable=False)
    created_at = Column(DateTime, default=_now)
    resolved_at = Column(DateTime, nullable=True)
