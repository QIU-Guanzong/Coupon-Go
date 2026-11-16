"""Reviews / ratings v1 (F6 v1).

Endpoints (report §12, 评分):
- POST /reviews — write a review; only a redeemed user may review.
- GET  /shops/{shop_id}/reviews — public review list (visible only).
- GET  /shops/{shop_id}/rating — public aggregate (total + 3 dims + count).
- POST /reviews/{id}/reply — merchant (shop owner) or admin replies.
- POST /reviews/{id}/dispute — author / shop vendor / admin disputes;
  disputed reviews leave the aggregate until resolved (v2).

Points / stars split (report §5.3-10, task contract): the v0.1
"review +15 points" must not move stars. A review that takes the
+15 points has counts_toward_rating=False and never enters
ShopRating. Only a review that forgoes the points (claim_points=False)
counts toward the rating. The legacy POST /review endpoint is left
untouched (points-only, no Review row, no rating effect).
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import config, models, schemas_line_b
from ..database import get_db
from ..deps import add_points, get_current_user, points_balance, require_role
from ..models_line_b import Review, ShopRating

router = APIRouter()


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


def review_out(db: Session, r: Review) -> dict:
    user = db.query(models.User).filter(models.User.id == r.user_id).first()
    return {
        "id": r.id, "user_id": r.user_id,
        "user_name": user.name if user else "",
        "shop_id": r.shop_id, "redemption_id": r.redemption_id,
        "wallet_item_id": r.wallet_item_id,
        "taste": r.taste, "ambience": r.ambience, "service": r.service,
        "overall": r.overall, "content": r.content or "",
        "images": r.images_list, "tags": r.tags_list,
        "points_given": r.points_given,
        "counts_toward_rating": r.counts_toward_rating,
        "status": r.status, "reply_text": r.reply_text or "",
        "replied_at": r.replied_at, "created_at": r.created_at,
    }


def recompute_rating(db: Session, shop_id: int) -> ShopRating:
    """Rebuild the ShopRating row from counted, visible reviews only."""
    counted = (db.query(Review)
               .filter(Review.shop_id == shop_id,
                       Review.status == "visible",
                       Review.counts_toward_rating.is_(True)).all())
    row = db.query(ShopRating).filter(ShopRating.shop_id == shop_id).first()
    if row is None:
        row = ShopRating(shop_id=shop_id)
        db.add(row)
    if not counted:
        row.overall = row.taste = row.ambience = row.service = None
        row.review_count = 0
    else:
        n = len(counted)
        row.overall = round(sum(r.overall for r in counted) / n, 2)
        row.taste = round(sum(r.taste for r in counted) / n, 2)
        row.ambience = round(sum(r.ambience for r in counted) / n, 2)
        row.service = round(sum(r.service for r in counted) / n, 2)
        row.review_count = n
    row.updated_at = _now()
    db.flush()
    return row


def _resolve_redemption(db: Session, user: models.User,
                        body: schemas_line_b.ReviewCreateIn):
    """Return (wallet_item, redemption) for the review, or raise."""
    if body.redemption_id is not None:
        red = db.query(models.Redemption).filter(
            models.Redemption.id == body.redemption_id).first()
        if red is None:
            raise HTTPException(status_code=404, detail="找不到此核銷記錄")
        item = db.query(models.WalletItem).filter(
            models.WalletItem.id == red.wallet_item_id).first()
        if item is None or item.user_id != user.id:
            raise HTTPException(status_code=403, detail="只能評價自己的已核銷優惠券")
        return item, red
    if body.wallet_item_id is not None:
        item = db.query(models.WalletItem).filter(
            models.WalletItem.id == body.wallet_item_id,
            models.WalletItem.user_id == user.id).first()
        if item is None:
            raise HTTPException(status_code=404, detail="找不到此優惠券")
        red = (db.query(models.Redemption)
               .filter(models.Redemption.wallet_item_id == item.id)
               .order_by(models.Redemption.id.desc()).first())
        if red is None:
            raise HTTPException(status_code=400, detail="只有已核銷的優惠券可以評價")
        return item, red
    raise HTTPException(status_code=422, detail="請提供 redemption_id 或 wallet_item_id")


@router.post("/reviews")
def create_review(body: schemas_line_b.ReviewCreateIn,
                  user: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    item, red = _resolve_redemption(db, user, body)
    if item.status != 2:
        raise HTTPException(status_code=400, detail="只有已使用的優惠券可以評價")
    existing = db.query(Review).filter(Review.redemption_id == red.id).first()
    if existing is not None or item.reviewed:
        raise HTTPException(status_code=400, detail="已經評價過")
    overall = round((body.taste + body.ambience + body.service) / 3, 2)
    points_given = bool(body.claim_points)
    review = Review(
        user_id=user.id, shop_id=red.shop_id, redemption_id=red.id,
        wallet_item_id=item.id, taste=body.taste, ambience=body.ambience,
        service=body.service, overall=overall,
        content=body.content or "",
        images=json.dumps(body.images, ensure_ascii=False),
        tags=json.dumps(body.tags, ensure_ascii=False),
        points_given=points_given,
        counts_toward_rating=not points_given,
        status="visible",
    )
    db.add(review)
    item.reviewed = True
    if points_given:
        add_points(db, user.id, config.POINTS_REVIEW, "review", ref_id=item.id)
    db.flush()
    recompute_rating(db, red.shop_id)
    db.commit()
    db.refresh(review)
    msg = (f"評價成功，+{config.POINTS_REVIEW} 分；此評價不計入商户星級"
           if points_given else "評價成功，已計入商户星級")
    return {"message": msg, "review": review_out(db, review),
            "balance": points_balance(db, user.id)}


@router.get("/shops/{shop_id}/reviews")
def list_shop_reviews(shop_id: int, page: int = Query(1, ge=1),
                      has_images: bool = Query(False),
                      db: Session = Depends(get_db)):
    shop = db.query(models.Shop).filter(models.Shop.id == shop_id).first()
    if shop is None:
        raise HTTPException(status_code=404, detail="找不到此商户")
    q = (db.query(Review)
         .filter(Review.shop_id == shop_id, Review.status == "visible")
         .order_by(Review.created_at.desc(), Review.id.desc()))
    all_reviews = q.all()
    if has_images:
        all_reviews = [r for r in all_reviews if r.images_list]
    per_page = 20
    total = len(all_reviews)
    page_items = all_reviews[(page - 1) * per_page: page * per_page]
    return {"shop_id": shop_id, "current_page": page,
            "total_pages": max(1, (total + per_page - 1) // per_page),
            "total": total,
            "reviews": [review_out(db, r) for r in page_items]}


@router.get("/shops/{shop_id}/rating")
def get_shop_rating(shop_id: int, db: Session = Depends(get_db)):
    shop = db.query(models.Shop).filter(models.Shop.id == shop_id).first()
    if shop is None:
        raise HTTPException(status_code=404, detail="找不到此商户")
    row = db.query(ShopRating).filter(ShopRating.shop_id == shop_id).first()
    if row is None:
        return {"shop_id": shop_id, "overall": None, "taste": None,
                "ambience": None, "service": None, "review_count": 0,
                "updated_at": None}
    return {"shop_id": shop_id, "overall": row.overall, "taste": row.taste,
            "ambience": row.ambience, "service": row.service,
            "review_count": row.review_count, "updated_at": row.updated_at}


@router.post("/reviews/{review_id}/reply")
def reply_review(review_id: int, body: schemas_line_b.ReviewReplyIn,
                 user: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    review = db.query(Review).filter(Review.id == review_id).first()
    if review is None:
        raise HTTPException(status_code=404, detail="找不到此評價")
    shop = db.query(models.Shop).filter(models.Shop.id == review.shop_id).first()
    if user.role != "admin" and (shop is None or shop.vendor_id != user.id):
        raise HTTPException(status_code=403, detail="只有該商户或管理員可以回覆")
    if not body.reply.strip():
        raise HTTPException(status_code=400, detail="回覆內容不能為空")
    review.reply_text = body.reply.strip()
    review.replied_at = _now()
    review.replied_by = user.id
    db.commit()
    return {"message": "回覆成功", "review": review_out(db, review)}


@router.post("/reviews/{review_id}/dispute")
def dispute_review(review_id: int, body: schemas_line_b.ReviewDisputeIn,
                   user: models.User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    review = db.query(Review).filter(Review.id == review_id).first()
    if review is None:
        raise HTTPException(status_code=404, detail="找不到此評價")
    shop = db.query(models.Shop).filter(models.Shop.id == review.shop_id).first()
    is_vendor = shop is not None and shop.vendor_id == user.id
    if user.role != "admin" and user.id != review.user_id and not is_vendor:
        raise HTTPException(status_code=403, detail="沒有權限申訴此評價")
    if review.status == "disputed":
        raise HTTPException(status_code=400, detail="此評價已在申訴中")
    review.status = "disputed"
    review.dispute_reason = body.reason or ""
    db.flush()
    recompute_rating(db, review.shop_id)
    db.commit()
    return {"message": "申訴已提交，此評價暫不計入星級",
            "review": review_out(db, review)}
