"""Merchant dashboard, settlement statements, disputes and exports.

Connect is the only merchant payout and balance mechanism.
"""
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from .. import merchant_finance as mf
from .. import merchant_weekly_summary as weekly_summary
from .. import models
from ..database import get_db
from ..deps import get_current_user
from ..permissions_line_f import can_view_settlement_shop, get_shop_or_404
from ..qbot_budget import admit
from ..settlement_basis import BASIS_EXPLAINER
from . import admin_v2
from .admin_v2 import _settlement_out

router = APIRouter()


def _sum_money(values):
    return float(sum((Decimal(str(value)) for value in values), Decimal('0')).quantize(Decimal('0.01')))


def _page(items, page: int, per_page: int = 20):
    total = len(items)
    return {
        "current_page": page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "total": total,
        "items": items[(page - 1) * per_page: page * per_page],
    }


def _merchant_shop_ids(db: Session, user: models.User) -> set:
    if user.role == "admin":
        return {shop.id for shop in db.query(models.Shop.id)}
    return admin_v2._merchant_settlement_shop_ids(db, user)


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
        "connect_mode": mf.connect_mode(),
        "pending_settlement": _sum_money(s["pending_settlement"] for s in per_shop),
        "withdrawable": _sum_money(s["withdrawable"] for s in per_shop),
        "reserved": _sum_money(s["reserved"] for s in per_shop),
        "withdrawn": _sum_money(s["withdrawn"] for s in per_shop),
        "balance_complete": all(s["balance_complete"] for s in per_shop),
        "legacy_untracked_redemptions": sum(s["legacy_untracked_redemptions"] for s in per_shop),
        "unknown_transfer_count": sum(s["unknown_transfer_count"] for s in per_shop),
        "unknown_transfer_amount": _sum_money(s["unknown_transfer_amount"] for s in per_shop),
        "unknown_reversal_count": sum(s["unknown_reversal_count"] for s in per_shop),
        "shops": per_shop,
    }


@router.get("/merchant/shops/{shop_id}/balance")
def shop_balance_endpoint(shop_id: int,
                          user: models.User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    shop = get_shop_or_404(db, shop_id)
    if not can_view_settlement_shop(db, user, shop):
        raise HTTPException(status_code=403, detail="沒有權限查看此商户")
    return mf.shop_balance(db, shop)


@router.get("/merchant/shops/{shop_id}/balance/entries")
def shop_balance_entries(shop_id: int, page: int = Query(1, ge=1),
                         page_size: int = Query(20, ge=1, le=100),
                         user: models.User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    shop = get_shop_or_404(db, shop_id)
    if not can_view_settlement_shop(db, user, shop):
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
    if not can_view_settlement_shop(db, user, shop):
        raise HTTPException(status_code=403, detail="沒有權限查看此商户")
    return mf.shop_dashboard(db, shop, days=days)


@router.get("/merchant/shops/{shop_id}/weekly-report")
def shop_weekly_report_endpoint(shop_id: int, response: Response,
                                user: models.User = Depends(get_current_user),
                                db: Session = Depends(get_db)):
    shop = get_shop_or_404(db, shop_id)
    if not can_view_settlement_shop(db, user, shop):
        raise HTTPException(status_code=403, detail="沒有權限查看此商户")
    response.headers["Cache-Control"] = "no-store"
    report = mf.shop_weekly_report(db, shop)
    revision = None
    if weekly_summary.enabled() and weekly_summary.provider_key():
        payload = weekly_summary.aggregate_payload(report)
        if payload["facts"]:
            revision = weekly_summary.revision(shop.id, payload)
    return {**report, "summary_available": revision is not None,
            "summary_revision": revision}


@router.post("/merchant/shops/{shop_id}/weekly-report/summary")
def shop_weekly_summary_endpoint(shop_id: int, body: weekly_summary.WeeklySummaryRequest,
                                 response: Response,
                                 user: models.User = Depends(get_current_user),
                                 db: Session = Depends(get_db)):
    shop = get_shop_or_404(db, shop_id)
    if not can_view_settlement_shop(db, user, shop):
        raise HTTPException(status_code=403, detail="沒有權限查看此商户")
    response.headers["Cache-Control"] = "no-store"
    key = weekly_summary.provider_key() if weekly_summary.enabled() else ""
    if not key:
        return {"summary": None}
    payload = weekly_summary.aggregate_payload(mf.shop_weekly_report(db, shop))
    user_id, authorized_shop_id = user.id, shop.id
    db.close()
    if not payload["facts"] or body.summary_revision != weekly_summary.revision(authorized_shop_id, payload):
        return {"summary": None}
    try:
        # Use the existing assistant allowance, rather than opening another
        # independent per-account or global provider quota.
        admit(f"user:{user_id}")
    except HTTPException:
        return {"summary": None}
    return {"summary": weekly_summary.summarize(payload, body.language, key=key)}


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
        "redeemed_orders": sum(s["totals"]["redeemed_orders"] for s in per_shop),
        "gross_amount": _sum_money(s["totals"]["gross_amount"] for s in per_shop),
        "commission": _sum_money(s["totals"]["commission"] for s in per_shop),
        "net_amount": _sum_money(s["totals"]["net_amount"] for s in per_shop),
        "claims": sum(s["totals"]["claims"] for s in per_shop),
        "redeemed_claims": sum(s["totals"]["redeemed_claims"] for s in per_shop),
    }
    totals["avg_order_value"] = (
        totals["gross_amount"] / totals["redeemed_orders"]
        if totals["redeemed_orders"] else 0)
    totals["conversion_rate"] = (
        totals["redeemed_claims"] / totals["claims"]
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
    if not can_view_settlement_shop(db, user, shop):
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
