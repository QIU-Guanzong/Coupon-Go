"""Search suggestions (three-branch), search history, Places proxy, filters."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import google_places, models
from ..database import get_db
from ..deps import get_current_user
from ..serializers import coupon_out, shop_out

router = APIRouter()


@router.post("/auto-suggestion-new")
def auto_suggestion(keyword: str, db: Session = Depends(get_db)):
    """Three-branch suggestion (from REVERSE.md §2):
    coupon -> coupon detail; non-coupon + has_branch=1 -> branch detail;
    otherwise -> shop detail."""
    kw = f"%{keyword}%"
    out = []
    for c in (db.query(models.Coupon)
              .filter(models.Coupon.status == "active",
                      models.Coupon.coupon_name.like(kw)).limit(5).all()):
        out.append({"kind": "coupon", "id": c.id, "title": c.coupon_name,
                    "subtitle": "", "has_branch": 0})
    for s in (db.query(models.Shop)
              .filter(models.Shop.name.like(kw)).limit(5).all()):
        branches = db.query(models.Branch).filter(
            models.Branch.shop_id == s.id).count()
        out.append({"kind": "shop" if branches == 0 else "branch", "id": s.id,
                    "title": s.name, "subtitle": s.address or "",
                    "has_branch": 1 if branches else 0})
    return {"suggestions": out, "keyword": keyword}


@router.get("/get-search-history")
def get_search_history(user: models.User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    rows = (db.query(models.SearchHistory)
            .filter(models.SearchHistory.user_id == user.id)
            .order_by(models.SearchHistory.id.desc()).limit(20).all())
    return {"history": [{"id": r.id, "search_value": r.search_value,
                         "created_at": r.created_at} for r in rows]}


@router.post("/delete-search-history")
def delete_search_history(history_id: int = None,
                          user: models.User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    q = db.query(models.SearchHistory).filter(
        models.SearchHistory.user_id == user.id)
    if history_id:
        q = q.filter(models.SearchHistory.id == history_id)
    q.delete()
    db.commit()
    return {"message": "已刪除搜尋記錄"}


@router.get("/new-filter-details")
def get_filters(db: Session = Depends(get_db)):
    return {
        "categories": [{"id": c.id, "name": c.name}
                       for c in db.query(models.Category).all()],
        "areas": [{"id": a.id, "name": a.name}
                  for a in db.query(models.Area).all()],
    }


# ---- Google Places proxy (key lives in local .env, see config.py) ----
@router.get("/place/autocomplete/json")
def places_autocomplete(input: str):
    try:
        return google_places.autocomplete(input)
    except google_places.GoogleNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/place/details/json")
def places_details(place_id: str):
    try:
        return google_places.place_details(place_id)
    except google_places.GoogleNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/place/nearbysearch/json")
def places_nearby(lat: float, lng: float, radius: int = 1000, keyword: str = ""):
    try:
        return google_places.nearby_search(lat, lng, radius, keyword)
    except google_places.GoogleNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
