"""Shop binding shared by simulated and verified merchant approval."""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import models
from .models_line_b import MerchantApplication


def bind_approved_shop(db: Session, app: MerchantApplication):
    """Bind a uniquely identified existing shop or create one for this owner.

    The applicant row is locked by the caller, serializing approvals for
    that owner. No Connect account is created or represented as verified.
    """
    if app.approved_shop_id:
        shop = db.query(models.Shop).filter_by(id=app.approved_shop_id).first()
        if shop is None or shop.vendor_id != app.user_id:
            raise HTTPException(409, "申請關聯的店舖不屬於此商戶，請聯絡平台核對")
        return shop
    namesakes = db.query(models.Shop).filter_by(vendor_id=app.user_id, name=app.shop_name).all()
    address = (app.address or "").strip()
    if namesakes and (not address or any(not (s.address or "").strip() for s in namesakes)):
        raise HTTPException(409, "已有同名店舖，請聯絡平台核對地址")
    matches = [s for s in namesakes if (s.address or "").strip() == address]
    if len(matches) > 1:
        raise HTTPException(409, "已有多間同名同址店舖，請聯絡平台核對")
    if matches:
        shop = matches[0]
        shop.is_approved = True
    else:
        shop = models.Shop(vendor_id=app.user_id, name=app.shop_name,
                           address=app.address or "", phone=app.phone or "", is_approved=True)
        db.add(shop)
        db.flush()
    app.approved_shop_id = shop.id
    return shop


