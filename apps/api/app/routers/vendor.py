"""Vendor (merchant) routes: issue coupon (two-step preview -> create),
vendor wallet / records, branches, profile, subscription plans, approval.
"""
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_user, require_role
from ..models_packages import REFUND_POLICIES
from ..serializers import amount_text, coupon_out, shop_out

router = APIRouter()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.get("/get-vendor-coupon-list")
def vendor_coupons(vendor: models.User = Depends(require_role("vendor", "admin")),
                    db: Session = Depends(get_db)):
    coupons = (db.query(models.Coupon)
               .filter(models.Coupon.vendor_id == vendor.id)
               .order_by(models.Coupon.id.desc()).all())
    return {"coupons": [coupon_out(db, c) for c in coupons]}


@router.post("/add-coupon-preview", response_model=schemas.CouponPreviewOut)
def coupon_preview(body: schemas.CouponCreateIn,
                   vendor: models.User = Depends(require_role("vendor", "admin")),
                   db: Session = Depends(get_db)):
    shop = db.query(models.Shop).filter(
        models.Shop.id == body.shop_id,
        models.Shop.vendor_id == vendor.id).first()
    if shop is None:
        raise HTTPException(status_code=404, detail="找不到此商店")
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
                  vendor: models.User = Depends(require_role("vendor", "admin")),
                  db: Session = Depends(get_db)):
    # Step 2 of the two-step flow: preview first (add-coupon-preview),
    # then create. Submitted coupons go to review.
    shop = db.query(models.Shop).filter(
        models.Shop.id == body.shop_id,
        models.Shop.vendor_id == vendor.id).first()
    if shop is None:
        raise HTTPException(status_code=404, detail="找不到此商店")
    if body.valid_to <= body.valid_from:
        raise HTTPException(status_code=400, detail="到期日期必須晚於生效日期")
    if body.refund_policy not in REFUND_POLICIES:
        raise HTTPException(status_code=400, detail="refund_policy 不正確")
    coupon = models.Coupon(
        shop_id=body.shop_id, vendor_id=vendor.id,
        coupon_type=body.coupon_type, coupon_name=body.coupon_name,
        name_en=body.name_en, category_id=body.category_id,
        cash_amount=body.cash_amount, percentage=body.percentage,
        gift=body.gift, discount_price=body.discount_price,
        regular_price=body.regular_price, min_spend=body.min_spend,
        # P1-A: sale_price > 0 = paid coupon; refund_policy rides along.
        sale_price=body.sale_price, refund_policy=body.refund_policy,
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
    db.commit()
    return {"coupon_id": coupon.id, "status": coupon.status}


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
                    vendor: models.User = Depends(require_role("vendor", "admin")),
                    db: Session = Depends(get_db)):
    coupon = db.query(models.Coupon).filter(
        models.Coupon.id == body.coupon_id,
        models.Coupon.vendor_id == vendor.id).first()
    if coupon is None:
        raise HTTPException(status_code=404, detail="找不到此優惠券")
    valid = [b.id for b in db.query(models.Branch).filter(
        models.Branch.id.in_(body.branch_ids),
        models.Branch.shop_id == coupon.shop_id).all()]
    return {"coupon_id": coupon.id, "assigned_branch_ids": valid}


@router.post("/update-branch-profile")
def update_branch(branch_id: int, name: str = None, address: str = None,
                  phone: str = None,
                  vendor: models.User = Depends(require_role("vendor", "admin")),
                  db: Session = Depends(get_db)):
    branch = db.query(models.Branch).filter(models.Branch.id == branch_id).first()
    if branch is None:
        raise HTTPException(status_code=404, detail="找不到此分店")
    shop = db.query(models.Shop).filter(models.Shop.id == branch.shop_id).first()
    if shop.vendor_id != vendor.id:
        raise HTTPException(status_code=403, detail="沒有權限")
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
                        vendor: models.User = Depends(require_role("vendor", "admin")),
                        db: Session = Depends(get_db)):
    shop = db.query(models.Shop).filter(
        models.Shop.id == shop_id, models.Shop.vendor_id == vendor.id).first()
    if shop is None:
        raise HTTPException(status_code=404, detail="找不到此商店")
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
