"""Line B models: reviews / ratings (F6 v1) and admin v1 (F4 v1).

Kept in a separate module from app.models so line B only adds new tables
and never edits the v0.1 tables. Imported by app.models (one line) so
Base.metadata.create_all() and Alembic see them.

Rules (FEATURE_RESEARCH_20261116.md §5.3 / §11):
- Only a redeemed user may review; the Redemption is the proof of
  consumption. One review per redemption.
- Points and stars are split: a review that takes the +15 review points
  (points_given=True) does NOT count toward the shop rating
  (counts_toward_rating=False) — incentivised reviews must not move stars.
  A reviewer may opt out of the points (claim_points=False); that review
  counts toward the rating. ShopRating aggregates counted, visible
  reviews only.
- images / tags / shop_photos are stored as JSON text. v1 has no file
  upload endpoint; images is a reserved field holding image URLs/paths
  supplied by the client (max 9), to be backed by real upload in v2.
"""
import json
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text,
)

from .database import Base


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_list(value):
    """Decode a JSON-text list column; never raise on bad data."""
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


class Review(Base):
    """A three-dimension (taste / ambience / service) 1-5 review.

    status: visible | disputed | hidden. Disputed reviews are excluded
    from the rating aggregate until resolved (no resolve endpoint in v1;
    F line / v2). reply_text is the merchant's public reply.
    """
    __tablename__ = "reviews"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    redemption_id = Column(Integer, ForeignKey("redemptions.id"),
                           nullable=False, unique=True)
    wallet_item_id = Column(Integer, ForeignKey("wallet_items.id"),
                            nullable=False)
    taste = Column(Integer, nullable=False)       # 口味 1-5
    ambience = Column(Integer, nullable=False)    # 環境 1-5
    service = Column(Integer, nullable=False)     # 服務 1-5
    overall = Column(Float, nullable=False)       # mean of the three dims
    content = Column(Text, default="")
    images = Column(Text, default="[]")           # JSON list, reserved (v1: URLs)
    tags = Column(Text, default="[]")             # JSON list, e.g. 推薦菜
    points_given = Column(Boolean, default=False)
    counts_toward_rating = Column(Boolean, default=False)
    status = Column(String(20), default="visible", nullable=False)
    reply_text = Column(Text, default="")
    replied_at = Column(DateTime, nullable=True)
    replied_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    dispute_reason = Column(Text, default="")
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    @property
    def images_list(self):
        return _json_list(self.images)

    @property
    def tags_list(self):
        return _json_list(self.tags)


class ShopRating(Base):
    """Pre-aggregated shop rating. Only counted, visible reviews feed it.

    A row exists once a shop has any review; overall/taste/ambience/
    service are None and review_count is 0 while no review counts
    toward the rating (e.g. every review so far took points).
    """
    __tablename__ = "shop_ratings"
    shop_id = Column(Integer, ForeignKey("shops.id"), primary_key=True)
    overall = Column(Float, nullable=True)
    taste = Column(Float, nullable=True)
    ambience = Column(Float, nullable=True)
    service = Column(Float, nullable=True)
    review_count = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class MerchantApplication(Base):
    """Merchant onboarding application (F4-1).

    status: pending | approved | rejected. A rejection may be resubmitted
    as a NEW application (v1 has no edit-in-place); approving sets the
    applicant's role to vendor when they are currently a customer.
    Document fields are text references in v1 (licence numbers / file
    paths); no file upload endpoint yet.
    """
    __tablename__ = "merchant_applications"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    shop_name = Column(String(120), nullable=False)
    contact_name = Column(String(120), default="")
    phone = Column(String(40), default="")
    address = Column(String(255), default="")
    business_license = Column(Text, default="")   # 營業執照
    id_card = Column(Text, default="")            # 法人身份證
    shop_photos = Column(Text, default="[]")      # JSON list, 門店照片
    industry_license = Column(Text, default="")   # 行業許可證（如食品經營許可證）
    status = Column(String(20), default="pending", nullable=False)
    reject_reason = Column(Text, default="")
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    @property
    def shop_photos_list(self):
        return _json_list(self.shop_photos)


class AdminAuditLog(Base):
    """Append-only log of admin actions (v1: merchant-application reviews).

    F line (admin v2) extends this to settlements / banners / role changes.
    """
    __tablename__ = "admin_audit_logs"
    id = Column(Integer, primary_key=True)
    admin_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String(60), nullable=False)      # e.g. merchant_application.approve
    target_type = Column(String(40), nullable=False)  # e.g. merchant_application
    target_id = Column(Integer, nullable=True)
    detail = Column(Text, default="")
    created_at = Column(DateTime, default=_now)
