"""Merchant dashboard, settlement statements, disputes and exports.

Connect is the only merchant payout and balance mechanism.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import merchant_finance as mf
from .. import models
from ..database import get_db
from ..deps import get_current_user
from ..permissions_line_f import (
    can_access_shop, get_shop_or_404,
)
from ..settlement_basis import BASIS_EXPLAINER
from . import admin_v2
from .admin_v2 import _settlement_out

router = APIRouter()


def _page(items, page: int, per_page: int = 20):
    total = len(items)
    return {
        "current_page": page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "total": total,
        "items": items[(page - 1) * per_page: page * per_page],
    }


def _merchant_shop_ids(db: Session, user: models.User) -> set:
    return admin_v2._merchant_admin_shop_ids(db, user)


# ---------- balance (§2.1) ----------
@router.get("/merchant/balance")
def merchant_balance(user: models.User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Live balance across all shops the caller may access."""
    shop_ids = _merchant_shop_ids(db, user)
    shops = (db.query(models.Shop).filter(models.Shop.id.in_(shop_ids))
             .order_by(models.Shop.id).all()) if shop_ids else []
    per_shop = [mf.shop_balance(db, s) for s in shops]
    return {
        "pending_settlement": sum(s["pending_settlement"] for s in per_shop),
        "withdrawable": sum(s["withdrawable"] for s in per_shop),
        "reserved": sum(s["reserved"] for s in per_shop),
        "withdrawn": sum(s["withdrawn"] for s in per_shop),
        "shops": per_shop,
    }


@router.get("/merchant/shops/{shop_id}/balance")
def shop_balance_endpoint(shop_id: int,
                          user: models.User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    shop = get_shop_or_404(db, shop_id)
    if not can_access_shop(db, user, shop):
        raise HTTPException(status_code=403, detail="沒有權限查看此商户")
    return mf.shop_balance(db, shop)


@router.get("/merchant/shops/{shop_id}/balance/entries")
def shop_balance_entries(shop_id: int, page: int = Query(1, ge=1),
                         page_size: int = Query(20, ge=1, le=100),
                         user: models.User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    shop = get_shop_or_404(db, shop_id)
    if not can_access_shop(db, user, shop):
        raise HTTPException(status_code=403, detail="沒有權限查看此商户")
    out = mf.balance_entries(db, shop, page=page, per_page=page_size)
    return {"shop_id": shop.id, "shop_name": shop.name, **out}


# ---------- dashboard (§2.4) ----------
@router.get("/merchant/shops/{shop_id}/dashboard")
def shop_dashboard_endpoint(shop_id: int,
                            days: int = Query(30, ge=1, le=90),
                            user: models.User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    shop = get_shop_or_404(db, shop_id)
    if not can_access_shop(db, user, shop):
        raise HTTPException(status_code=403, detail="沒有權限查看此商户")
    return mf.shop_dashboard(db, shop, days=days)


@router.get("/merchant/dashboard")
def merchant_dashboard(days: int = Query(30, ge=1, le=90),
                       user: models.User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """Aggregate dashboard across the caller's shops, with a per-shop
    breakdown (the Dianping merchant-home comparison)."""
    shop_ids = _merchant_shop_ids(db, user)
    shops = (db.query(models.Shop).filter(models.Shop.id.in_(shop_ids))
             .order_by(models.Shop.id).all()) if shop_ids else []
    per_shop = [mf.shop_dashboard(db, s, days=days) for s in shops]
    totals = {
        "redemptions": sum(s["totals"]["redemptions"] for s in per_shop),
        "gross_amount": sum(s["totals"]["gross_amount"] for s in per_shop),
        "commission": sum(s["totals"]["commission"] for s in per_shop),
        "net_amount": sum(s["totals"]["net_amount"] for s in per_shop),
        "claims": sum(s["totals"]["claims"] for s in per_shop),
    }
    totals["avg_order_value"] = (
        totals["gross_amount"] / totals["redemptions"]
        if totals["redemptions"] else 0)
    totals["conversion_rate"] = (
        totals["redemptions"] / totals["claims"]
        if totals["claims"] else 0)
    return {
        "period": per_shop[0]["period"] if per_shop else {
            "from": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "to": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "days": days},
        "totals": totals,
        "shops": [{"shop_id": s["shop_id"], "shop_name": s["shop_name"],
                   "totals": s["totals"]} for s in per_shop],
    }


# ---------- merchant dispute (§2.6) ----------
@router.post("/merchant/settlements/{settlement_id}/dispute")
def merchant_dispute_settlement(settlement_id: int, body: dict,
                                user: models.User = Depends(get_current_user),
                                db: Session = Depends(get_db)):
    """Merchant-side entry to the admin dispute state machine
    (admin_v2.dispute_settlement): same lock, same statuses, same
    audit log — only the caller check differs (shop access instead
    of settlements.write)."""
    s = admin_v2.lock_statement(db, settlement_id)
    if s is None:
        raise HTTPException(status_code=404, detail="找不到此結算單")
    shop = db.get(models.Shop, s.shop_id)
    if not can_access_shop(db, user, shop):
        raise HTTPException(status_code=403, detail="沒有權限查看此結算單")
    if s.status not in ("pending", "approved"):
        raise HTTPException(status_code=400, detail="此狀態的結算單不能提出爭議")
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="請填寫爭議原因")
    s.status = "disputed"
    s.dispute_reason = reason
    s.decided_by = user.id
    s.decided_at = admin_v2._now()
    admin_v2._log(db, user.id, "settlement.dispute.merchant",
                  "settlement", s.id, f"reason={reason}")
    db.commit()
    return {"message": "結算單已標記為爭議",
            "settlement": _settlement_out(db, s)}


# ---------- settlement CSV export ----------
# Export uses ?format=csv on the existing list endpoints (same pattern
# as the redemption export in routers/redemption.py); the format
# branches live in admin_v2.list_settlements / merchant_settlements /
# own_statements / get_settlement and share mf.settlements_csv /
# mf.settlement_items_csv. No separate .csv routes: one URL per list.
BASIS_EXPLAINER_TEXT = BASIS_EXPLAINER
