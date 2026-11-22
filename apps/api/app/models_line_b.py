"""Line B models: reviews / ratings (F6 v1) and admin v1 (F4 v1).

Kept in a separate module from app.models so line B only adds new tables
and never edits the v0.1 tables. Imported by app.models (one line) so
Base.metadata.create_all() and Alembic see them.

Rules (FEATURE_RESEARCH_20261116.md §5.3 / §11, as corrected by M7):
- Only a redeemed user may review; the Redemption is the proof of
  consumption. One review per redemption (DB UNIQUE on
  reviews.redemption_id, model + migration 0002_line_b).
- Points and stars are DECOUPLED (M7): every visible review counts
  toward the shop rating (counts_toward_rating=True), whether or not
  it took the +15 review points. claim_points only decides whether
  points are granted; it never affects the rating. The pre-M7 split
  (points review -> counts_toward_rating=False) is removed; legacy
  rows were backfilled by migration 0015.
- images / tags / shop_photos are stored as JSON text. v1 has no file
  upload endpoint; images is a reserved field holding image URLs/paths
  supplied by the client (max 9), to be backed by real upload in v2.
"""
import json
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)

from .database import Base


from .timeutil import utcnow as _now


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
    # M7: always True for new reviews — points never affect the rating.
    # (Legacy False rows backfilled by migration 0015.)
    counts_toward_rating = Column(Boolean, default=True)
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


class ReviewHelpfulVote(Base):
    """Gap 9: one "helpful" vote per (review, user).

    A user votes at most once per review (unique constraint); voting
    again is a no-op and cancelling deletes the row. The count is
    derived from this table (no denormalised counter to drift).
    """
    __tablename__ = "review_helpful_votes"
    id = Column(Integer, primary_key=True)
    review_id = Column(Integer, ForeignKey("reviews.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=_now)

    __table_args__ = (
        UniqueConstraint("review_id", "user_id",
                         name="uq_review_helpful_vote"),
    )


class ShopRating(Base):
    """Pre-aggregated shop rating. All visible reviews feed it (M7).

    A row exists once a shop has any review; overall/taste/ambience/
    service are None and review_count is 0 while no visible review
    exists (e.g. every review so far is disputed or hidden).
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

    P1-D (Hong Kong fields): br_number / br_file_id / ci_file_id /
    director_id_file_id / shop_photo_file_ids replace the mainland
    fields as the approval basis. The mainland columns
    (business_license, id_card, industry_license) and the legacy
    shop_photos JSON list are RETAINED verbatim for back-compat and
    audit: old rows keep their values, old clients may still submit
    them, but they no longer gate approval (see migration 0022).

    Approval gates (enforced in routers/admin.py review_application):
    (1) the applicant signed the CURRENT contract version;
    (2) Stripe onboarding is complete (stripe_account_id set and
    charges + payouts enabled — written by P1-B, or by admin via the
    stripe-onboarding endpoint until P1-B lands). We never store bank
    account numbers: Stripe collects identity + bank details.
    """
    __tablename__ = "merchant_applications"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    shop_name = Column(String(120), nullable=False)
    contact_name = Column(String(120), default="")  # 聯絡人
    phone = Column(String(40), default="")
    address = Column(String(255), default="")
    business_license = Column(Text, default="")   # LEGACY 內地口徑：營業執照
    id_card = Column(Text, default="")            # LEGACY 內地口徑：法人身份證
    shop_photos = Column(Text, default="[]")      # LEGACY JSON list, 門店照片
    industry_license = Column(Text, default="")   # LEGACY 行業許可證
    # --- P1-D Hong Kong fields ---
    br_number = Column(String(20), default="")   # 商業登記號碼（8 位數字）
    # File pointers: plain integers, deliberately NOT ForeignKeys.
    # MerchantFile.application_id already points back at applications,
    # so FKs here would form a cycle (merchant_applications <->
    # merchant_files) that breaks Base.metadata.drop_all on Postgres,
    # and use_alter=True needs named constraints to be droppable.
    # Referential checks live in the API layer instead
    # (routers/admin._resolve_application_file validates existence,
    # ownership and kind on submit).
    br_file_id = Column(Integer, nullable=True)            # BR 文件
    ci_file_id = Column(Integer, nullable=True)            # 公司註冊證書（CI）文件
    director_id_file_id = Column(Integer, nullable=True)   # 董事身份證明文件
    shop_photo_file_ids = Column(Text, default="[]")  # JSON list[int], 門店照片
    # --- P1-D Stripe onboarding (P1-B writes these; admin bridge until then) ---
    stripe_account_id = Column(String(120), default="")
    stripe_charges_enabled = Column(Boolean, default=False, nullable=False)
    stripe_payouts_enabled = Column(Boolean, default=False, nullable=False)
    stripe_onboarding_updated_at = Column(DateTime, nullable=True)
    # Set only during approval; never infer ownership from the account ID.
    approved_shop_id = Column(Integer, nullable=True)
    # --- P1-D e-contract (denormalised pointer to the latest signature) ---
    contract_version_id = Column(
        Integer, ForeignKey("merchant_contract_versions.id"), nullable=True)
    contract_signed_at = Column(DateTime, nullable=True)
    contract_signed_ip = Column(String(45), default="")
    status = Column(String(20), default="pending", nullable=False)
    reject_reason = Column(Text, default="")
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    @property
    def shop_photos_list(self):
        return _json_list(self.shop_photos)

    @property
    def shop_photo_file_ids_list(self):
        ids = _json_list(self.shop_photo_file_ids)
        return [i for i in ids if isinstance(i, int)]


class MerchantFile(Base):
    """A privately stored merchant-onboarding file (P1-D).

    Never served from the public /uploads mount: only the applicant
    who uploaded it (or the applicant it belongs to) and admin may
    read it, via a short-lived HMAC-signed download link.
    stored_name is a server-generated UUID filename under
    <MERCHANT_FILE_DIR>/; the original filename is kept for the
    download Content-Disposition only.
    kind: br | ci | director_id | shop_photo | contract_pdf.
    application_id is NULL while a file is uploaded before its
    application is submitted (claimed at submit time).
    """
    __tablename__ = "merchant_files"
    id = Column(Integer, primary_key=True)
    application_id = Column(Integer, ForeignKey("merchant_applications.id"),
                            nullable=True, index=True)
    uploader_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    kind = Column(String(20), nullable=False)
    stored_name = Column(String(120), nullable=False)
    original_filename = Column(String(255), default="")
    content_type = Column(String(120), default="")
    size_bytes = Column(Integer, default=0, nullable=False)
    sha256 = Column(String(64), default="")
    created_at = Column(DateTime, default=_now)


class MerchantContractVersion(Base):
    """A versioned merchant contract (P1-D e-contract).

    Exactly one row has is_current=True. Publishing a new version
    flips the flag; old signatures stay on their version for audit.
    The v1 body is a placeholder marked 待法務確認 (migration 0022
    and seed insert it); legal finalises the text, then admin
    publishes the real version and merchants re-sign.
    """
    __tablename__ = "merchant_contract_versions"
    id = Column(Integer, primary_key=True)
    version = Column(String(20), nullable=False, unique=True)
    title = Column(String(200), default="")
    body_text = Column(Text, default="")
    is_current = Column(Boolean, default=False, nullable=False)
    published_at = Column(DateTime, default=_now)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)


class MerchantContractSignature(Base):
    """One merchant's click-sign of one contract version (P1-D).

    Records who signed which version, when, and from which IP, plus
    the archived PDF (a merchant_files row, kind=contract_pdf).
    One signature per (application, version): re-signing the same
    version returns the existing row instead of duplicating it.
    """
    __tablename__ = "merchant_contract_signatures"
    id = Column(Integer, primary_key=True)
    application_id = Column(Integer, ForeignKey("merchant_applications.id"),
                            nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    contract_version_id = Column(
        Integer, ForeignKey("merchant_contract_versions.id"), nullable=False)
    signed_at = Column(DateTime, default=_now)
    ip_address = Column(String(45), default="")
    pdf_file_id = Column(Integer, ForeignKey("merchant_files.id"),
                         nullable=True)

    __table_args__ = (
        UniqueConstraint("application_id", "contract_version_id",
                         name="uq_contract_signature_app_version"),
    )


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
