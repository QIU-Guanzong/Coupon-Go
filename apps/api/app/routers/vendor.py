"""Vendor (merchant) routes: issue coupon (two-step preview -> create),
vendor wallet / records, branches, profile, subscription plans, approval,
and the Dianping listing lifecycle (§3.2/§3.3): coupon edit / pause /
resume / unlist / submit, bulk create, templates.

Dianping §1 (merchant management):
- §1.2: POST /merchant/shops + POST /merchant/shops/{id}/branches create
  shops/branches; POST /assign-branches really persists the coupon-branch
  links (coupon_branches; no rows = all branches of the shop).
- §1.3: coupon create/preview/assign and branch/profile edits accept the
  shop owner, admin, or an active shop member with manage_coupons
  (manager / legacy merchant-admin) — cashier / finance members cannot.
- §1.4: PATCH /merchant/shops/{id} is the merchant self-maintenance
  endpoint (hours / gallery / announcement / contact).
"""
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ValidationError
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import listing, models, schemas
from ..database import get_db
from ..deps import get_current_user, require_role
from ..models_packages import Order, REFUND_POLICIES
from ..notify import notify
from ..permissions_line_f import (
    can_access_shop, has_shop_perm,
)
from ..serializers import amount_text, coupon_out, shop_out
from ..shared import vendor_shop_ids

router = APIRouter()

BULK_MAX_COUPONS = 20

COUPON_TEMPLATES = [
    {
        "key": "manjian",
        "name": "滿減券",
        "coupon_type": "Cash",
        "cash_amount": 50,
        "percentage": None,
        "gift": "",
        "min_spend": 100,
        "discount_price": None,
        "regular_price": None,
        "sale_price": None,
        "per_user_limit": 1,
        "refund_policy": "anytime",
        "additional_terms": "滿 HK$100 可用，每人限領 1 張；不可與其他優惠同時使用。",
    },
    {
        "key": "zhekou",
        "name": "折扣券",
        "coupon_type": "Discount",
        "cash_amount": None,
        "percentage": 90,
        "gift": "",
        "min_spend": 0,
        "discount_price": None,
        "regular_price": None,
        "sale_price": None,
        "per_user_limit": 1,
        "refund_policy": "anytime",
        "additional_terms": "全單 9 折，每人限領 1 張；不可與其他優惠同時使用。",
    },
    {
        "key": "maizeng",
        "name": "買贈券",
        "coupon_type": "Gift",
        "cash_amount": None,
        "percentage": None,
        "gift": "飲品一杯",
        "min_spend": 0,
        "discount_price": None,
        "regular_price": None,
        "sale_price": None,
        "per_user_limit": 1,
        "refund_policy": "anytime",
        "additional_terms": "消費即贈飲品一杯，每人限領 1 張；數量有限，送完即止。",
    },
]


from ..timeutil import utcnow as _now


def _check_coupon_shop(db: Session, vendor: models.User, shop_id: int):
    shop = db.query(models.Shop).filter(
        models.Shop.id == shop_id,
        models.Shop.vendor_id == vendor.id).first()
    if shop is None:
        raise HTTPException(status_code=404, detail="找不到此商店")
    return shop


def _validate_coupon_dates_total(body):
    if body.valid_to <= body.valid_from:
        raise HTTPException(status_code=400, detail="到期日期必須晚於生效日期")
    if body.total <= 0:
        raise HTTPException(status_code=400, detail="請輸入優惠券數量")
    if body.refund_policy not in listing.REFUND_POLICIES:
        raise HTTPException(status_code=400, detail="refund_policy 不正確")


def _build_coupon(vendor: models.User, body: schemas.CouponCreateIn):
    return models.Coupon(
        shop_id=body.shop_id, vendor_id=vendor.id,
        coupon_type=body.coupon_type, coupon_name=body.coupon_name,
        name_en=body.name_en, category_id=body.category_id,
        cash_amount=body.cash_amount, percentage=body.percentage,
        gift=body.gift, discount_price=body.discount_price,
        regular_price=body.regular_price, min_spend=body.min_spend,
        # §3.1 calibre (P1-A compatible): None / 0 = free, > 0 = paid.
        sale_price=body.sale_price, refund_policy=body.refund_policy,
        per_user_limit=body.per_user_limit or 1,
        image=body.image, valid_from=body.valid_from, valid_to=body.valid_to,
        total=body.total, remaining=body.total, status="pending_review",
        additional_terms=body.additional_terms,
        is_limited_time=body.is_limited_time,
    )


def _get_vendor_coupon(db: Session, coupon_id: int,
                       vendor: models.User, *, lock: bool = False) -> models.Coupon:
    """A coupon managed by its owner, admin or active authorized staff."""
    q = db.query(models.Coupon).filter(models.Coupon.id == coupon_id)
    if lock:
        if db.get_bind().dialect.name == "sqlite":
            q.update({"id": models.Coupon.id}, synchronize_session=False)
        q = q.with_for_update().populate_existing()
    coupon = q.first()
    if coupon is None:
        raise HTTPException(status_code=404, detail="找不到此優惠券")
    _require_shop_write(db, vendor, coupon.shop_id)
    return coupon


def _shop_or_404(db: Session, shop_id: int) -> models.Shop:
    shop = db.query(models.Shop).filter(models.Shop.id == shop_id).first()
    if shop is None:
        raise HTTPException(status_code=404, detail="找不到此商店")
    return shop


def _require_shop_write(db: Session, user: models.User,
                        shop_id: int) -> models.Shop:
    """Shop the caller may manage: owner, admin, or an active member
    with manage_coupons (manager / legacy merchant-admin)."""
    shop = _shop_or_404(db, shop_id)
    if user.role == "admin" or shop.vendor_id == user.id:
        return shop
    if has_shop_perm(db, user, shop, "manage_coupons"):
        return shop
    raise HTTPException(status_code=403, detail="沒有權限")


def _require_shop_read(db: Session, user: models.User,
                       shop_id: int) -> models.Shop:
    shop = _shop_or_404(db, shop_id)
    if can_access_shop(db, user, shop):
        return shop
    raise HTTPException(status_code=403, detail="沒有權限")


def _require_any_coupon_manager(db: Session, user: models.User):
    if user.role == "admin" or db.query(models.Shop.id).filter_by(vendor_id=user.id).first():
        return
    if db.query(models.ShopMember.id).filter(
            models.ShopMember.user_id == user.id, models.ShopMember.status == "active",
            models.ShopMember.role.in_(("merchant-admin", "manager"))).first():
        return
    raise HTTPException(status_code=403, detail="沒有權限")


def _branch_out(b: models.Branch) -> dict:
    return {"id": b.id, "branch_id": b.id, "shop_id": b.shop_id,
            "name": b.name, "address": b.address or "",
            "phone": b.phone or "",
            "latitude": float(b.latitude) if b.latitude is not None else None,
            "longitude": float(b.longitude) if b.longitude is not None else None}


def _validated_branch_ids(db: Session, shop_id: int, branch_ids: List[int]) -> list:
    valid = {b.id for b in db.query(models.Branch).filter(
        models.Branch.id.in_(branch_ids), models.Branch.shop_id == shop_id).all()}
    if valid != set(branch_ids):
        raise HTTPException(400, "包含不存在或不屬於此店舖的分店")
    return sorted(valid)


class ShopCreateIn(BaseModel):
    name: str
    name_en: str = ""
    category_id: Optional[int] = None
    area_id: Optional[int] = None
    address: str = ""
    phone: str = ""
    description: str = ""
    image: str = ""


class BranchCreateIn(BaseModel):
    name: str
    address: str = ""
    phone: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class ShopPatchIn(BaseModel):
    name: Optional[str] = None
    name_en: Optional[str] = None
    category_id: Optional[int] = None
    area_id: Optional[int] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    description: Optional[str] = None
    image: Optional[str] = None
    business_hours: Optional[Union[Dict[str, Any], str]] = None
    gallery: Optional[Any] = None
    announcement: Optional[str] = None


@router.get("/get-vendor-coupon-list")
def vendor_coupons(status: str = Query(None),
                   vendor: models.User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    _require_any_coupon_manager(db, vendor)
    q = db.query(models.Coupon)
    if vendor.role != "admin":
        ids = {row.id for row in db.query(models.Shop.id).filter_by(vendor_id=vendor.id)}
        ids.update(row.shop_id for row in db.query(models.ShopMember.shop_id).filter(
            models.ShopMember.user_id == vendor.id, models.ShopMember.status == "active",
            models.ShopMember.role.in_(("merchant-admin", "manager"))))
        q = q.filter(models.Coupon.shop_id.in_(ids))
    if status:
        q = q.filter(models.Coupon.status == status)
    coupons = q.order_by(models.Coupon.id.desc()).all()
    return {"coupons": [coupon_out(db, c) for c in coupons]}


@router.get("/coupon-templates")
def coupon_templates(vendor: models.User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """§3.3: three fixed listing templates (滿減 / 折扣 / 買贈) as
    create-form presets. Read-only; the merchant fills in shop,
    dates and stock, then submits for review as usual."""
    _require_any_coupon_manager(db, vendor)
    return {"templates": COUPON_TEMPLATES}


@router.post("/add-coupon-preview", response_model=schemas.CouponPreviewOut)
def coupon_preview(body: schemas.CouponCreateIn,
                   vendor: models.User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    _require_shop_write(db, vendor, body.shop_id)
    _validate_coupon_dates_total(body)
    _validated_branch_ids(db, body.shop_id, body.branch_ids)
    tmp = models.Coupon(coupon_type=body.coupon_type,
                        coupon_name=body.coupon_name,
                        cash_amount=body.cash_amount,
                        percentage=body.percentage, gift=body.gift,
                        discount_price=body.discount_price)
    return {"coupon_name": body.coupon_name, "coupon_type": body.coupon_type,
            "amount_text": amount_text(tmp), "valid_from": body.valid_from,
            "valid_to": body.valid_to, "total": body.total,
            "shop_name": db.query(models.Shop).filter(
                models.Shop.id == body.shop_id).first().name}


@router.post("/coupon-previewed")
def coupon_create(body: schemas.CouponCreateIn,
                  vendor: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    # Step 2 of the two-step flow: preview first (add-coupon-preview),
    # then create. Submitted coupons go to review.
    shop = _require_shop_write(db, vendor, body.shop_id)
    _validate_coupon_dates_total(body)
    branch_ids = _validated_branch_ids(db, body.shop_id, body.branch_ids)
    coupon = _build_coupon(db.get(models.User, shop.vendor_id), body)
    db.add(coupon)
    db.flush()
    db.add_all([models.CouponBranch(coupon_id=coupon.id, branch_id=branch_id)
                for branch_id in branch_ids])
    db.commit()
    db.refresh(coupon)
    return {"message": "內容將提交審核，請耐心等待。",
            "coupon": coupon_out(db, coupon)}


class BulkCouponsIn(BaseModel):
    coupons: List[dict] = []


@router.post("/vendor/coupons/bulk")
def coupon_bulk_create(body: BulkCouponsIn,
                       vendor: models.User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """§3.3: create up to 20 coupons in one call (festival / chain
    setup). Each item is validated like a single create; failures are
    reported per item and never block the valid ones. Every created
    coupon goes to pending_review, as usual."""
    _require_any_coupon_manager(db, vendor)
    if not body.coupons:
        raise HTTPException(status_code=400, detail="請至少提供一張優惠券")
    if len(body.coupons) > BULK_MAX_COUPONS:
        raise HTTPException(
            status_code=400,
            detail=f"一次最多建立 {BULK_MAX_COUPONS} 張優惠券")
    created, failed = [], []
    for index, raw in enumerate(body.coupons):
        try:
            item = schemas.CouponCreateIn.model_validate(raw)
            shop = _require_shop_write(db, vendor, item.shop_id)
            _validate_coupon_dates_total(item)
            branch_ids = _validated_branch_ids(db, item.shop_id, item.branch_ids)
        except ValidationError as exc:
            failed.append({"index": index,
                           "error": "; ".join(
                               f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                               for e in exc.errors())})
            continue
        except HTTPException as exc:
            failed.append({"index": index, "error": exc.detail})
            continue
        coupon = _build_coupon(db.get(models.User, shop.vendor_id), item)
        db.add(coupon)
        db.flush()
        db.add_all([models.CouponBranch(coupon_id=coupon.id, branch_id=branch_id)
                    for branch_id in branch_ids])
        created.append(coupon)
    db.commit()
    for coupon in created:
        db.refresh(coupon)
    return {"created": [coupon_out(db, c) for c in created],
            "failed": failed,
            "message": f"已建立 {len(created)} 張，失敗 {len(failed)} 張"}


@router.patch("/vendor/coupons/{coupon_id}")
def coupon_edit(coupon_id: int, body: schemas.CouponUpdateIn,
                vendor: models.User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """§3.2: merchant edits a coupon. Editing an active / paused /
    scheduled coupon sends it back to pending_review (price, stock
    and validity changes are re-reviewed); draft / rejected edits
    stay drafts; pending_review edits stay pending_review. Expired /
    sold_out coupons cannot be edited — create a new one."""
    coupon = _get_vendor_coupon(db, coupon_id, vendor, lock=True)
    # Paid orders refer to this coupon row as their entitlement. Until
    # coupon versioning is available, protect all orders that can be
    # paid/retried and their historical terms; merchants may issue a new
    # coupon rather than changing the terms of an existing purchase.
    if db.query(Order.id).filter(Order.coupon_id == coupon.id,
                                 Order.payment_status != "cancelled").first():
        raise HTTPException(409, "此優惠券已有訂單，請建立新券以修改條款")
    if coupon.status in ("expired", "sold_out"):
        raise HTTPException(status_code=400, detail="已過期或已售罄的優惠券不可編輯，請重新建立")
    if coupon.status not in listing.EDITABLE_STATES:
        raise HTTPException(status_code=400, detail="此狀態的優惠券不可編輯")
    valid_from = body.valid_from if body.valid_from is not None else coupon.valid_from
    valid_to = body.valid_to if body.valid_to is not None else coupon.valid_to
    if valid_to <= valid_from:
        raise HTTPException(status_code=400, detail="到期日期必須晚於生效日期")
    if body.refund_policy is not None and body.refund_policy not in listing.REFUND_POLICIES:
        raise HTTPException(status_code=400, detail="refund_policy 不正確")
    claimed = (coupon.total or 0) - (coupon.remaining or 0)
    for field in ("coupon_name", "name_en", "category_id", "cash_amount",
                  "percentage", "gift", "discount_price", "regular_price",
                  "sale_price", "refund_policy", "min_spend",
                  "per_user_limit", "image", "valid_from", "valid_to",
                  "additional_terms", "is_limited_time"):
        value = getattr(body, field)
        if value is not None:
            setattr(coupon, field, value)
    if body.total is not None:
        coupon.total = body.total
        coupon.remaining = max(0, body.total - claimed)
    if coupon.status in listing.RE_REVIEW_STATES:
        coupon.status = "pending_review"
    elif coupon.status == "rejected":
        coupon.status = "draft"
    db.commit()
    db.refresh(coupon)
    return {"message": "已儲存，內容將重新提交審核。" if coupon.status == "pending_review" else "已儲存。",
            "coupon": coupon_out(db, coupon)}


@router.post("/vendor/coupons/{coupon_id}/pause")
def coupon_pause(coupon_id: int,
                 vendor: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    coupon = _get_vendor_coupon(db, coupon_id, vendor)
    if coupon.status != "active":
        raise HTTPException(status_code=400, detail="只有上架中的優惠券可以暫停")
    coupon.status = "paused"
    db.commit()
    return {"coupon_id": coupon.id, "status": coupon.status}


@router.post("/vendor/coupons/{coupon_id}/resume")
def coupon_resume(coupon_id: int,
                  vendor: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    coupon = _get_vendor_coupon(db, coupon_id, vendor)
    if coupon.status != "paused":
        raise HTTPException(status_code=400, detail="只有已暫停的優惠券可以恢復上架")
    target = listing.live_status(coupon.valid_from, coupon.valid_to,
                                 coupon.remaining)
    if target == "expired":
        raise HTTPException(status_code=400, detail="優惠券已過期，無法重新上架")
    if target == "sold_out":
        raise HTTPException(status_code=400, detail="優惠券已售罄，無法重新上架")
    coupon.status = target
    db.commit()
    return {"coupon_id": coupon.id, "status": coupon.status}


@router.post("/vendor/coupons/{coupon_id}/unlist")
def coupon_unlist(coupon_id: int,
                  vendor: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """§3.2: take a coupon off the shelf back to draft; the merchant
    edits it and submits again. Expired / sold_out coupons are already
    off the shelf and cannot be unlisted."""
    coupon = _get_vendor_coupon(db, coupon_id, vendor)
    if coupon.status in ("expired", "sold_out"):
        raise HTTPException(status_code=400, detail="已過期或已售罄的優惠券不可下架")
    if coupon.status == "draft":
        raise HTTPException(status_code=400, detail="草稿本來就未上架")
    coupon.status = "draft"
    db.commit()
    return {"coupon_id": coupon.id, "status": coupon.status}


@router.post("/vendor/coupons/{coupon_id}/submit")
def coupon_submit(coupon_id: int,
                  vendor: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    coupon = _get_vendor_coupon(db, coupon_id, vendor)
    if coupon.status not in ("draft", "rejected"):
        raise HTTPException(status_code=400, detail="只有草稿或被駁回的優惠券可以提交審核")
    coupon.status = "pending_review"
    db.commit()
    return {"coupon_id": coupon.id, "status": coupon.status}


@router.post("/vendor/coupons/{coupon_id}/approve")
def approve_coupon(coupon_id: int, approve: bool = True, reason: str = "",
                   admin: models.User = Depends(require_role("admin")),
                   db: Session = Depends(get_db)):
    """§3.2: admin reviews a coupon. Approval lands on active, or on
    scheduled when valid_from is still in the future (the listing
    sweep activates it at valid_from), or sold_out when stock is
    already exhausted. Rejection records the reason. Only
    pending_review coupons can be decided."""
    coupon = db.query(models.Coupon).filter(models.Coupon.id == coupon_id).first()
    if coupon is None:
        raise HTTPException(status_code=404, detail="找不到此優惠券")
    if coupon.status != "pending_review":
        raise HTTPException(status_code=400, detail="只有待審核的優惠券可以審批")
    if approve:
        if coupon.valid_to <= _now():
            raise HTTPException(status_code=400, detail="優惠券已過期，無法上架")
        coupon.status = listing.approve_status(
            coupon.valid_from, coupon.valid_to, coupon.remaining)
        new_status = coupon.status
    else:
        coupon.status = "rejected"
        new_status = "rejected"
    notify(db, coupon.vendor_id,
           "優惠券審核通過" if approve else "優惠券審核未通過",
           f"「{coupon.coupon_name}」已通過審核。"
           if approve else f"「{coupon.coupon_name}」未通過審核，請修改後重新提交。",
           "coupon_approved" if approve else "coupon_rejected",
           target_coupon_id=coupon.id, target_shop_id=coupon.shop_id)
    db.commit()
    listing.audit(db, admin.id,
                  "coupon.approve" if approve else "coupon.reject",
                  "coupon", coupon.id,
                  f"approved -> {new_status}" if approve else (reason or ""))
    return {"coupon_id": coupon.id, "status": coupon.status,
            "reason": reason if not approve else ""}


# ---------- merchant shops & branches (dianping §1.2 / §1.4) ----------
@router.post("/merchant/shops", status_code=201)
def create_shop(body: ShopCreateIn,
                vendor: models.User = Depends(require_role("vendor", "admin")),
                db: Session = Depends(get_db)):
    """§1.2: an already-vetted vendor opens another shop. The vendor
    account itself was approved at onboarding, so the new shop starts
    is_approved=True; the merchant completes category / hours / photos
    via PATCH /merchant/shops/{id} (§1.4)."""
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="請輸入門店名稱")
    if body.category_id is not None and db.query(models.Category).filter(
            models.Category.id == body.category_id).first() is None:
        raise HTTPException(status_code=400, detail="找不到此分類")
    if body.area_id is not None and db.query(models.Area).filter(
            models.Area.id == body.area_id).first() is None:
        raise HTTPException(status_code=400, detail="找不到此地區")
    shop = models.Shop(vendor_id=vendor.id, name=body.name.strip(),
                        name_en=body.name_en, category_id=body.category_id,
                        area_id=body.area_id, address=body.address,
                        phone=body.phone, description=body.description,
                        image=body.image, is_approved=True)
    db.add(shop)
    db.commit()
    db.refresh(shop)
    return {"message": "門店已建立", "shop": shop_out(db, shop)}


@router.get("/merchant/shops/{shop_id}")
def get_merchant_shop(shop_id: int,
                      user: models.User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    shop = _require_shop_read(db, user, shop_id)
    branches = (db.query(models.Branch)
                .filter(models.Branch.shop_id == shop.id)
                .order_by(models.Branch.id).all())
    return {"shop": shop_out(db, shop),
            "branches": [_branch_out(b) for b in branches]}


@router.get("/merchant/shops/{shop_id}/branches")
def list_branches(shop_id: int,
                  user: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    shop = _require_shop_read(db, user, shop_id)
    branches = (db.query(models.Branch)
                .filter(models.Branch.shop_id == shop.id)
                .order_by(models.Branch.id).all())
    return {"shop_id": shop.id,
            "branches": [_branch_out(b) for b in branches]}


@router.post("/merchant/shops/{shop_id}/branches", status_code=201)
def create_branch(shop_id: int, body: BranchCreateIn,
                  user: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    shop = _require_shop_write(db, user, shop_id)
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="請輸入分店名稱")
    branch = models.Branch(shop_id=shop.id, name=body.name.strip(),
                           address=body.address, phone=body.phone,
                           latitude=body.latitude, longitude=body.longitude)
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return {"message": "分店已建立", "branch": _branch_out(branch)}


@router.patch("/merchant/shops/{shop_id}")
def update_shop(shop_id: int, body: ShopPatchIn,
                user: models.User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """§1.4: merchant self-maintenance — contact, hours, gallery,
    announcement. Owner / admin / manage_coupons member only."""
    shop = _require_shop_write(db, user, shop_id)
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(status_code=400, detail="門店名稱不能為空")
        shop.name = body.name.strip()
    if body.name_en is not None:
        shop.name_en = body.name_en
    if body.category_id is not None:
        if db.query(models.Category).filter(
                models.Category.id == body.category_id).first() is None:
            raise HTTPException(status_code=400, detail="找不到此分類")
        shop.category_id = body.category_id
    if body.area_id is not None:
        if db.query(models.Area).filter(
                models.Area.id == body.area_id).first() is None:
            raise HTTPException(status_code=400, detail="找不到此地區")
        shop.area_id = body.area_id
    if body.address is not None:
        shop.address = body.address
    if body.phone is not None:
        shop.phone = body.phone
    if body.description is not None:
        shop.description = body.description
    if body.image is not None:
        shop.image = body.image
    if body.business_hours is not None:
        if isinstance(body.business_hours, dict):
            shop.business_hours = json.dumps(body.business_hours,
                                             ensure_ascii=False)
        elif isinstance(body.business_hours, str):
            shop.business_hours = body.business_hours
        else:
            raise HTTPException(status_code=400,
                                detail="營業時間須為對象或文字")
    if body.gallery is not None:
        if not isinstance(body.gallery, list) or not all(
                isinstance(x, str) for x in body.gallery):
            raise HTTPException(status_code=400, detail="相冊須為圖片鏈接列表")
        if len(body.gallery) > 9:
            raise HTTPException(status_code=400, detail="相冊最多 9 張圖")
        shop.gallery = json.dumps(body.gallery, ensure_ascii=False)
    if body.announcement is not None:
        shop.announcement = body.announcement
    db.commit()
    db.refresh(shop)
    return {"message": "門店資料已更新", "shop": shop_out(db, shop)}


@router.get("/get-vendor-record-list")
def vendor_records(vendor: models.User = Depends(require_role("vendor", "admin")),
                   db: Session = Depends(get_db)):
    shop_ids = vendor_shop_ids(db, vendor)
    reds = (db.query(models.Redemption)
            .filter(models.Redemption.shop_id.in_(shop_ids))
            .order_by(models.Redemption.scanned_at.desc()).all()) if shop_ids else []
    out = []
    for r in reds:
        c = db.query(models.Coupon).filter(models.Coupon.id == r.coupon_id).first()
        out.append({"id": r.id, "coupon_name": c.coupon_name if c else "",
                    "scanned_at": r.scanned_at})
    return {"records": out}


@router.get("/get-company-list")
def get_companies(vendor: models.User = Depends(require_role("vendor", "admin")),
                  db: Session = Depends(get_db)):
    shops = db.query(models.Shop).filter(models.Shop.vendor_id == vendor.id).all()
    return {"companies": [shop_out(db, s) for s in shops]}


class AssignBranchesIn(BaseModel):
    coupon_id: int
    branch_ids: List[int]


@router.post("/assign-branches")
def assign_branches(body: AssignBranchesIn,
                    vendor: models.User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """§1.2: REAL assignment now — the old version only validated and
    returned, writing nothing. The set replaces the previous one; an
    empty list clears the restriction (coupon valid at all branches of
    its shop). Invalid branch ids reject the entire request."""
    query = db.query(models.Coupon).filter(models.Coupon.id == body.coupon_id)
    if db.get_bind().dialect.name == "sqlite":
        query.update({"id": models.Coupon.id}, synchronize_session=False)
    coupon = query.with_for_update().populate_existing().first()
    if coupon is None:
        raise HTTPException(status_code=404, detail="找不到此優惠券")
    _require_shop_write(db, vendor, coupon.shop_id)
    valid = _validated_branch_ids(db, coupon.shop_id, body.branch_ids)
    previous = {row.branch_id for row in db.query(models.CouponBranch).filter_by(coupon_id=coupon.id)}
    if previous == set(valid):
        return {"coupon_id": coupon.id, "assigned_branch_ids": sorted(previous)}
    order_links = [Order.coupon_id == coupon.id, Order.package_coupon_id == coupon.id]
    if coupon.package_id is not None:
        order_links.append(Order.package_id == coupon.package_id)
    if db.query(Order.id).filter(or_(*order_links), Order.payment_status != "cancelled").first():
        raise HTTPException(409, "此優惠券已有訂單，不能修改適用分店")
    db.query(models.CouponBranch).filter(
        models.CouponBranch.coupon_id == coupon.id).delete(
            synchronize_session=False)
    for branch_id in sorted(set(valid)):
        db.add(models.CouponBranch(coupon_id=coupon.id,
                                   branch_id=branch_id))
    db.commit()
    return {"coupon_id": coupon.id, "assigned_branch_ids": sorted(set(valid))}


@router.post("/update-branch-profile")
def update_branch(branch_id: int, name: str = None, address: str = None,
                  phone: str = None,
                  vendor: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    branch = db.query(models.Branch).filter(models.Branch.id == branch_id).first()
    if branch is None:
        raise HTTPException(status_code=404, detail="找不到此分店")
    # §1.3: owner / admin / manage_coupons member.
    _require_shop_write(db, vendor, branch.shop_id)
    if name is not None:
        branch.name = name
    if address is not None:
        branch.address = address
    if phone is not None:
        branch.phone = phone
    db.commit()
    return {"message": "分店資料已更新"}


@router.post("/delete-vandor-image")
def delete_vendor_image(shop_id: int,
                        vendor: models.User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    shop = _require_shop_write(db, vendor, shop_id)
    shop.image = ""
    db.commit()
    return {"message": "圖片已刪除"}


@router.post("/new-update-vendor-profile")
def update_vendor_profile(name: str = None, phone: str = None,
                          user: models.User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    if name is not None:
        user.name = name
    if phone is not None:
        user.phone = phone
    db.commit()
    return {"message": "商戶資訊已更新，正在等待管理員批准。"}
