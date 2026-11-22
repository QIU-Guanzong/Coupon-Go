"""Line F permissions: role-based access for admin v2.

v1 contract (line B) is untouched: the v1 admin endpoints stay
require_role("admin"), and existing admin accounts keep working — admin
is granted every v2 permission below, so the v1 single-admin setup
upgrades with no account or endpoint change.

Roles (users.role):
- admin: everything (all permissions, plus the v1 endpoints).
- finance: settlements + reconciliation + invoices (read/write).
- auditor: read-only dashboard data + review-dispute resolution.
- support: review-dispute resolution + read-only dashboard data.
- merchant-admin: shop staff; powers are scoped per shop through the
  shop_members table (see require_shop_access), not through platform
  permissions.
- vendor: shop owner; same shop-scoped powers as merchant-admin for the
  shops they own.
- customer: no admin powers.
"""
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from . import models
from .database import get_db
from .deps import get_current_user
from .models_line_f import ShopMember

ALL_ROLES = [
    "customer", "vendor", "merchant-admin", "finance", "auditor",
    "support", "admin",
]

# permission -> roles allowed (admin is always allowed on top).
PERMISSIONS = {
    "dashboard.read": {"finance", "auditor", "support"},
    "withdrawals.review": {"finance"},
    "settlements.read": {"finance", "auditor"},
    "settlements.write": {"finance"},
    "reconciliation.read": {"finance", "auditor"},
    "reconciliation.write": {"finance"},
    "invoices.read": {"finance", "auditor"},
    "invoices.write": {"finance"},
    "content.read": {"finance", "auditor", "support"},
    "content.write": set(),          # admin only
    "users.read": {"finance", "auditor", "support"},
    "roles.write": set(),            # admin only
    "members.read": {"finance", "auditor"},
    "members.write": set(),          # admin only (+ shop owner, see below)
    "commission_rates.read": {"finance", "auditor"},
    "commission_rates.write": {"finance"},
    "reviews.dispute.resolve": {"auditor", "support"},
    "reviews.disputes.read": {"auditor", "support"},
}


def has_perm(user: models.User, *perms: str) -> bool:
    if user.role == "admin":
        return True
    allowed = set()
    for p in perms:
        allowed |= PERMISSIONS.get(p, set())
    return user.role in allowed


def require_perm(*perms: str):
    def _check(user: models.User = Depends(get_current_user)):
        if not has_perm(user, *perms):
            raise HTTPException(status_code=403, detail="沒有權限")
        return user
    return _check


def get_shop_or_404(db: Session, shop_id: int) -> models.Shop:
    shop = db.query(models.Shop).filter(models.Shop.id == shop_id).first()
    if shop is None:
        raise HTTPException(status_code=404, detail="找不到此商户")
    return shop


def can_access_shop(db: Session, user: models.User, shop: models.Shop) -> bool:
    """Platform staff with read perms, the shop owner, or an ACTIVE
    staff member. A pending invite (F8) confers no access until the
    invitee confirms it."""
    if user.role == "admin":
        return True
    if user.role in ("finance", "auditor"):
        return True
    if shop.vendor_id == user.id:
        return True
    member = (db.query(ShopMember)
              .filter(ShopMember.shop_id == shop.id,
                      ShopMember.user_id == user.id,
                      ShopMember.status == "active").first())
    return member is not None


def can_request_invoice_for_shop(db: Session, user: models.User,
                                 shop: models.Shop) -> bool:
    """Invoice-request fallback for callers WITHOUT invoices.write.

    Only the shop owner or an ACTIVE shop_members member may request an
    invoice for a shop. Unlike can_access_shop, platform roles get NO blanket
    access here: auditor/finance/support must hold invoices.write
    (finance does; auditor/support do not), otherwise an auditor could
    POST an invoice for any shop. Used only by POST /admin/invoices.
    """
    if shop.vendor_id == user.id:
        return True
    member = (db.query(ShopMember)
              .filter(ShopMember.shop_id == shop.id,
                      ShopMember.user_id == user.id,
                      ShopMember.status == "active").first())
    return member is not None


def require_shop_access(shop_id: int):
    """Dependency: load the shop and check the caller may access it."""
    def _check(user: models.User = Depends(get_current_user),
               db: Session = Depends(get_db)):
        shop = get_shop_or_404(db, shop_id)
        if not can_access_shop(db, user, shop):
            raise HTTPException(status_code=403, detail="沒有權限查看此商户")
        return shop
    return _check


def role_permissions() -> dict:
    """Public view of the role -> permission map (for the admin UI)."""
    out = {}
    for role in ALL_ROLES:
        if role == "admin":
            out[role] = ["*"]
        else:
            out[role] = sorted(p for p, roles in PERMISSIONS.items()
                               if role in roles)
    return out


# ---------- shop staff roles (dianping §1.3) ----------
# ShopMember.role values and what each may do INSIDE one shop. The shop
# owner (vendor) and platform admin hold every shop permission; the
# legacy "merchant-admin" staff role keeps full staff powers so existing
# memberships do not lose access when the finer roles land.
# - manager: redeem + manage coupons + view redemption records.
# - cashier: redeem only (+ view redemption records for handover).
# - finance: read settlements / invoices only, never redeem.
SHOP_MEMBER_ROLES = ["merchant-admin", "manager", "cashier", "finance"]

SHOP_ROLE_PERMISSIONS = {
    "merchant-admin": {"redeem", "manage_coupons", "view_settlement",
                       "view_redemptions", "manage_members"},
    "manager": {"redeem", "manage_coupons", "view_redemptions"},
    "cashier": {"redeem", "view_redemptions"},
    "finance": {"view_settlement"},
}

ALL_SHOP_PERMISSIONS = {"redeem", "manage_coupons", "view_settlement",
                        "view_redemptions", "manage_members"}


def shop_member_role(db: Session, user: models.User,
                     shop: models.Shop):
    """Active ShopMember.role for this user+shop, or None.

    A pending invite confers nothing until the invitee confirms it."""
    member = (db.query(ShopMember)
              .filter(ShopMember.shop_id == shop.id,
                      ShopMember.user_id == user.id,
                      ShopMember.status == "active").first())
    return member.role if member is not None else None


def shop_permissions(db: Session, user: models.User,
                     shop: models.Shop) -> set:
    """Shop-scoped permission set for one user on one shop."""
    if user.role == "admin":
        return set(ALL_SHOP_PERMISSIONS)
    if shop.vendor_id == user.id:
        return set(ALL_SHOP_PERMISSIONS)
    if user.role in ("finance", "auditor"):
        # Platform read scope (same blanket as can_access_shop): they may
        # read settlement data for any shop, nothing else shop-scoped.
        return {"view_settlement"}
    role = shop_member_role(db, user, shop)
    if role is None:
        return set()
    return set(SHOP_ROLE_PERMISSIONS.get(role, set()))


def has_shop_perm(db: Session, user: models.User, shop: models.Shop,
                  perm: str) -> bool:
    return perm in shop_permissions(db, user, shop)


def can_redeem_shop(db: Session, user: models.User,
                    shop: models.Shop) -> bool:
    return has_shop_perm(db, user, shop, "redeem")


def can_view_settlement_shop(db: Session, user: models.User,
                             shop: models.Shop) -> bool:
    return has_shop_perm(db, user, shop, "view_settlement")


def can_manage_coupons_shop(db: Session, user: models.User,
                            shop: models.Shop) -> bool:
    return has_shop_perm(db, user, shop, "manage_coupons")


def shop_role_permissions_out(role: str) -> list:
    """Sorted permission list for a ShopMember.role (API output)."""
    return sorted(SHOP_ROLE_PERMISSIONS.get(role, set()))
