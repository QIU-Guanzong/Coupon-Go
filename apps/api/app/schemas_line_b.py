"""Line B schemas: reviews (F6 v1) and admin v1 (F4 v1)."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


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
    contact_name: str = ""
    phone: str = ""
    address: str = ""
    business_license: str = ""   # 營業執照（文字憑證，v1 無上傳）
    id_card: str = ""            # 法人身份證（文字憑證，v1 無上傳）
    shop_photos: List[str] = Field(default_factory=list, max_length=10)
    industry_license: str = ""   # 行業許可證（如食品經營許可證）


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
