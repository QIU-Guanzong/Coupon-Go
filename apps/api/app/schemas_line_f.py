"""Line F schemas: admin v2 (settlements / reconciliation / invoices /
CMS / roles / shop members) and review-dispute resolution."""
from datetime import datetime
from typing import List, Optional

from decimal import Decimal
from pydantic import BaseModel, Field


# ---------- settlements ----------
class SettlementGenerateIn(BaseModel):
    shop_id: int
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")  # YYYY-MM
    # F5: retained for compatibility only. None = not passed. When
    # passed it must equal the effective rate (the shop's configured
    # rate, else the 10% default) or generation is rejected with 400;
    # it is never adopted directly.
    commission_rate: Optional[float] = Field(default=None, ge=0, le=1)


class CommissionRateSetIn(BaseModel):
    shop_id: int
    rate: float = Field(ge=0, le=1)


class SettlementDisputeIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class SettlementResolveIn(BaseModel):
    decision: str = Field(pattern=r"^(approve|reopen)$")
    note: str = Field(default="", max_length=500)


# ---------- reconciliation ----------
class ReconciliationRunIn(BaseModel):
    shop_id: int
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")


class DiscrepancyResolveIn(BaseModel):
    resolution: str = Field(min_length=1, max_length=500)


# ---------- invoices ----------
class InvoiceCreateIn(BaseModel):
    shop_id: int
    settlement_id: Optional[int] = None
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    title: str = Field(min_length=1, max_length=160)
    tax_id: str = Field(default="", max_length=60)
    invoice_type: str = Field(default="receipt", max_length=20)


class InvoiceCreditIn(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    reference: str = Field(min_length=1, max_length=120)


class InvoiceRejectIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


# ---------- CMS ----------
class BannerIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    image: str = Field(default="", max_length=255)
    link_type: str = Field(default="none", max_length=20)
    link_target: str = Field(default="", max_length=255)
    sort_order: int = 0
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    status: str = Field(default="draft", pattern=r"^(draft|active|archived)$")


class AnnouncementIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(default="")
    status: str = Field(default="draft", pattern=r"^(draft|active|archived)$")
    sort_order: int = 0
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    name_en: str = Field(default="", max_length=60)
    icon: str = Field(default="", max_length=120)


# ---------- roles / members ----------
class RoleChangeIn(BaseModel):
    role: str


class ShopMemberAddIn(BaseModel):
    user_id: Optional[int] = None
    email: Optional[str] = None
    # Dianping §1.3: staff role for this shop; defaults to the legacy
    # full-power merchant-admin. Validated against SHOP_MEMBER_ROLES.
    role: str = "merchant-admin"


class ShopMemberRoleIn(BaseModel):
    """Dianping §1.3: owner/admin changes a member's shop role."""
    role: str


# ---------- review dispute resolution ----------
class ReviewResolveIn(BaseModel):
    decision: str = Field(pattern=r"^(uphold|dismiss)$")
    note: str = Field(default="", max_length=500)


class SettlementTransferIn(BaseModel):
    amount: Decimal = Field(max_digits=12, decimal_places=2)
    reference: str = Field(min_length=1, max_length=120)
