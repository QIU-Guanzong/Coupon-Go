"""Resolve and version package templates without rewriting issued vouchers."""
from fastapi import HTTPException

from . import models
from .models_packages import Order, Package


def current_package_coupon(db, package_id):
    rows = (db.query(models.Coupon)
            .filter(models.Coupon.package_id == package_id,
                    models.Coupon.kind == "package")
            .order_by(models.Coupon.id).all())
    current = [row for row in rows if row.is_package_template is True]
    if len(current) == 1:
        package = db.get(Package, package_id)
        if package is None or current[0].shop_id != package.shop_id:
            raise HTTPException(409, "套餐券商戶資料需要人工核對")
        return current[0]
    if len(current) > 1:
        raise HTTPException(409, "套餐券模板需要人工核對")
    legacy = [row for row in rows if row.is_package_template is None]
    if len(legacy) == 1:
        package = db.get(Package, package_id)
        if package is None or legacy[0].shop_id != package.shop_id:
            raise HTTPException(409, "歷史套餐券商戶資料需要人工核對")
        return legacy[0]
    if len(legacy) > 1:
        raise HTTPException(409, "歷史套餐券模板需要人工核對")
    return None


def sync_edited_package_template(db, package):
    """Keep already issued coupon rows immutable before updating the listing."""
    coupon = current_package_coupon(db, package.id)
    if coupon is None:
        raise HTTPException(409, "套餐券模板缺失，請聯絡客服")
    issued = db.query(models.WalletItem.id).filter_by(coupon_id=coupon.id).first()
    pinned = db.query(Order.id).filter_by(package_coupon_id=coupon.id).first()
    if issued or pinned:
        values = {col.name: getattr(coupon, col.name)
                  for col in models.Coupon.__table__.columns
                  if col.name not in ("id", "created_at")}
        values["is_package_template"] = True
        values["downloads"] = 0
        values["used_count"] = 0
        coupon.is_package_template = False
        coupon = models.Coupon(**values)
        db.add(coupon)
    else:
        coupon.is_package_template = True
    coupon.coupon_name = package.name
    coupon.discount_price = package.package_price
    coupon.regular_price = package.original_price
    coupon.valid_from = package.valid_from
    coupon.valid_to = package.valid_to
    coupon.total = package.stock_total
    coupon.remaining = package.stock_left
    coupon.person_count = package.person_count
    coupon.usage_rules = package.usage_rules
    coupon.refund_policy = package.refund_policy
    coupon.status = package.status
    db.flush()
    return coupon
