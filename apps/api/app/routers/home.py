"""Discovery routes: home, banners, classification, lists, shop/coupon details.

Endpoint names mirror the originals (REVERSE_SUPPLEMENT.md §4.1), including
the original misspellings: `recomandation-coupons`, `get-used-merchents-shop`,
`get-postion-shops`, `get-nearest-branchs`.
"""
import math
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import config, models
from ..database import get_db
from ..deps import get_current_user, get_optional_user
from ..serializers import coupon_out, shop_out

router = APIRouter()


from ..timeutil import utcnow as _now


def _active_coupons(db: Session):
    return db.query(models.Coupon).filter(
        models.Coupon.status == "active",
        models.Coupon.valid_to > _now(),
        models.Coupon.is_points_exchange.is_(False),
    )


def _optional_user(db: Session, authorization: str = None):
    return None


@router.get("/home")
def get_home(lat: float = Query(None), lng: float = Query(None),
             db: Session = Depends(get_db)):
    active = _active_coupons(db)
    popular = active.order_by(models.Coupon.downloads.desc()).limit(10).all()
    week = active.order_by(models.Coupon.id.desc()).limit(10).all()
    most_grab = active.order_by(models.Coupon.downloads.desc()).limit(10).all()
    recommended = active.order_by(models.Coupon.id).limit(10).all()
    limited = active.filter(models.Coupon.is_limited_time.is_(True)).all()
    shops = db.query(models.Shop).filter(models.Shop.is_approved.is_(True)).limit(10).all()
    # Gap 10: distance_km on the home coupon lists, only when the
    # caller sent a location (both lat and lng); otherwise null.
    has_loc = lat is not None and lng is not None
    loc = (lat, lng) if has_loc else (None, None)
    return {
        "banners": [
            {"image": "assets/images/bg_main.png", "title": "全新優惠券平台",
             "subtitle": "Coupon GO!"},
            {"image": "assets/images/ic_program_card.png", "title": "新客戶首月免費",
             "subtitle": "訂閱計劃"},
        ],
        "categories": [
            {"id": c.id, "name": c.name, "name_en": c.name_en, "icon": c.icon}
            for c in db.query(models.Category).all()
        ],
        "popular_coupons": [coupon_out(db, c, lat=loc[0], lng=loc[1]) for c in popular],
        "week_coupons": [coupon_out(db, c, lat=loc[0], lng=loc[1]) for c in week],
        "most_grab": [coupon_out(db, c, lat=loc[0], lng=loc[1]) for c in most_grab],
        "recommended": [coupon_out(db, c, lat=loc[0], lng=loc[1]) for c in recommended],
        "limited_time_coupons": [coupon_out(db, c, lat=loc[0], lng=loc[1]) for c in limited],
        "popular_merchants": [shop_out(db, s) for s in shops],
        "frequently_used_shops": [shop_out(db, s) for s in shops[:6]],
        "new_shops": [shop_out(db, s) for s in shops[:6]],
    }


@router.get("/advertisement-banners")
def get_banners(db: Session = Depends(get_db)):
    # The only endpoint confirmed guest-callable in the original app.
    return {"banners": [
        {"image": "assets/images/bg_main.png", "title": "全新優惠券平台"},
        {"image": "assets/images/ic_program_card.png", "title": "新客戶首月免費"},
    ]}


@router.get("/get-classification-coupons")
def get_classification(category_id: int = None, area: str = None,
                       db: Session = Depends(get_db)):
    q = _active_coupons(db)
    if category_id:
        q = q.filter(models.Coupon.category_id == category_id)
    if area:
        area_row = db.query(models.Area).filter(models.Area.name == area).first()
        if area_row:
            shop_ids = [s.id for s in db.query(models.Shop).filter(
                models.Shop.area_id == area_row.id).all()]
            q = q.filter(models.Coupon.shop_id.in_(shop_ids))
    return {"coupons": [coupon_out(db, c) for c in q.all()]}


@router.get("/get-limited-time-coupons")
def get_limited_time(db: Session = Depends(get_db)):
    q = _active_coupons(db).filter(models.Coupon.is_limited_time.is_(True))
    return {"coupons": [coupon_out(db, c) for c in q.all()]}


@router.get("/get-most-people-grab")
def get_most_grab(db: Session = Depends(get_db)):
    q = _active_coupons(db).order_by(models.Coupon.downloads.desc())
    return {"coupons": [coupon_out(db, c) for c in q.all()]}


@router.get("/recomandation-coupons")
def get_recommended(db: Session = Depends(get_db)):
    q = _active_coupons(db).order_by(models.Coupon.id)
    return {"coupons": [coupon_out(db, c) for c in q.all()]}


@router.get("/get-used-merchents-shop")
def get_frequently_used(db: Session = Depends(get_db)):
    shops = db.query(models.Shop).filter(models.Shop.is_approved.is_(True)).all()
    return {"shops": [shop_out(db, s) for s in shops]}


@router.get("/get-postion-shops")
def get_position_shops(lat: float = Query(None), lng: float = Query(None),
                       db: Session = Depends(get_db)):
    shops = db.query(models.Shop).filter(models.Shop.is_approved.is_(True)).all()
    out = []
    for s in shops:
        d = shop_out(db, s)
        if lat is not None and lng is not None and s.latitude and s.longitude:
            d["nearest_shop_distance"] = round(_haversine(
                lat, lng, float(s.latitude), float(s.longitude)), 2)
        else:
            d["nearest_shop_distance"] = None
        out.append(d)
    out.sort(key=lambda x: (x["nearest_shop_distance"] is None,
                            x["nearest_shop_distance"] or 0))
    return {"shops": out}


def _haversine(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@router.get("/get-area-list")
def get_areas(db: Session = Depends(get_db)):
    return {"areas": [{"id": a.id, "name": a.name, "name_en": a.name_en}
                      for a in db.query(models.Area).all()]}


@router.get("/get-hong-kong-list")
def get_hong_kong(db: Session = Depends(get_db)):
    return _area_shops(db, "香港")


@router.get("/get-kowloon-list")
def get_kowloon(db: Session = Depends(get_db)):
    return _area_shops(db, "九龍")


@router.get("/get-new-territories-list")
def get_new_territories(db: Session = Depends(get_db)):
    return _area_shops(db, "新界")


def _area_shops(db: Session, name: str):
    area = db.query(models.Area).filter(models.Area.name == name).first()
    if not area:
        return {"shops": []}
    shops = db.query(models.Shop).filter(models.Shop.area_id == area.id).all()
    return {"area": name, "shops": [shop_out(db, s) for s in shops]}


@router.get("/get-category-list")
def get_categories(db: Session = Depends(get_db)):
    return {"categories": [
        {"id": c.id, "name": c.name, "name_en": c.name_en, "icon": c.icon}
        for c in db.query(models.Category).all()]}


@router.get("/shop-details")
def shop_details(shop_id: int, db: Session = Depends(get_db)):
    shop = db.query(models.Shop).filter(models.Shop.id == shop_id).first()
    if shop is None:
        raise HTTPException(status_code=404, detail="找不到此商店")
    branches = db.query(models.Branch).filter(models.Branch.shop_id == shop.id).all()
    coupons = _active_coupons(db).filter(models.Coupon.shop_id == shop.id).all()
    return {
        "shop": shop_out(db, shop),
        "branches": [{"id": b.id, "branch_id": b.id, "shop_id": b.shop_id,
                      "branch_name": b.name, "address": b.address or "",
                      "phone": b.phone or ""} for b in branches],
        "coupons": [coupon_out(db, c) for c in coupons],
    }


@router.get("/branch-details")
def branch_details(branch_id: int, db: Session = Depends(get_db)):
    b = db.query(models.Branch).filter(models.Branch.id == branch_id).first()
    if b is None:
        raise HTTPException(status_code=404, detail="找不到此分店")
    return {"id": b.id, "branch_id": b.id, "shop_id": b.shop_id,
            "branch_name": b.name, "address": b.address or "",
            "phone": b.phone or ""}


@router.get("/get-nearest-branchs")
def get_branches(shop_id: int, db: Session = Depends(get_db)):
    branches = db.query(models.Branch).filter(models.Branch.shop_id == shop_id).all()
    return {"branches": [{"id": b.id, "branch_id": b.id, "shop_id": b.shop_id,
                          "branch_name": b.name, "address": b.address or "",
                          "phone": b.phone or ""} for b in branches]}


@router.get("/coupon-details-new")
def coupon_details(coupon_id: int, db: Session = Depends(get_db),
                   user: models.User | None = Depends(get_optional_user)):
    c = db.query(models.Coupon).filter(models.Coupon.id == coupon_id).first()
    if c is None:
        raise HTTPException(status_code=404, detail="找不到此優惠券")
    shop = db.query(models.Shop).filter(models.Shop.id == c.shop_id).first()
    claimed = user is not None and db.query(models.WalletItem.id).filter_by(
        user_id=user.id, coupon_id=c.id).first() is not None
    return {"is_claimed": claimed, "coupon": coupon_out(db, c),
            "shop": shop_out(db, shop) if shop else None}


@router.get("/get-collection-shops")
def get_collection_shops(user: models.User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    favs = db.query(models.Favorite).filter(
        models.Favorite.user_id == user.id,
        models.Favorite.shop_id.isnot(None)).all()
    shops = [db.query(models.Shop).filter(models.Shop.id == f.shop_id).first()
             for f in favs]
    return {"shops": [shop_out(db, s, user.id) for s in shops if s]}


@router.get("/favorite-coupon-list")
def get_favorite_coupons(user: models.User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    favs = db.query(models.Favorite).filter(
        models.Favorite.user_id == user.id,
        models.Favorite.coupon_id.isnot(None)).all()
    coupons = [db.query(models.Coupon).filter(models.Coupon.id == f.coupon_id).first()
               for f in favs]
    return {"coupons": [coupon_out(db, c, user.id) for c in coupons if c]}


@router.post("/favorite-shop")
def favorite_shop(shop_id: int, user: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    fav = db.query(models.Favorite).filter(
        models.Favorite.user_id == user.id,
        models.Favorite.shop_id == shop_id).first()
    if fav:
        db.delete(fav)
        db.commit()
        return {"is_shop_favorite": False}
    db.add(models.Favorite(user_id=user.id, shop_id=shop_id))
    db.commit()
    return {"is_shop_favorite": True}


@router.post("/favorite-coupon-all")
def favorite_coupon(coupon_id: int, user: models.User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    fav = db.query(models.Favorite).filter(
        models.Favorite.user_id == user.id,
        models.Favorite.coupon_id == coupon_id).first()
    if fav:
        db.delete(fav)
        db.commit()
        return {"is_coupon_favorite": False}
    db.add(models.Favorite(user_id=user.id, coupon_id=coupon_id))
    db.commit()
    return {"is_coupon_favorite": True}


@router.get("/get-subscription-categories-list")
def get_sub_categories(db: Session = Depends(get_db)):
    return {"categories": [
        {"id": c.id, "name": c.name} for c in db.query(models.Category).all()]}


@router.get("/get-subscription-plan-list")
def get_plans(db: Session = Depends(get_db)):
    plans = db.query(models.SubscriptionPlan).filter(
        models.SubscriptionPlan.is_active.is_(True)).all()
    return {"plans": [
        {"id": p.id, "name": p.name, "monthly_fee": float(p.monthly_fee),
         "coupon_limit": p.coupon_limit, "description": p.description or ""}
        for p in plans]}


@router.get("/app-config")
def app_config():
    """Public display rules only; never serialize the config module or env."""
    return {
        "points_claim": config.POINTS_CLAIM,
        "points_redeem": config.POINTS_REDEEM,
        "points_review": config.POINTS_REVIEW,
        "qr_ttl_seconds": config.QR_TTL_SECONDS,
        "cashback_unlock_days": config.CASHBACK_DINEIN_UNLOCK_DAYS,
    }
