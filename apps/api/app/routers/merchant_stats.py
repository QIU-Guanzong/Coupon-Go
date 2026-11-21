"""Merchant stats + finance (Dianping §2).

Merchant-facing balance / dashboard / withdrawals, the admin
withdrawal review queue, the merchant settlement-dispute entry, and
CSV export for settlement lists. Amounts come from
app.merchant_finance (settlement_basis only); the payout itself lives
in app.payouts and fails closed while P1-B is not wired in.

Admin withdrawal paths live under /admin/merchant-withdrawals on
purpose: GET /admin/withdrawals and POST /admin/withdrawals/{id}
already belong to the user-cashback withdrawal review queue
(routers/cashback.py) — the two pots of money stay separate.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import merchant_finance as mf
from .. import models
from ..database import get_db
from ..deps import get_current_user
from ..models_merchant_finance import MerchantWithdrawal
from ..permissions_line_f import (
    can_access_shop, get_shop_or_404, require_perm,
)
from ..settlement_basis import BASIS_EXPLAINER
from . import admin_v2
from .admin_v2 import _settlement_out

router = APIRouter()

finance_only = require_perm("withdrawals.review")


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


# ---------- withdrawals (§2.2) ----------
@router.post("/merchant/withdrawals", status_code=201)
def create_merchant_withdrawal(body: dict,
                               user: models.User = Depends(get_current_user),
                               db: Session = Depends(get_db)):
    shop = get_shop_or_404(db, body.get("shop_id") or 0)
    if not can_access_shop(db, user, shop):
        raise HTTPException(status_code=403, detail="沒有權限查看此商户")
    w = mf.create_withdrawal(db, user, shop, body.get("settlement_id"),
                             body.get("amount"))
    return {"message": "提款申請已提交",
            "withdrawal": mf.withdrawal_out(db, w)}


@router.get("/merchant/withdrawals")
def list_own_withdrawals(status: str = Query(None),
                         page: int = Query(1, ge=1),
                         user: models.User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    shop_ids = _merchant_shop_ids(db, user)
    q = (db.query(MerchantWithdrawal)
         .filter(MerchantWithdrawal.shop_id.in_(shop_ids))
         .order_by(MerchantWithdrawal.id.desc())) if shop_ids else \
        db.query(MerchantWithdrawal).filter(False)
    if status in ("pending", "approved", "paid", "rejected",
                  "cancelled", "reversed"):
        q = q.filter(MerchantWithdrawal.status == status)
    out = _page([mf.withdrawal_out(db, w) for w in q.all()], page)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "withdrawals": out["items"]}


@router.get("/merchant/withdrawals/{withdrawal_id}")
def get_own_withdrawal(withdrawal_id: int,
                       user: models.User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    w = db.get(MerchantWithdrawal, withdrawal_id)
    if w is None:
        raise HTTPException(status_code=404, detail="找不到此提款申請")
    shop = db.get(models.Shop, w.shop_id)
    if not can_access_shop(db, user, shop):
        raise HTTPException(status_code=403, detail="沒有權限查看此提款申請")
    return {"withdrawal": mf.withdrawal_out(db, w)}


@router.post("/merchant/withdrawals/{withdrawal_id}/cancel")
def cancel_own_withdrawal(withdrawal_id: int,
                          user: models.User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    w = db.get(MerchantWithdrawal, withdrawal_id)
    if w is None:
        raise HTTPException(status_code=404, detail="找不到此提款申請")
    shop = db.get(models.Shop, w.shop_id)
    if not can_access_shop(db, user, shop):
        raise HTTPException(status_code=403, detail="沒有權限查看此提款申請")
    w = mf.cancel_withdrawal(db, w)
    return {"message": "提款申請已取消",
            "withdrawal": mf.withdrawal_out(db, w)}


@router.get("/admin/merchant-withdrawals")
def list_merchant_withdrawals(shop_id: int = Query(None),
                              status: str = Query(None),
                              page: int = Query(1, ge=1),
                              user: models.User = Depends(finance_only),
                              db: Session = Depends(get_db)):
    q = db.query(MerchantWithdrawal).order_by(
        MerchantWithdrawal.id.desc())
    if shop_id:
        q = q.filter(MerchantWithdrawal.shop_id == shop_id)
    if status in ("pending", "approved", "paid", "rejected",
                  "cancelled", "reversed"):
        q = q.filter(MerchantWithdrawal.status == status)
    out = _page([mf.withdrawal_out(db, w) for w in q.all()], page)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "withdrawals": out["items"]}


@router.post("/admin/merchant-withdrawals/{withdrawal_id}/approve")
def approve_merchant_withdrawal(withdrawal_id: int,
                                user: models.User = Depends(finance_only),
                                db: Session = Depends(get_db)):
    w = db.get(MerchantWithdrawal, withdrawal_id)
    if w is None:
        raise HTTPException(status_code=404, detail="找不到此提款申請")
    w = mf.approve_withdrawal(db, w, user)
    return {"message": "提款申請已批准",
            "withdrawal": mf.withdrawal_out(db, w)}


@router.post("/admin/merchant-withdrawals/{withdrawal_id}/reject")
def reject_merchant_withdrawal(withdrawal_id: int, body: dict,
                               user: models.User = Depends(finance_only),
                               db: Session = Depends(get_db)):
    w = db.get(MerchantWithdrawal, withdrawal_id)
    if w is None:
        raise HTTPException(status_code=404, detail="找不到此提款申請")
    w = mf.reject_withdrawal(db, w, user, body.get("reason") or "")
    return {"message": "提款申請已駁回",
            "withdrawal": mf.withdrawal_out(db, w)}


@router.post("/admin/merchant-withdrawals/{withdrawal_id}/execute-payout")
def execute_merchant_withdrawal(withdrawal_id: int,
                                user: models.User = Depends(finance_only),
                                db: Session = Depends(get_db)):
    """Execute the payout via app.payouts (P1-B Stripe Connect).
    While P1-B is not wired in this fails closed with 503 — the
    withdrawal stays approved and nothing is marked paid."""
    w = db.get(MerchantWithdrawal, withdrawal_id)
    if w is None:
        raise HTTPException(status_code=404, detail="找不到此提款申請")
    w = mf.execute_withdrawal_payout(db, w, user)
    return {"message": "提款已打款",
            "withdrawal": mf.withdrawal_out(db, w)}


@router.post("/admin/merchant-withdrawals/{withdrawal_id}/reverse")
def reverse_merchant_withdrawal(withdrawal_id: int, body: dict,
                                user: models.User = Depends(finance_only),
                                db: Session = Depends(get_db)):
    w = db.get(MerchantWithdrawal, withdrawal_id)
    if w is None:
        raise HTTPException(status_code=404, detail="找不到此提款申請")
    w = mf.reverse_withdrawal(db, w, user, body.get("reason") or "")
    return {"message": "提款已沖回",
            "withdrawal": mf.withdrawal_out(db, w)}


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
