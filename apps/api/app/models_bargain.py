"""Bargain (2C) models — line E only.

Report: FEATURE_RESEARCH_20261116.md §11.1 (bargain_campaigns,
bargain_sessions, bargain_helps) + §11.3 (session state machine).

Flow (share-to-help bargain, NOT Dutch auction):
  start (prepay simulated, stock locked) -> friends help cut the price
  -> success (floor price reached OR helper limit reached) -> purchase
     at the cut price (generates a package coupon into Q Wallet,
     refunds the price-cut difference)
  -> failure (timeout / expired unpurchased) -> full refund + stock released

Money is simulated ledger only in this dev build: there is no payment
gateway yet (README: 支付未做). `paid_amount` records the simulated
prepayment; BargainRefund rows record what would be returned via the
original payment route (1-3 working days). No real transfer happens.

Deliberately NOT copied from the DT-life complaints (§4.5):
no 30% "occupancy" fee on refund, no "一经售出不退不换" refusal.
Default refund policy: unformed / timed-out = full refund; package
coupon unredeemed = refundable, expired = auto-refund; redeemed = not
refundable.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .database import Base


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BargainCampaign(Base):
    """One bargain activity for one package (line A `packages` id when it
    exists; plain int, no FK, so line E does not depend on line A's table).

    status: active | sold_out | expired
    """
    __tablename__ = "bargain_campaigns"
    id = Column(Integer, primary_key=True)
    package_id = Column(Integer, nullable=True)  # line A packages.id, if any
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    name = Column(String(160), nullable=False)
    items = Column(Text, default="")              # 套餐内容 (dishes / items)
    person_count = Column(Integer, default=2)
    original_price = Column(Numeric(10, 2), nullable=False)  # HKD
    floor_price = Column(Numeric(10, 2), nullable=False)     # HKD, 砍价底价
    stock_total = Column(Integer, nullable=False)
    stock_left = Column(Integer, nullable=False)
    helper_limit = Column(Integer, default=5)      # 成团人数 (helps to form)
    duration_minutes = Column(Integer, default=1440)  # session lifetime, 24h
    lock_stock = Column(Boolean, default=True)     # True = 发起即锁 1 份库存
    status = Column(String(20), default="active", nullable=False)
    expires_at = Column(DateTime, nullable=False)  # campaign end
    created_at = Column(DateTime, default=_now)

    shop = relationship("Shop")
    sessions = relationship("BargainSession", back_populates="campaign")


class BargainSession(Base):
    """One user's bargain attempt.

    status: active | success | failed | expired
      active  -> success  (floor reached, or helper_limit reached)
      active  -> failed   (timed out before success: full refund)
      success -> expired  (success but never purchased in time: full refund)
    stock_locked: True while 1 unit is held for this session; released on
    failed/expired, consumed (stays deducted) on purchase.
    """
    __tablename__ = "bargain_sessions"
    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("bargain_campaigns.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    current_price = Column(Numeric(10, 2), nullable=False)  # HKD
    target_price = Column(Numeric(10, 2), nullable=False)   # = floor_price
    paid_amount = Column(Numeric(10, 2), nullable=False)    # simulated prepay
    status = Column(String(20), default="active", nullable=False)
    helper_count = Column(Integer, default=0)
    stock_locked = Column(Boolean, default=True)
    wallet_item_id = Column(Integer, nullable=True)  # set on purchase
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=_now)

    campaign = relationship("BargainCampaign", back_populates="sessions")
    user = relationship("User")
    helps = relationship("BargainHelp", back_populates="session")
    refunds = relationship("BargainRefund", back_populates="session")


class BargainHelp(Base):
    """One friend's help. One row per (session, helper) — enforced by the
    unique constraint and by the router (duplicate help -> 409)."""
    __tablename__ = "bargain_helps"
    __table_args__ = (
        UniqueConstraint("session_id", "helper_user_id", name="uq_help_once"),
    )
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("bargain_sessions.id"), nullable=False)
    helper_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    cut_amount = Column(Numeric(10, 2), nullable=False)  # HKD cut by this help
    device_id = Column(String(120), default="")  # anti-fraud: device dedupe
    is_new_user = Column(Boolean, default=False)  # registered within 7 days
    created_at = Column(DateTime, default=_now)

    session = relationship("BargainSession", back_populates="helps")
    helper = relationship("User")


class BargainRefund(Base):
    """Refund ledger for a session (simulated in dev; see module docstring).

    reason: bargain_failed (timeout before success) | expired (success but
    never purchased) | price_cut (difference refunded on purchase).
    status: refunded (simulated original-route refund, 1-3 working days).
    """
    __tablename__ = "bargain_refunds"
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("bargain_sessions.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)  # HKD
    reason = Column(String(30), nullable=False)
    status = Column(String(20), default="refunded", nullable=False)
    note = Column(String(255), default="")
    created_at = Column(DateTime, default=_now)

    session = relationship("BargainSession", back_populates="refunds")
