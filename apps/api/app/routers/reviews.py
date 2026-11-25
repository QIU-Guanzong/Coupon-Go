"""Reviews / ratings v1 (F6 v1).

Endpoints (report §12, 评分):
- POST /reviews — write a review; only a redeemed user may review.
- GET  /shops/{shop_id}/reviews — public review list (visible only).
- GET  /shops/{shop_id}/rating — public aggregate (total + 3 dims + count).
- POST /reviews/{id}/reply — merchant (shop owner) or admin replies.
- POST /reviews/{id}/dispute — author / shop vendor / admin disputes;
  disputed reviews leave the aggregate until resolved (v2).

Gap 9: helpful votes + server-side star distribution.
- POST /reviews/{id}/helpful — vote / cancel; one vote per user
  (review_helpful_votes has a unique (review_id, user_id) constraint;
  voting twice is a no-op, cancelling twice never goes negative).
- Every review carries helpful_count and helped_by_me (the latter is
  per-caller: anonymous callers always see False).
- rating_distribution (1-5 stars each) is computed server-side over
  ALL visible reviews of the shop and returned by both the list and
  the rating endpoints, so the client never stats page 1 only.
  Distribution covers all visible reviews; since M7 the averages /
  review_count in the rating endpoint cover the same set (every
  visible review counts toward the rating, points or not).
- GET /shops/{shop_id}/reviews?sort=helpful orders by helpful_count
  desc (then newest), so "最有用" is a real server sort.

Gap 11: review images ride the uploads chain — POST
/uploads/review-images returns /uploads/review-images/... paths and
POST /reviews accepts them as images[] (max 9); the list/detail
responses return the same paths. Storage stays the local-disk
placeholder in app/routers/uploads.py (object storage before launch).

Points / stars decoupled (M7, supersedes the old split): points and
stars are two separate books. EVERY visible review counts toward the
shop rating (counts_toward_rating=True), whether or not it took the
+15 points; claim_points only decides whether points are granted.
A revoked redemption cannot be reviewed, and revoking a redemption
hides its review and claws its review points back (see
hide_reviews_for_revoked_redemption, called from the revoke endpoint).
The legacy POST /review endpoint is left untouched (points-only, no
Review row, no rating effect).
"""
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import config, models, points, schemas_line_b
from ..database import get_db
from ..deps import (get_current_user, get_optional_user,
                    points_balance, require_role)
from ..shared import paginate
from ..models_line_b import Review, ReviewHelpfulVote, ShopRating
from ..notify import notify

router = APIRouter()


from ..timeutil import utcnow as _now


def _helpful_counts(db: Session, review_ids) -> dict:
    """{review_id: vote count} for the given ids, one query."""
    ids = list(review_ids)
    if not ids:
        return {}
    rows = (db.query(ReviewHelpfulVote.review_id,
                     func.count(ReviewHelpfulVote.id))
            .filter(ReviewHelpfulVote.review_id.in_(ids))
            .group_by(ReviewHelpfulVote.review_id).all())
    return {rid: n for rid, n in rows}


def _helped_ids(db: Session, review_ids, user_id) -> set:
    """Review ids among review_ids that user_id has voted helpful on."""
    if user_id is None:
        return set()
    rows = (db.query(ReviewHelpfulVote.review_id)
            .filter(ReviewHelpfulVote.review_id.in_(list(review_ids)),
                    ReviewHelpfulVote.user_id == user_id).all())
    return {r[0] for r in rows}


def _rating_distribution(db: Session, shop_id: int) -> dict:
    """Star distribution over ALL visible reviews of the shop.

    Bucketed by round(overall), clamped to 1-5. Keys are "1".."5"
    (JSON object keys); the values always sum to the visible-review
    count, never to the page size.
    """
    dist = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
    rows = (db.query(Review.overall)
            .filter(Review.shop_id == shop_id,
                    Review.status == "visible").all())
    for (overall,) in rows:
        star = max(1, min(5, round(overall)))
        dist[str(star)] += 1
    return dist


def review_out(db: Session, r: Review, helpful_counts=None,
               helped_ids=None) -> dict:
    user = db.query(models.User).filter(models.User.id == r.user_id).first()
    if helpful_counts is None:
        helpful_counts = _helpful_counts(db, [r.id])
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
        "helpful_count": helpful_counts.get(r.id, 0),
        "helped_by_me": r.id in (helped_ids or set()),
        "status": r.status, "reply_text": r.reply_text or "",
        "replied_at": r.replied_at, "created_at": r.created_at,
    }


def recompute_rating(db: Session, shop_id: int) -> ShopRating:
    """Rebuild the ShopRating row from all visible reviews (M7).

    Points and stars are decoupled: every visible review counts,
    whether or not it took the review points.
    """
    counted = (db.query(Review)
               .filter(Review.shop_id == shop_id,
                       Review.status == "visible").all())
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


def hide_reviews_for_revoked_redemption(db: Session, red) -> dict | None:
    """M7: hide the review(s) of a revoked redemption, claw points back.

    Decision (M7 req 2, "hide or keep-but-stop-counting"): HIDE.
    The M7 invariant is "every visible review counts toward the
    rating", so a review that must stop counting cannot stay visible.
    The row is kept (status "hidden", never deleted) as an audit trail
    and so an admin can restore it; its points are clawed back with a
    -15 PointTransaction (reason "review_revoke", same negative-delta
    pattern as the +20 redeem-points reversal in the revoke endpoint).
    The wallet item's `reviewed` flag is reset so that, if the returned
    coupon is re-redeemed, the new redemption can be reviewed fresh.
    Returns {"review_id", "points_clawed_back", "status"} for the
    (at most one) review found, else None. Caller commits.
    """
    review = db.query(Review).filter_by(redemption_id=red.id).with_for_update().first()
    item = db.get(models.WalletItem, red.wallet_item_id)
    if not item.reviewed and review is None:
        return None
    # The original /review endpoint wrote only the item flag and a ledger entry.
    # Hidden and disputed reviews can also have an outstanding reward.
    clawed = 0
    if review is None or review.points_given:
        clawed = points.clawback_points(
            db, item.user_id, "review", item.id,
            revoke_reason="review_revoke",
            revoke_ref_id=review.id if review else red.id,
            created_after=red.scanned_at)
    item.reviewed = False
    if review is not None:
        review.status = "hidden"
        review.counts_toward_rating = False
        db.flush()
        recompute_rating(db, red.shop_id)
    return {"review_id": review.id if review else None, "points_clawed_back": clawed,
            "status": "hidden" if review else "revoked"}


def _resolve_redemption(db: Session, user: models.User,
                        body: schemas_line_b.ReviewCreateIn):
    """Return (wallet_item, redemption) for the review, or raise."""
    if body.redemption_id is not None:
        red = db.query(models.Redemption).filter(
            models.Redemption.id == body.redemption_id).with_for_update().first()
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
               .order_by(models.Redemption.id.desc()).with_for_update().first())
        if red is None:
            raise HTTPException(status_code=400, detail="只有已核銷的優惠券可以評價")
        return item, red
    raise HTTPException(status_code=422, detail="請提供 redemption_id 或 wallet_item_id")


@router.post("/reviews")
def create_review(body: schemas_line_b.ReviewCreateIn,
                  user: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    item, red = _resolve_redemption(db, user, body)
    # M7: a revoked redemption cannot be reviewed — checked before the
    # item-status check so the message stays specific even if the
    # returned coupon was re-redeemed since (item.status == 2 again).
    if red.revoked_at is not None:
        raise HTTPException(status_code=400, detail="此核銷已被撤销，無法評價")
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
        # M7: points never affect the rating — every visible review counts.
        counts_toward_rating=True,
        status="visible",
    )
    marked = db.query(models.WalletItem).filter_by(id=item.id, status=2, reviewed=False).update(
        {"reviewed": True}, synchronize_session=False)
    if not marked:
        raise HTTPException(400, "優惠券狀態已更新，請返回錢包")
    db.add(review)
    try:
        if points_given:
            points.award_review_points(db, user.id, item.id)
        db.flush()
    except IntegrityError:
        # M4: the app-layer duplicate check above can lose a race — two
        # concurrent submits for one redemption both pass it, then the
        # DB UNIQUE constraint on reviews.redemption_id fires. That is
        # still "already reviewed", not a 500: roll back and answer 400.
        db.rollback()
        raise HTTPException(status_code=400, detail="已經評價過")
    recompute_rating(db, red.shop_id)
    db.commit()
    db.refresh(review)
    # §1.5: tell the shop owner a new review arrived.
    _shop = db.query(models.Shop).filter(models.Shop.id == red.shop_id).first()
    if _shop is not None:
        notify(db, _shop.vendor_id, "收到新評價",
               f"「{_shop.name}」收到一則 {overall} 星評價。",
               "review_received", target_shop_id=_shop.id)
        db.commit()
    msg = (f"評價成功，+{config.POINTS_REVIEW} 分，已計入商户星級"
           if points_given else "評價成功，已計入商户星級")
    return {"message": msg, "review": review_out(db, review),
            "balance": points_balance(db, user.id)}


@router.get("/shops/{shop_id}/reviews")
def list_shop_reviews(shop_id: int, page: int = Query(1, ge=1),
                      page_size: int = Query(20, ge=1, le=100),
                      has_images: bool = Query(False),
                      sort: str = Query("latest"),
                      db: Session = Depends(get_db),
                      user: models.User | None = Depends(get_optional_user)):
    if sort not in ("latest", "helpful"):
        raise HTTPException(status_code=422, detail="sort 只支援 latest / helpful")
    shop = db.query(models.Shop).filter(models.Shop.id == shop_id).first()
    if shop is None:
        raise HTTPException(status_code=404, detail="找不到此商户")
    q = (db.query(Review)
         .filter(Review.shop_id == shop_id, Review.status == "visible"))
    if sort == "helpful":
        counts_sq = (db.query(
            ReviewHelpfulVote.review_id.label("review_id"),
            func.count(ReviewHelpfulVote.id).label("c"))
            .group_by(ReviewHelpfulVote.review_id).subquery())
        q = q.outerjoin(counts_sq, counts_sq.c.review_id == Review.id)
        q = q.order_by(func.coalesce(counts_sq.c.c, 0).desc(),
                       Review.created_at.desc(), Review.id.desc())
    else:
        q = q.order_by(Review.created_at.desc(), Review.id.desc())
    all_reviews = q.all()
    if has_images:
        all_reviews = [r for r in all_reviews if r.images_list]
    out = paginate(all_reviews, page, page_size)
    page_items = out["items"]
    ids = [r.id for r in page_items]
    counts = _helpful_counts(db, ids)
    helped = _helped_ids(db, ids, user.id if user else None)
    return {"shop_id": shop_id, "current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "rating_distribution": _rating_distribution(db, shop_id),
            "reviews": [review_out(db, r, counts, helped) for r in page_items]}


def _review_management_shops(db: Session, user: models.User, shop_id: int | None):
    """Owner/admin scope used by merchant reply and the existing shop picker."""
    shops = db.query(models.Shop)
    if user.role != "admin":
        shops = shops.filter(models.Shop.vendor_id == user.id)
    if shop_id is not None:
        shops = shops.filter(models.Shop.id == shop_id)
    # Unrelated and missing shops deliberately produce the same result.
    if (user.role != "admin" or shop_id is not None) and not db.query(shops.exists()).scalar():
        raise HTTPException(status_code=403, detail="沒有權限管理此店舖的評價")
    return shops


def _reply_missing():
    return func.length(func.trim(func.coalesce(Review.reply_text, ""))) == 0


def _merchant_review_distribution(db: Session, allowed_ids) -> dict[str, int]:
    """SQL buckets matching the public endpoint's round() and 1–5 clamp."""
    star = case(
        (Review.overall < 1.5, 1),
        (Review.overall <= 2.5, 2),
        (Review.overall < 3.5, 3),
        (Review.overall <= 4.5, 4),
        else_=5,
    )
    counts = dict((db.query(star, func.count(Review.id))
                   .filter(Review.shop_id.in_(allowed_ids), Review.status == "visible")
                   .group_by(star).all()))
    return {str(n): counts.get(n, 0) for n in range(1, 6)}


@router.get("/merchant/reviews")
def list_merchant_reviews(
    shop_id: int | None = Query(None, ge=1),
    status: Literal["all", "visible", "disputed"] = "visible",
    unreplied: bool | None = None,
    max_stars: float | None = Query(None, ge=1, le=5),
    sort: Literal["latest", "helpful"] = "latest",
    page: int = Query(1, ge=1, le=100_000),
    page_size: int = Query(20, ge=1, le=100),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Merchant workspace only: filter/count in SQL, materialize one page."""
    shops = _review_management_shops(db, user, shop_id)
    allowed_ids = shops.with_entities(models.Shop.id).statement
    visible_statuses = ("visible", "disputed") if status == "all" else (status,)
    filters = [Review.shop_id.in_(allowed_ids), Review.status.in_(visible_statuses)]
    if unreplied is True:
        # Disputes are not awaiting action even when no reply exists.
        filters.extend([Review.status == "visible", _reply_missing()])
    elif unreplied is False:
        filters.append(~_reply_missing())
    if max_stars is not None:
        filters.append(Review.overall <= max_stars)

    total = db.query(func.count(Review.id)).filter(*filters).scalar() or 0
    total_pages = max(1, (total + page_size - 1) // page_size)
    current_page = min(page, total_pages)
    rows = db.query(Review).filter(*filters)
    if sort == "helpful":
        # Group only the matching review set; tie breaks are stable across pages.
        votes = (db.query(ReviewHelpfulVote.review_id,
                          func.count(ReviewHelpfulVote.id).label("vote_count"))
                 .filter(ReviewHelpfulVote.review_id.in_(
                     db.query(Review.id).filter(*filters).statement))
                 .group_by(ReviewHelpfulVote.review_id).subquery())
        rows = (rows.outerjoin(votes, votes.c.review_id == Review.id)
                .order_by(func.coalesce(votes.c.vote_count, 0).desc()))
    items = (rows.order_by(Review.created_at.desc(), Review.id.desc())
             .offset((current_page - 1) * page_size).limit(page_size).all())
    ids = [item.id for item in items]
    names = {}
    if items:
        names = dict(db.query(models.User.id, models.User.name)
                     .filter(models.User.id.in_({item.user_id for item in items})).all())
    counts = _helpful_counts(db, ids)
    return {"shop_id": shop_id, "current_page": current_page, "total_pages": total_pages,
            "total": total,
            "rating_distribution": _merchant_review_distribution(db, allowed_ids),
            "reviews": [
                {"id": item.id, "shop_id": item.shop_id,
                 "user_name": names.get(item.user_id, "") or "",
                 "taste": item.taste, "ambience": item.ambience, "service": item.service,
                 "overall": item.overall, "content": item.content or "",
                 "images": item.images_list, "tags": item.tags_list,
                 "helpful_count": counts.get(item.id, 0), "status": item.status,
                 "reply_text": (item.reply_text or "").strip(),
                 "replied_at": item.replied_at, "created_at": item.created_at}
                for item in items
            ]}


@router.get("/merchant/reviews/pending-count")
def merchant_review_pending_count(
    shop_id: int | None = Query(None, ge=1),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Count all actionable unreplied reviews, including zero-count shops."""
    shops = _review_management_shops(db, user, shop_id)
    counts = (shops.with_entities(models.Shop.id, func.count(Review.id))
              .outerjoin(Review, and_(Review.shop_id == models.Shop.id,
                                       Review.status == "visible", _reply_missing()))
              .group_by(models.Shop.id).order_by(models.Shop.id).all())
    return {"shop_id": shop_id, "total": sum(count for _, count in counts),
            "shops": [{"shop_id": sid, "pending_count": count} for sid, count in counts]}


@router.get("/shops/{shop_id}/rating")
def get_shop_rating(shop_id: int, db: Session = Depends(get_db)):
    shop = db.query(models.Shop).filter(models.Shop.id == shop_id).first()
    if shop is None:
        raise HTTPException(status_code=404, detail="找不到此商户")
    row = db.query(ShopRating).filter(ShopRating.shop_id == shop_id).first()
    if row is None:
        return {"shop_id": shop_id, "overall": None, "taste": None,
                "ambience": None, "service": None, "review_count": 0,
                "rating_distribution": _rating_distribution(db, shop_id),
                "updated_at": None}
    return {"shop_id": shop_id, "overall": row.overall, "taste": row.taste,
            "ambience": row.ambience, "service": row.service,
            "review_count": row.review_count,
            "rating_distribution": _rating_distribution(db, shop_id),
            "updated_at": row.updated_at}


@router.post("/reviews/{review_id}/helpful")
def vote_helpful(review_id: int, body: schemas_line_b.HelpfulVoteIn,
                 user: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """Gap 9: vote a review helpful (or cancel). Idempotent per user."""
    review = db.query(Review).filter(Review.id == review_id).first()
    if review is None:
        raise HTTPException(status_code=404, detail="找不到此評價")
    if review.status != "visible":
        raise HTTPException(status_code=400, detail="此評價不可投票")
    existing = (db.query(ReviewHelpfulVote)
                .filter(ReviewHelpfulVote.review_id == review_id,
                        ReviewHelpfulVote.user_id == user.id).first())
    if body.helpful:
        if existing is None:
            db.add(ReviewHelpfulVote(review_id=review_id, user_id=user.id))
            try:
                db.flush()
            except IntegrityError:
                # concurrent duplicate vote: the unique constraint won
                db.rollback()
        db.commit()
    else:
        if existing is not None:
            db.delete(existing)
            db.commit()
    count = (db.query(func.count(ReviewHelpfulVote.id))
             .filter(ReviewHelpfulVote.review_id == review_id).scalar())
    helped = (db.query(ReviewHelpfulVote)
              .filter(ReviewHelpfulVote.review_id == review_id,
                      ReviewHelpfulVote.user_id == user.id).first()
              is not None)
    return {"review_id": review_id, "helpful_count": int(count or 0),
            "helped_by_me": helped}


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
