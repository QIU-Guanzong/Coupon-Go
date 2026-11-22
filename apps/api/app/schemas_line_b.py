"""Line B schemas: reviews (F6 v1) and admin v1 (F4 v1)."""
import re
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


# ---------- reviews ----------
class ReviewCreateIn(BaseModel):
    """Write a review. redemption_id is the consumption proof; if it is
    omitted, wallet_item_id is used and its latest redemption is taken.

    claim_points (default True): take the +15 review points. A review
    that takes points does NOT count toward the shop rating (anti
    incentivised-review rule). Set claim_points=False to forgo the
    points; that review counts toward the rating.
    """
    wallet_item_id: Optional[int] = None
    redemption_id: Optional[int] = None
    taste: int = Field(ge=1, le=5)
    ambience: int = Field(ge=1, le=5)
    service: int = Field(ge=1, le=5)
    content: str = Field(default="", max_length=1000)
    images: List[str] = Field(default_factory=list, max_length=9)
    tags: List[str] = Field(default_factory=list, max_length=10)
    claim_points: bool = True


class ReviewOut(BaseModel):
    id: int
    user_id: int
    user_name: str = ""
    shop_id: int
    redemption_id: int
    wallet_item_id: int
    taste: int
    ambience: int
    service: int
    overall: float
    content: str = ""
    images: List[str] = []
    tags: List[str] = []
    points_given: bool = False
    counts_toward_rating: bool = False
    # Gap 9: server-side helpful votes + whether the caller voted.
    helpful_count: int = 0
    helped_by_me: bool = False
    status: str
    reply_text: str = ""
    replied_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class HelpfulVoteIn(BaseModel):
    """Gap 9: helpful=True votes (idempotent, one per user);
    helpful=False cancels the caller's vote."""
    helpful: bool = True


class ReviewReplyIn(BaseModel):
    reply: str = Field(max_length=500)


class ReviewDisputeIn(BaseModel):
    reason: str = Field(default="", max_length=500)


class ShopRatingOut(BaseModel):
    shop_id: int
    overall: Optional[float] = None
    taste: Optional[float] = None
    ambience: Optional[float] = None
    service: Optional[float] = None
    review_count: int = 0
    updated_at: Optional[datetime] = None


# ---------- merchant applications / admin ----------
class MerchantApplicationCreateIn(BaseModel):
    shop_name: str = Field(min_length=1, max_length=120)
    contact_name: str = ""   # 聯絡人
    phone: str = ""
    address: str = ""
    # P1-D Hong Kong fields. br_number: 8 digits when given.
    br_number: str = ""
    br_file_id: Optional[int] = None
    ci_file_id: Optional[int] = None
    director_id_file_id: Optional[int] = None
    shop_photo_file_ids: List[int] = Field(default_factory=list, max_length=10)
    # LEGACY mainland fields: still accepted + stored, no longer gate approval.
    business_license: str = ""   # 營業執照（舊口徑，僅追溯）
    id_card: str = ""            # 法人身份證（舊口徑，僅追溯）
    shop_photos: List[str] = Field(default_factory=list, max_length=10)
    industry_license: str = ""   # 行業許可證（舊口徑，僅追溯）

    @field_validator("br_number")
    @classmethod
    def _br_number_must_be_8_digits(cls, v: str) -> str:
        digits = re.sub(r"[\s\-]", "", v or "")
        if digits and not re.fullmatch(r"\d{8}", digits):
            raise ValueError("商業登記號碼須為 8 位數字")
        return digits


class MerchantFileOut(BaseModel):
    id: int
    application_id: Optional[int] = None
    kind: str
    original_filename: str = ""
    content_type: str = ""
    size_bytes: int = 0
    sha256: str = ""
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MerchantContractInfo(BaseModel):
    version: str
    signed_at: Optional[datetime] = None
    signed_ip: str = ""
    pdf_file_id: Optional[int] = None


class MerchantApplicationOut(BaseModel):
    id: int
    user_id: int
    applicant_email: str = ""
    shop_name: str
    contact_name: str = ""
    phone: str = ""
    address: str = ""
    business_license: str = ""
    id_card: str = ""
    shop_photos: List[str] = []
    industry_license: str = ""
    # P1-D
    br_number: str = ""
    documents: dict = {}
    shop_photo_files: List[MerchantFileOut] = []
    stripe_account_id: str = ""
    stripe_charges_enabled: bool = False
    stripe_payouts_enabled: bool = False
    contract: Optional[MerchantContractInfo] = None
    status: str
    reject_reason: str = ""
    reviewed_by: Optional[int] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class MerchantApplicationReviewIn(BaseModel):
    approve: bool
    reject_reason: str = ""


class StripeOnboardingIn(BaseModel):
    """P1-B bridge: record a merchant's Stripe Connect onboarding result.

    Until P1-B lands, admin fills this in from the Stripe Dashboard;
    after P1-B lands, P1-B's onboarding callback writes the same three
    columns directly. We never store bank account numbers.
    """
    stripe_account_id: str = Field(min_length=1, max_length=120)
    charges_enabled: bool = False
    payouts_enabled: bool = False


class MerchantContractVersionCreateIn(BaseModel):
    version: str = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=200)
    body_text: str = Field(min_length=1)


class MerchantContractVersionOut(BaseModel):
    id: int
    version: str
    title: str
    body_text: str = ""
    is_current: bool = False
    published_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MerchantContractSignIn(BaseModel):
    contract_version_id: Optional[int] = None  # omit = sign current version


class MerchantContractSignatureOut(BaseModel):
    id: int
    application_id: int
    user_id: int
    contract_version: str
    signed_at: Optional[datetime] = None
    ip_address: str = ""
    pdf_file_id: Optional[int] = None

    class Config:
        from_attributes = True


class AdminAuditLogOut(BaseModel):
    id: int
    admin_id: int
    action: str
    target_type: str
    target_id: Optional[int] = None
    detail: str = ""
    created_at: datetime

    class Config:
        from_attributes = True
