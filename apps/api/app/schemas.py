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
    min_spend: float = 0
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
    min_spend: float = 0
    image: str = ""
    valid_from: datetime
    valid_to: datetime
    total: int = 100
    additional_terms: str = ""
    is_limited_time: bool = False
    branch_ids: List[int] = []


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
    # Dianping §4.4: how the code reached the till — "scan" (QR shown and
    # scanned) or "manual" (the 6 digits typed in by hand). Recorded on
    # the Redemption row; the single consume chain does not branch on it.
    method: str = "scan"
    # Line A: idempotency key (repeat submit of the same scan = one
    # redemption) and optional prepare token from /new-scan-coupon/prepare.
    idempotency_key: Optional[str] = None
    prepare_token: Optional[str] = None


class PrepareIn(BaseModel):
    code: str


# ---------- dianping §4 redemption (lookup / supplementary / batch / force-revoke) ----------
class ScanBatchIn(BaseModel):
    """One batch scan: several presented codes, one transaction.

    idempotency_key is REQUIRED and is the batch key: item i is recorded
    under f"{idempotency_key}:{i}", so replaying the identical request
    returns the original redemptions (duplicate: true) instead of
    consuming again. prepare_tokens, when given, must line up with
    codes (same order, same length).
    """
    codes: List[str] = Field(min_length=1, max_length=20)
    prepare_tokens: Optional[List[str]] = None
    idempotency_key: str = Field(min_length=1, max_length=80)
    note: Optional[str] = Field(default=None, max_length=500)


class SupplementaryScanIn(BaseModel):
    """After-the-fact scan (§4.1): no presented code, no prepare token.

    The merchant found the item via the lookup endpoint and the customer
    has left / never presented a code. The consume chain is the same as
    scan_coupon; the redemption is marked method="supplementary" and an
    AdminAuditLog entry is written.
    """
    wallet_item_id: int
    idempotency_key: Optional[str] = Field(default=None, max_length=80)
    reason: Optional[str] = Field(default=None, max_length=500)
    note: Optional[str] = Field(default=None, max_length=500)


class ForceRevokeIn(BaseModel):
    """Over-window revoke (§4.2): admin / support only, reason mandatory.

    The reason is written to AdminAuditLog with the redemption id, so the
    override is reviewable after the fact.
    """
    reason: str = Field(min_length=1, max_length=500)


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
