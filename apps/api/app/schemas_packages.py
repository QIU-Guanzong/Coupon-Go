"""Pydantic schemas for line C (套餐 2A) — packages / orders / payments.

Paths follow docs/FEATURE_RESEARCH_20261116.md §12 (套餐）.
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class PackageItemIn(BaseModel):
    name: str
    qty: int = 1


class UsageRulesIn(BaseModel):
    weekday_only: bool = False
    holiday: bool = True
    need_booking: bool = False
    includes: List[str] = []
    excludes: List[str] = []


class PackageCreateIn(BaseModel):
    """Vendor/admin creates a package product (套餐商品）. The coupon
    template (kind='package') is auto-created by the endpoint."""
    shop_id: int
    name: str
    name_en: str = ""
    items: List[PackageItemIn] = []
    person_count: int = Field(default=2, ge=1)
    original_price: float = Field(gt=0)
    package_price: float = Field(gt=0)
    valid_from: datetime
    valid_to: datetime
    usage_rules: UsageRulesIn = UsageRulesIn()
    stock_total: int = Field(default=0, ge=0)
    refund_policy: str = "anytime"  # anytime | expiry_only | non_refundable


class PackageOut(BaseModel):
    id: int
    package_id: int
    shop_id: int
    shop_name: str = ""
    name: str
    name_en: str = ""
    items: List[dict] = []
    person_count: int
    original_price: float
    package_price: float
    save_amount: float = 0
    save_percent: float = 0
    valid_from: datetime
    valid_to: datetime
    usage_rules: dict = {}
    stock_total: int
    stock_left: int
    sold_count: int = 0
    refund_policy: str
    refund_policy_text: str = ""
    status: str

    class Config:
        from_attributes = True


class PurchaseIn(BaseModel):
    payment_method: str = "fps"  # fps | card
    client_ref: str = ""  # 客户端幂等键：同一键重复下单返回原订单


class PaymentOut(BaseModel):
    id: int
    order_id: int
    gateway: str
    gateway_ref: str
    method: str
    amount: float
    currency: str
    status: str
    failure_message: str = ""
    created_at: datetime

    class Config:
        from_attributes = True


class OrderOut(BaseModel):
    id: int
    order_id: int
    user_id: int
    order_type: str
    package_id: Optional[int] = None
    package_name: str = ""
    shop_name: str = ""
    amount: float
    paid_amount: float
    payment_method: str = ""
    payment_status: str
    refund_status: str
    settlement_status: str
    commission_amount: float = 0
    net_amount: float = 0
    wallet_item_id: Optional[int] = None
    created_at: datetime
    paid_at: Optional[datetime] = None
    payments: List[PaymentOut] = []

    class Config:
        from_attributes = True


class PurchaseOut(BaseModel):
    """Purchase response: order + the payment attempt the App must
    complete (Stripe test mode: client_secret + method)."""
    order: OrderOut
    payment: PaymentOut
    client_secret: str = ""
    gateway: str = "stripe"
    test_mode: bool = True
    message: str = ""


class ConfirmPaymentIn(BaseModel):
    """Test-mode payment result (stands in for the Stripe webhook until
    a real test key + webhook secret exist)."""
    payment_id: Optional[int] = None
    outcome: str  # succeeded | failed | timeout
    failure_message: str = ""


class RetryPaymentIn(BaseModel):
    payment_method: Optional[str] = None  # fps | card; default: original


class RefundIn(BaseModel):
    reason: str = ""


class RefundOut(BaseModel):
    id: int
    refund_id: int
    order_id: int
    amount: float
    status: str
    gateway_ref: str = ""
    expected_arrival: str = "原路返回，預計 1–3 個工作日到賬"
    created_at: datetime

    class Config:
        from_attributes = True


class WebhookIn(BaseModel):
    """Minimal Stripe test-mode webhook payload."""
    type: str
    data: dict = {}
