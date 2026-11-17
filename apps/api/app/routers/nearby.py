"""Nearby shops + shop map (line A, report §5.2 / §10 F5).

- GET /nearby-shops?lat=&lng=&radius_km=&sort=distance — nearby merchant
  list sorted by Haversine straight-line distance (ascending). lat/lng
  are required: without a location there is no "nearby" (the client
  falls back to manual city / area browsing). radius_km defaults to 10.
- sort=rating — overall stars descending, unrated shops last.
- sort=newest — created_at descending, shops without one last.
- Each shop carries rating (ShopRating.overall, null when unrated —
  never invented), has_coupons / has_packages (an *active* coupon /
  package exists for the shop) and created_at, so the client no longer
  needs parallel /shops/{id}/rating calls to sort by rating or newest.
- GET /shops/{id}/map — shop position (lat/lng, address) + Google Maps
  search / directions links for in-app navigation jump (Hong Kong).
"""
from fastapi import APIRouter, Depends, HTTPException, Query
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


@router.get("/nearby-shops")
def nearby_shops(lat: float = Query(None), lng: float = Query(None),
                 radius_km: float = Query(DEFAULT_RADIUS_KM, gt=0,
                                          le=MAX_RADIUS_KM),
                 sort: str = Query("distance"),
                 db: Session = Depends(get_db)):
    if lat is None or lng is None:
        raise HTTPException(status_code=400,
                            detail="需要定位（lat/lng）才能列出附近商户")
    if sort not in SORTS:
        raise HTTPException(status_code=400,
                            detail="sort 只支援 distance / rating / newest")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise HTTPException(status_code=400, detail="經緯度超出範圍")
    shops = db.query(models.Shop).filter(models.Shop.is_approved.is_(True)).all()
    out = []
    for s in shops:
        d = shop_out(db, s)
        if s.latitude is None or s.longitude is None:
            continue  # no coordinates: cannot be placed on a map / ranked
        dist = haversine_km(lat, lng, float(s.latitude), float(s.longitude))
        if dist > radius_km:
            continue
        d["distance_km"] = round(dist, 2)
        d["latitude"] = float(s.latitude)
        d["longitude"] = float(s.longitude)
        d["google_maps_url"] = google_maps_search_url(
            float(s.latitude), float(s.longitude), s.name)
        d["directions_url"] = google_maps_directions_url(
            float(s.latitude), float(s.longitude))
        # Gap 8: rating / flags / created_at, from the shop's own rows.
        rating = db.query(ShopRating).filter(
            ShopRating.shop_id == s.id).first()
        d["rating"] = rating.overall if rating else None
        d["has_coupons"] = db.query(models.Coupon).filter(
            models.Coupon.shop_id == s.id,
            models.Coupon.status == "active").first() is not None
        d["has_packages"] = db.query(models.Package).filter(
            models.Package.shop_id == s.id,
            models.Package.status == "active").first() is not None
        d["created_at"] = (s.created_at.isoformat()
                           if s.created_at else None)
        out.append(d)
    # Distance is the tiebreak for every sort (stable sorts, applied
    # first so the later sort keeps it for equal keys).
    out.sort(key=lambda x: x["distance_km"])
    if sort == "rating":
        # Stars descending; unrated (None) last.
        out.sort(key=lambda x: (x["rating"] is None, -(x["rating"] or 0)))
    elif sort == "newest":
        # created_at descending (ISO strings sort chronologically);
        # missing created_at ("") sorts last under reverse=True.
        out.sort(key=lambda x: x["created_at"] or "", reverse=True)
    return {"center": {"lat": lat, "lng": lng},
            "radius_km": radius_km, "sort": sort,
            "total": len(out), "shops": out}


@router.get("/shops/{shop_id}/map")
def shop_map(shop_id: int, db: Session = Depends(get_db)):
    shop = db.query(models.Shop).filter(models.Shop.id == shop_id).first()
    if shop is None:
        raise HTTPException(status_code=404, detail="找不到此商店")
    lat = float(shop.latitude) if shop.latitude is not None else None
    lng = float(shop.longitude) if shop.longitude is not None else None
    if lat is not None and lng is not None:
        maps_url = google_maps_search_url(lat, lng, shop.name)
        directions_url = google_maps_directions_url(lat, lng)
    else:
        maps_url = (f"https://www.google.com/maps/search/?api=1&query="
                    f"{shop.address or shop.name}")
        directions_url = None
    return {"shop_id": shop.id, "shop_name": shop.name,
            "address": shop.address or "",
            "latitude": lat, "longitude": lng,
            "google_maps_url": maps_url,
            "directions_url": directions_url}
