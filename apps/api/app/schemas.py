"""Pydantic schemas for the /api/v3 contract."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


# ---------- auth ----------
class LegalVersionsIn(BaseModel):
    accepted_terms_version: Optional[str] = Field(default=None, max_length=64)
    accepted_privacy_version: Optional[str] = Field(default=None, max_length=64)


class RegisterIn(LegalVersionsIn):
    email: EmailStr
    password: str = Field(min_length=6)
    name: str = ""
    phone: str = ""
    role: str = "customer"  # customer | vendor


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class OtpVerifyIn(LegalVersionsIn):
    email: EmailStr
    code: str
    purpose: str = "register"  # register | forgot
    new_password: Optional[str] = None


class OtpRequestIn(BaseModel):
    email: EmailStr
    purpose: str = "register"


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    email: EmailStr
    code: str
    new_password: str = Field(min_length=6)


class SocialLoginIn(BaseModel):
    provider: str  # google | facebook | apple
    token: str
    role: str = "customer"


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    user_id: int
    role: str
    email: str


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    phone: str
    role: str
    locale: str
    limited_time_notify: bool = True

    class Config:
        from_attributes = True


class ProfileUpdateIn(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    locale: Optional[str] = None


class DeleteAccountIn(BaseModel):
    reason: str = ""


# ---------- coupons / shops ----------
class CouponOut(BaseModel):
    package_id: Optional[int] = None
    id: int
    coupon_id: int
    shop_id: int
    vendor_name: str = ""
    shop_name: str = ""
    coupon_name: str
    name_en: str = ""
    coupon_type: str
    cash_amount: Optional[float] = None
    percentage: Optional[float] = None
    gift: str = ""
    discount_price: Optional[float] = None
    regular_price: Optional[float] = None
    # §3.1 calibre (P1-A compatible): None = free; > 0 = paid.
    sale_price: Optional[float] = None
    is_paid: bool = False
    refund_policy: str = "anytime"
    min_spend: float = 0
    # §3.4: max claims per user (default 1).
    per_user_limit: int = 1
    image: str = ""
    valid_from: datetime
    valid_to: datetime
    remaining: int
    total: int
    downloads: int
    used_count: int
    status: str
    additional_terms: str = ""
    is_coupon_favorite: bool = False

    class Config:
        from_attributes = True


class ShopOut(BaseModel):
    id: int
    shop_id: int
    vendor_name: str = ""
    shop_name: str
    name_en: str = ""
    category_id: Optional[int] = None
    category_name: str = ""
    area_name: str = ""
    address: str = ""
    phone: str = ""
    description: str = ""
    image: str = ""
    branch_count: int = 0
    is_shop_favorite: bool = False

    class Config:
        from_attributes = True


class BranchOut(BaseModel):
    id: int
    branch_id: int
    shop_id: int
    branch_name: str
    address: str = ""
    phone: str = ""

    class Config:
        from_attributes = True


class CouponCreateIn(BaseModel):
    """Vendor issue-coupon form (step 2: create, after preview)."""
    shop_id: int
    coupon_type: str = "Discount"
    coupon_name: str
    name_en: str = ""
    category_id: Optional[int] = None
    cash_amount: Optional[float] = None
    percentage: Optional[float] = None
    gift: str = ""
    discount_price: Optional[float] = None
    regular_price: Optional[float] = None
    # §3.1 calibre (P1-A compatible): None / 0 = free; > 0 = paid.
    sale_price: Optional[float] = Field(default=None, ge=0)
    # §3.1 calibre: refund policy for paid coupons (same 3 values as
    # packages); free coupons keep the default.
    refund_policy: str = "anytime"
    min_spend: float = 0
    # §3.4: max claims per user; None = 1 (previous behaviour).
    per_user_limit: Optional[int] = Field(default=None, ge=1)
    image: str = ""
    valid_from: datetime
    valid_to: datetime
    total: int = 100
    additional_terms: str = ""
    is_limited_time: bool = False
    branch_ids: List[int] = []


class CouponUpdateIn(BaseModel):
    """Merchant edit of a coupon (§3.2). All fields optional; only the
    supplied ones change. Editing an active / paused / scheduled coupon
    sends it back to pending_review (re-review); editing a draft /
    rejected coupon leaves it a draft; editing a pending_review coupon
    keeps it pending_review. Expired / sold_out coupons cannot be
    edited — create a new one."""
    coupon_name: Optional[str] = None
    name_en: Optional[str] = None
    category_id: Optional[int] = None
    cash_amount: Optional[float] = None
    percentage: Optional[float] = None
    gift: Optional[str] = None
    discount_price: Optional[float] = None
    regular_price: Optional[float] = None
    sale_price: Optional[float] = Field(default=None, ge=0)
    refund_policy: Optional[str] = None
    min_spend: Optional[float] = None
    per_user_limit: Optional[int] = Field(default=None, ge=1)
    image: Optional[str] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    total: Optional[int] = Field(default=None, ge=0)
    additional_terms: Optional[str] = None
    is_limited_time: Optional[bool] = None


class CouponPreviewOut(BaseModel):
    coupon_name: str
    coupon_type: str
    amount_text: str
    valid_from: datetime
    valid_to: datetime
    total: int
    shop_name: str = ""


# ---------- wallet ----------
class WalletItemOut(BaseModel):
    id: int
    coupon_id: int
    coupon_name: str
    shop_name: str
    coupon_type: str
    status: int  # 1 unused, 2 used, 3 expired
    code: str = ""
    qr_expires_at: Optional[datetime] = None
    claimed_at: datetime
    used_at: Optional[datetime] = None
    reviewed: bool = False
    valid_to: datetime
    discount_price: Optional[float] = None
    regular_price: Optional[float] = None


class QrOut(BaseModel):
    wallet_item_id: int
    code: str
    qr_payload: str
    expires_in_seconds: int


class ScanIn(BaseModel):
    code: str
    # Line A: idempotency key (repeat submit of the same scan = one
    # redemption) and optional prepare token from /new-scan-coupon/prepare.
    idempotency_key: Optional[str] = None
    prepare_token: Optional[str] = None


class PrepareIn(BaseModel):
    code: str


class PointsOut(BaseModel):
    balance: int
    transactions: List[dict] = []


# ---------- misc ----------
class SuggestionOut(BaseModel):
    kind: str  # coupon | shop | branch
    id: int
    title: str
    subtitle: str = ""
    has_branch: int = 0


class NotificationOut(BaseModel):
    id: int
    title: str
    body: str = ""
    notification_type: str = "general"
    target_coupon_id: Optional[int] = None
    target_shop_id: Optional[int] = None
    is_read: bool = False
    created_at: datetime

    class Config:
        from_attributes = True
