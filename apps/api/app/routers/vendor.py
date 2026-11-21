"""Vendor (merchant) routes: issue coupon (two-step preview -> create),
vendor wallet / records, branches, profile, subscription plans, approval.

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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_user, require_role
from ..notify import notify
from ..permissions_line_f import (
    can_access_shop, has_shop_perm,
)
from ..serializers import amount_text, coupon_out, shop_out

router = APIRouter()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


def _branch_out(b: models.Branch) -> dict:
    return {"id": b.id, "branch_id": b.id, "shop_id": b.shop_id,
            "name": b.name, "address": b.address or "",
            "phone": b.phone or "",
            "latitude": float(b.latitude) if b.latitude is not None else None,
            "longitude": float(b.longitude) if b.longitude is not None else None}


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
def vendor_coupons(vendor: models.User = Depends(require_role("vendor", "admin")),
                    db: Session = Depends(get_db)):
    coupons = (db.query(models.Coupon)
               .filter(models.Coupon.vendor_id == vendor.id)
               .order_by(models.Coupon.id.desc()).all())
    return {"coupons": [coupon_out(db, c) for c in coupons]}


@router.post("/add-coupon-preview", response_model=schemas.CouponPreviewOut)
def coupon_preview(body: schemas.CouponCreateIn,
                   vendor: models.User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    # §1.3: the shop owner, admin, or a manage_coupons member may draft.
    shop = _require_shop_write(db, vendor, body.shop_id)
    if body.valid_to <= body.valid_from:
        raise HTTPException(status_code=400, detail="到期日期必須晚於生效日期")
    if body.total <= 0:
        raise HTTPException(status_code=400, detail="請輸入優惠券數量")
    tmp = models.Coupon(coupon_type=body.coupon_type,
                        coupon_name=body.coupon_name,
                        cash_amount=body.cash_amount,
                        percentage=body.percentage, gift=body.gift,
                        discount_price=body.discount_price)
    return {"coupon_name": body.coupon_name, "coupon_type": body.coupon_type,
            "amount_text": amount_text(tmp), "valid_from": body.valid_from,
            "valid_to": body.valid_to, "total": body.total,
            "shop_name": shop.name}


@router.post("/coupon-previewed")
def coupon_create(body: schemas.CouponCreateIn,
                  vendor: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    # Step 2 of the two-step flow: preview first (add-coupon-preview),
    # then create. Submitted coupons go to review.
    # §1.3: owner / admin / manage_coupons member; the coupon is owned by
    # the SHOP owner, not by whichever staff member filed it.
    shop = _require_shop_write(db, vendor, body.shop_id)
    if body.valid_to <= body.valid_from:
        raise HTTPException(status_code=400, detail="到期日期必須晚於生效日期")
    coupon = models.Coupon(
        shop_id=body.shop_id, vendor_id=shop.vendor_id,
        coupon_type=body.coupon_type, coupon_name=body.coupon_name,
        name_en=body.name_en, category_id=body.category_id,
        cash_amount=body.cash_amount, percentage=body.percentage,
        gift=body.gift, discount_price=body.discount_price,
        regular_price=body.regular_price, min_spend=body.min_spend,
        image=body.image, valid_from=body.valid_from, valid_to=body.valid_to,
        total=body.total, remaining=body.total, status="pending_review",
        additional_terms=body.additional_terms,
        is_limited_time=body.is_limited_time,
    )
    db.add(coupon)
    db.commit()
    db.refresh(coupon)
    return {"message": "內容將提交審核，請耐心等待。",
            "coupon": coupon_out(db, coupon)}


@router.post("/vendor/coupons/{coupon_id}/approve")
def approve_coupon(coupon_id: int, approve: bool = True,
                   admin: models.User = Depends(require_role("admin")),
                   db: Session = Depends(get_db)):
    coupon = db.query(models.Coupon).filter(models.Coupon.id == coupon_id).first()
    if coupon is None:
        raise HTTPException(status_code=404, detail="找不到此優惠券")
    coupon.status = "active" if approve else "rejected"
    # §1.5: tell the merchant the review outcome (in-app notification).
    notify(db, coupon.vendor_id,
           "優惠券審核通過" if approve else "優惠券審核未通過",
           f"「{coupon.coupon_name}」已通過審核，現已上架。"
           if approve else f"「{coupon.coupon_name}」未通過審核，請修改後重新提交。",
           "coupon_approved" if approve else "coupon_rejected",
           target_coupon_id=coupon.id, target_shop_id=coupon.shop_id)
    db.commit()
    return {"coupon_id": coupon.id, "status": coupon.status}


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
    shop_ids = [s.id for s in db.query(models.Shop).filter(
        models.Shop.vendor_id == vendor.id).all()]
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
    branch_ids: List[int] = []


@router.post("/assign-branches")
def assign_branches(body: AssignBranchesIn,
                    vendor: models.User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """§1.2: REAL assignment now — the old version only validated and
    returned, writing nothing. The set replaces the previous one; an
    empty list clears the restriction (coupon valid at all branches of
    its shop). Branch ids from another shop are dropped, never stored."""
    coupon = db.query(models.Coupon).filter(
        models.Coupon.id == body.coupon_id).first()
    if coupon is None:
        raise HTTPException(status_code=404, detail="找不到此優惠券")
    _require_shop_write(db, vendor, coupon.shop_id)
    valid = [b.id for b in db.query(models.Branch).filter(
        models.Branch.id.in_(body.branch_ids),
        models.Branch.shop_id == coupon.shop_id).all()]
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
