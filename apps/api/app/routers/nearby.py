"""Published merchant discovery and map links."""
from datetime import datetime, timezone
from math import cos, radians

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..geo import (google_maps_directions_url, google_maps_search_url,
                   haversine_km)
from ..models_line_b import ShopRating
from ..serializers import shop_out

router = APIRouter()
DEFAULT_RADIUS_KM = 10.0
MAX_RADIUS_KM = 50.0
SORTS = ("distance", "rating", "newest")


def published_shops(db, *, center=None, radius_km=DEFAULT_RADIUS_KM,
                    area_terms=(), category_terms=(), shop_terms=(), offer=None,
                    sort="distance", limit=None, category_id=None, after_id=0):
    """Filter and fetch catalogue metadata together, then rank using exact distance."""
    shop, area, cat = models.Shop, models.Area, models.Category
    voucher, package = models.Coupon, models.Package
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    active_coupons = db.query(voucher.id).filter(
        voucher.shop_id == shop.id, voucher.status == "active",
        voucher.valid_from <= now, voucher.valid_to > now, voucher.remaining > 0,
        voucher.kind != "package", voucher.is_points_exchange.is_(False)).exists()
    active_packages = db.query(package.id).filter(
        package.shop_id == shop.id, package.status == "active",
        package.valid_from <= now, package.valid_to > now, package.stock_left > 0).exists()
    branches = db.query(func.count(models.Branch.id)).filter(
        models.Branch.shop_id == shop.id).scalar_subquery()
    rows = (db.query(shop, models.User.name, cat.name, area.name, branches,
                     ShopRating.overall, active_coupons, active_packages)
            .outerjoin(models.User, models.User.id == shop.vendor_id)
            .outerjoin(cat, cat.id == shop.category_id)
            .outerjoin(area, area.id == shop.area_id)
            .outerjoin(ShopRating, ShopRating.shop_id == shop.id)
            .filter(shop.is_approved.is_(True)))
    if center is not None:
        lat, lng = center
        # Conservative bounding box before exact Haversine filtering. At the
        # poles or date line, latitude alone remains a safe pre-filter.
        delta_lat = radius_km / 110
        rows = rows.filter(shop.latitude.between(lat - delta_lat, lat + delta_lat),
                           shop.longitude.is_not(None))
        extreme_lat = abs(lat) + delta_lat
        if extreme_lat < 89:
            delta_lng = delta_lat / cos(radians(extreme_lat))
            if -180 < lng - delta_lng <= lng + delta_lng < 180:
                rows = rows.filter(shop.longitude.between(lng - delta_lng, lng + delta_lng))
    if category_id is not None:
        rows = rows.filter(shop.category_id == category_id, cat.is_active.is_(True))
    if after_id:
        rows = rows.filter(shop.id > after_id)
    if area_terms:
        parent_names = db.query(area.id).filter(or_(
            area.name.in_(area_terms), func.lower(area.name_en).in_(area_terms)))
        rows = rows.filter(or_(area.id.in_(parent_names),
                               area.parent_id.in_(parent_names)))
    if category_terms:
        rows = rows.filter(and_(cat.is_active.is_(True), or_(
            cat.name.in_(category_terms), func.lower(cat.name_en).in_(category_terms))))
    if shop_terms:
        rows = rows.filter(or_(*(func.lower(column).contains(term, autoescape=True)
                               for term in shop_terms
                               for column in (shop.name, shop.name_en, shop.description))))
    if offer == "package":
        rows = rows.filter(active_packages)
    elif offer == "coupon":
        rows = rows.filter(active_coupons)
    elif offer == "any":
        rows = rows.filter(or_(active_coupons, active_packages))

    if center is None:
        rows = rows.order_by(shop.id)
        if limit is not None:
            rows = rows.limit(limit)
    matches = []
    for item, vendor, category, district, count, rating, coupons, packages in rows:
        distance = None
        if center is not None:
            distance = haversine_km(*center, float(item.latitude), float(item.longitude))
            if distance > radius_km:
                continue
        matches.append((item, (vendor, category, district, count), rating, coupons, packages, distance))
    if center is not None:
        matches.sort(key=lambda row: (row[-1], row[0].id))
    if sort == "rating":
        matches.sort(key=lambda row: (row[2] is None, -(row[2] or 0)))
    elif sort == "newest":
        matches.sort(key=lambda row: row[0].created_at or datetime.min, reverse=True)

    out = []
    for item, related, rating, coupons, packages, distance in matches[:limit]:
        result = shop_out(db, item, related=related)
        result.update(rating=rating, distance_km=round(distance, 2) if distance is not None else None,
                      has_coupons=bool(coupons), has_packages=bool(packages),
                      created_at=item.created_at.isoformat() if item.created_at else None)
        if item.latitude is not None and item.longitude is not None:
            lat, lng = float(item.latitude), float(item.longitude)
            result.update(google_maps_url=google_maps_search_url(lat, lng),
                          directions_url=google_maps_directions_url(lat, lng))
        out.append(result)
    return out


@router.get("/discover/shops")
def discover_shops(category_id: int = Query(..., gt=0), after_id: int = Query(0, ge=0),
                   db: Session = Depends(get_db)):
    category = db.get(models.Category, category_id)
    if category is None or not category.is_active:
        raise HTTPException(status_code=404, detail="找不到此商戶分類")
    shops = published_shops(db, category_id=category_id, after_id=after_id, limit=25)
    return {"category": {"name": category.name, "name_en": category.name_en},
            "shops": shops[:24], "next_after_id": shops[23]["id"] if len(shops) > 24 else None}


@router.get("/nearby-shops")
def nearby_shops(lat: float = Query(None), lng: float = Query(None),
                 radius_km: float = Query(DEFAULT_RADIUS_KM, gt=0, le=MAX_RADIUS_KM),
                 sort: str = Query("distance"), db: Session = Depends(get_db)):
    if lat is None or lng is None:
        raise HTTPException(status_code=400, detail="需要定位（lat/lng）才能列出附近商户")
    if sort not in SORTS:
        raise HTTPException(status_code=400, detail="sort 只支援 distance / rating / newest")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise HTTPException(status_code=400, detail="經緯度超出範圍")
    shops = published_shops(db, center=(lat, lng), radius_km=radius_km, sort=sort)
    return {"center": {"lat": lat, "lng": lng}, "radius_km": radius_km,
            "sort": sort, "total": len(shops), "shops": shops}


@router.get("/shops/{shop_id}/map")
def shop_map(shop_id: int, db: Session = Depends(get_db)):
    shop = db.get(models.Shop, shop_id)
    if shop is None or not shop.is_approved:
        raise HTTPException(status_code=404, detail="找不到此商店")
    lat = float(shop.latitude) if shop.latitude is not None else None
    lng = float(shop.longitude) if shop.longitude is not None else None
    with_coordinates = lat is not None and lng is not None
    return {"shop_id": shop.id, "shop_name": shop.name, "address": shop.address or "",
            "latitude": lat, "longitude": lng,
            "google_maps_url": (google_maps_search_url(lat, lng) if with_coordinates
                                else google_maps_search_url(shop.address or shop.name)),
            "directions_url": google_maps_directions_url(lat, lng) if with_coordinates else None}
