"""PostgreSQL / SQLite models.

Field names follow the names recovered from the Coupon GO! 1.2.6 APK
(REVERSE.md §2.5) where they exist: coupon_id, coupon_name, shop_id,
vendor_id, valid_from / valid_to, discount_price / regular_price /
cash_amount / percentage, is_coupon_favorite, is_shop_favorite, etc.
Wallet status follows the original: 1 = 未使用, 2 = 已使用, 3 = 已失效.
Coupon types: Cash / Discount / Gift / Price.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .database import Base


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(120), default="")
    phone = Column(String(40), default="")
    # customer | vendor | admin
    role = Column(String(20), default="customer", nullable=False)
    locale = Column(String(10), default="zh-Hant")
    limited_time_notify = Column(Boolean, default=True)  # 限時搶購推送開關
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_now)

    shops = relationship("Shop", back_populates="vendor")
    wallet_items = relationship("WalletItem", back_populates="user")
    points = relationship("PointTransaction", back_populates="user")


class LegalAcceptance(Base):
    __tablename__ = "legal_acceptances"
    __table_args__ = (UniqueConstraint("user_id", "terms_version", "privacy_version",
                                       name="uq_legal_acceptance_versions"),)
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    terms_version = Column(String(64), nullable=False)
    privacy_version = Column(String(64), nullable=False)
    terms_url = Column(String(1024), nullable=False)
    privacy_url = Column(String(1024), nullable=False)
    accepted_at = Column(DateTime, default=_now, nullable=False)


class OtpCode(Base):
    """6-digit OTP for registration / password reset (dev: printable via tests)."""
    __tablename__ = "otp_codes"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), index=True, nullable=False)
    code = Column(String(10), nullable=False)
    purpose = Column(String(30), nullable=False)  # register | forgot
    role = Column(String(20), default="customer")
    used = Column(Boolean, default=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=_now)


class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    name = Column(String(60), nullable=False)       # 飲食
    name_en = Column(String(60), default="")
    icon = Column(String(120), default="")          # asset path, e.g. ic_category.png
    is_active = Column(Boolean, default=True)       # line F: CMS 可停用分類


class Area(Base):
    __tablename__ = "areas"
    id = Column(Integer, primary_key=True)
    name = Column(String(60), nullable=False)       # 香港 / 九龍 / 新界
    name_en = Column(String(60), default="")
    parent_id = Column(Integer, ForeignKey("areas.id"), nullable=True)


class Shop(Base):
    """Merchant shop (vendor-owned). One vendor may own several shops."""
    __tablename__ = "shops"
    id = Column(Integer, primary_key=True)
    vendor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(120), nullable=False)
    name_en = Column(String(120), default="")
    category_id = Column(Integer, ForeignKey("categories.id"))
    area_id = Column(Integer, ForeignKey("areas.id"))
    address = Column(String(255), default="")
    phone = Column(String(40), default="")
    description = Column(Text, default="")
    image = Column(String(255), default="")         # asset path / URL
    latitude = Column(Numeric(10, 6), nullable=True)
    longitude = Column(Numeric(10, 6), nullable=True)
    is_approved = Column(Boolean, default=False)    # vendor onboarding approval
    created_at = Column(DateTime, default=_now)

    vendor = relationship("User", back_populates="shops")
    branches = relationship("Branch", back_populates="shop")
    coupons = relationship("Coupon", back_populates="shop")


class Branch(Base):
    __tablename__ = "branches"
    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    name = Column(String(120), nullable=False)
    address = Column(String(255), default="")
    phone = Column(String(40), default="")
    latitude = Column(Numeric(10, 6), nullable=True)
    longitude = Column(Numeric(10, 6), nullable=True)

    shop = relationship("Shop", back_populates="branches")


class Coupon(Base):
    __tablename__ = "coupons"
    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    vendor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    # Cash | Discount | Gift | Price
    coupon_type = Column(String(20), default="Discount", nullable=False)
    coupon_name = Column(String(160), nullable=False)
    name_en = Column(String(160), default="")
    category_id = Column(Integer, ForeignKey("categories.id"))
    # Amount fields (only the ones matching coupon_type are used)
    cash_amount = Column(Numeric(10, 2), nullable=True)
    percentage = Column(Numeric(5, 2), nullable=True)      # e.g. 85 = 85折
    gift = Column(String(160), default="")
    discount_price = Column(Numeric(10, 2), nullable=True)
    regular_price = Column(Numeric(10, 2), nullable=True)
    min_spend = Column(Numeric(10, 2), default=0)          # 需消費金額
    image = Column(String(255), default="")
    valid_from = Column(DateTime, nullable=False)
    valid_to = Column(DateTime, nullable=False)
    total = Column(Integer, default=0)                      # total_download
    remaining = Column(Integer, default=0)
    downloads = Column(Integer, default=0)
    used_count = Column(Integer, default=0)
    # draft | pending_review | active | rejected | expired
    status = Column(String(20), default="draft", nullable=False)
    additional_terms = Column(Text, default="")
    # Line E (bargain) / line C (packages) shared fields, §11.2:
    # kind=coupon (free claim, unchanged) | package (bargain/package coupon).
    # package_id FK added by line C (packages.id); line E created the
    # columns without the FK, line C's migration adds person_count /
    # usage_rules / refund_policy and skips the 4 shared columns.
    is_limited_time = Column(Boolean, default=False)
    is_points_exchange = Column(Boolean, default=False)  # 9 積分兌換券（積分換券）
    created_at = Column(DateTime, default=_now)
    # --- line C (套餐 2A): package vouchers reuse the wallet states ---
    # kind: coupon (free) | package (paid set-meal voucher)
    kind = Column(String(20), default="coupon", nullable=False)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=True)
    person_count = Column(Integer, nullable=True)
    # usage rules JSON (times / booking / includes-excludes)
    usage_rules = Column(Text, default="")
    # refund policy: anytime | expiry_only | non_refundable
    refund_policy = Column(String(20), default="anytime", nullable=False)

    shop = relationship("Shop", back_populates="coupons")


class WalletItem(Base):
    """A coupon claimed into a customer's Q Wallet. status: 1 unused, 2 used, 3 expired."""
    __tablename__ = "wallet_items"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    coupon_id = Column(Integer, ForeignKey("coupons.id"), nullable=False)
    status = Column(Integer, default=1, nullable=False)
    code = Column(String(20), default="")                  # 6-digit redemption code
    qr_expires_at = Column(DateTime, nullable=True)
    claimed_at = Column(DateTime, default=_now)
    used_at = Column(DateTime, nullable=True)
    reviewed = Column(Boolean, default=False)
    # --- line C (套餐 2A) / line E (bargain) shared: voucher provenance ---
    # source: claim | purchase | bargain | exchange. order_id is line C's
    # orders.id (FK); line E created the columns without the FK.
    source = Column(String(20), default="claim", nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    user = relationship("User", back_populates="wallet_items")
    coupon = relationship("Coupon")


class Redemption(Base):
    __tablename__ = "redemptions"
    id = Column(Integer, primary_key=True)
    wallet_item_id = Column(Integer, ForeignKey("wallet_items.id"), nullable=False)
    coupon_id = Column(Integer, ForeignKey("coupons.id"), nullable=False)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    scanned_by_vendor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    scanned_at = Column(DateTime, default=_now)
    # F6: face value snapshot at scan time (Numeric, nullable). The
    # settlement amount for this redemption is frozen here, so a later
    # coupon price change cannot drift the settlement / reconciliation
    # basis. NULL = legacy redemption created before the snapshot
    # column existed; settlement_basis falls back to the live rows
    # for those rows only (migration 0014 backfills them).
    face_value = Column(Numeric(12, 2), nullable=True)
    # Line A (report §11.2): idempotency + prepare + revoke window.
    idempotency_key = Column(String(80), unique=True, nullable=True)
    prepared_at = Column(DateTime, nullable=True)   # pre-redeem check time
    revoked_at = Column(DateTime, nullable=True)    # revoke time (10-min window)
    revoked_by = Column(Integer, ForeignKey("users.id"), nullable=True)


class PointTransaction(Base):
    __tablename__ = "point_transactions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    delta = Column(Integer, nullable=False)                 # +10 / +20 / +15 / -9 ...
    reason = Column(String(60), nullable=False)            # claim | redeem | review | exchange
    ref_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=_now)

    user = relationship("User", back_populates="points")


class Favorite(Base):
    __tablename__ = "favorites"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    coupon_id = Column(Integer, ForeignKey("coupons.id"), nullable=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=True)
    created_at = Column(DateTime, default=_now)


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # null = broadcast
    title = Column(String(160), nullable=False)
    body = Column(Text, default="")
    notification_type = Column(String(40), default="general")
    target_coupon_id = Column(Integer, nullable=True)
    target_shop_id = Column(Integer, nullable=True)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_now)


class SearchHistory(Base):
    __tablename__ = "search_history"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    search_value = Column(String(120), nullable=False)
    created_at = Column(DateTime, default=_now)


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"
    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    name = Column(String(120), nullable=False)
    monthly_fee = Column(Numeric(10, 2), default=0)
    coupon_limit = Column(Integer, default=0)
    description = Column(Text, default="")
    is_active = Column(Boolean, default=True)


# Line B models (reviews / ratings, merchant applications, admin audit log)
# live in app/models_line_b.py; imported here so Base.metadata (used by
# create_all and Alembic) includes the new tables. v0.1 tables above
# are untouched.
from .models_line_b import (  # noqa: E402,F401
    AdminAuditLog, MerchantApplication, Review, ReviewHelpfulVote,
    ShopRating,
)

# Line D: register cashback tables on Base.metadata (import only; the
# models live in app/models_cashback.py so other lines' tables are untouched).
from . import models_cashback  # noqa: F401,E402
# --- line C (套餐 2A) models live in their own module; importing here
# registers them on Base.metadata so create_all / alembic see them. ---
from .models_packages import Order, Package, Payment, Refund  # noqa: E402,F401
# Line F models (settlements / reconciliation / invoices / CMS / shop
# members) live in app/models_line_f.py; same pattern as line B.
from .models_line_f import (  # noqa: E402,F401
    CmsAnnouncement, CmsBanner, Invoice, ReconciliationDiscrepancy,
    ReconciliationReport, Settlement, SettlementItem, ShopMember,
)