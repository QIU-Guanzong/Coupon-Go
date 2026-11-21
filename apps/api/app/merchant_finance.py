"""Merchant finance service (Dianping §2: balance, withdrawals,
dashboard, monthly settlement generation).

Balance semantics (settled with the GAP §2.1 note — the "when is
money withdrawable" rule is stated here, not guessed in code):
- pending_settlement （待结算）: net value of redemptions not yet in
  any settlement, plus the outstanding net of settlements still
  pending / disputed — minus any amount already reserved by a
  withdrawal request against them.
- withdrawable （可提取）: outstanding net of APPROVED settlements,
  minus reservations. A settlement becomes withdrawable when finance
  approves it; there is no extra time-based hold.
- reserved: amounts held by pending / approved withdrawal requests
  (not yet paid, so not yet in SettlementTransfer).
- withdrawn （已提取）: net paid out per SettlementTransfer
  (cash_totals — payments minus recoveries), the same number the
  settlement view already shows as transferred_net.

Every amount goes through settlement_basis() — the one shared basis
(package = Order.paid_amount, price coupon = discount_price, cash /
free / pure-threshold = 0, refunded order = never settles). Nothing
here invents a second basis.
"""
import csv
import io
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models
from .models_line_f import (
    MerchantCommissionRate, Settlement, SettlementItem,
    SettlementReversal, SettlementTransfer,
)
from .models_merchant_finance import MerchantWithdrawal
from .settlement_basis import settlement_basis, settlement_basis_note
from .settlement_ledger import CENT, cash_totals, lock_shop, lock_statement
from . import payouts

logger = logging.getLogger(__name__)

DEFAULT_COMMISSION_RATE = Decimal("0.10")

WITHDRAWABLE_STATUSES = ("pending", "approved")
# A withdrawal in one of these states holds its amount out of the
# withdrawable balance until it is paid (then the transfer covers it)
# or rejected / cancelled / reversed (then the hold is released).
RESERVING_STATUSES = ("pending", "approved")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _money(value) -> float:
    return float(value) if value is not None else 0.0


def _period_bounds(period: str):
    year, month = int(period[:4]), int(period[5:7])
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)
    return start, end


def _previous_period(now: datetime = None) -> str:
    now = now or datetime.now(timezone.utc)
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def effective_commission_rate(db: Session, shop_id: int) -> Decimal:
    """The shop's configured rate, else the platform default 10% —
    the same source generate_settlement reads (admin_v2)."""
    row = (db.query(MerchantCommissionRate)
           .filter(MerchantCommissionRate.shop_id == shop_id).first())
    return Decimal(str(row.rate)) if row is not None else DEFAULT_COMMISSION_RATE


def reserved_amount(db: Session, settlement_id: int) -> Decimal:
    """Amount held by not-yet-paid withdrawals against a settlement."""
    total = (db.query(func.coalesce(func.sum(MerchantWithdrawal.amount), 0))
             .filter(MerchantWithdrawal.settlement_id == settlement_id,
                     MerchantWithdrawal.status.in_(RESERVING_STATUSES))
             .scalar())
    return Decimal(str(total))


def settlement_available(db: Session, s: Settlement) -> Decimal:
    """What a new withdrawal may still ask for on this settlement."""
    _, outstanding = cash_totals(db, s)
    return outstanding - reserved_amount(db, s.id)


# ---------- balance ----------
def shop_balance(db: Session, shop: models.Shop) -> dict:
    rate = effective_commission_rate(db, shop.id)
    settled_ids = {r for (r,) in
                   db.query(SettlementItem.redemption_id)
                   .join(Settlement, Settlement.id == SettlementItem.settlement_id)
                   .filter(Settlement.shop_id == shop.id).all()}
    pending = Decimal("0")
    for r in (db.query(models.Redemption)
              .filter(models.Redemption.shop_id == shop.id,
                      models.Redemption.revoked_at.is_(None)).all()):
        if r.id in settled_ids:
            continue
        amount = settlement_basis(db, redemption=r)
        if amount is None:
            continue  # refunded order: never settles, never pending
        pending += amount - (amount * rate).quantize(CENT)
    withdrawable = Decimal("0")
    withdrawn = Decimal("0")
    reserved = Decimal("0")
    for s in db.query(Settlement).filter(Settlement.shop_id == shop.id).all():
        transferred, outstanding = cash_totals(db, s)
        withdrawn += transferred
        res = reserved_amount(db, s.id)
        reserved += res
        if s.status in ("pending", "disputed"):
            pending += outstanding - res
        elif s.status == "approved":
            withdrawable += outstanding - res
        # paid: outstanding is 0 by construction (full transfer).
    return {
        "shop_id": shop.id,
        "shop_name": shop.name,
        "pending_settlement": _money(pending),
        "withdrawable": _money(max(Decimal("0"), withdrawable)),
        "reserved": _money(reserved),
        "withdrawn": _money(withdrawn),
        "commission_rate": _money(rate),
    }


def balance_entries(db: Session, shop: models.Shop,
                    page: int = 1, per_page: int = 20) -> dict:
    """Per-redemption balance detail: which coupon, when scanned, the
    basis, the commission, the net — and which state the money is in
    (pending_settlement / withdrawable / withdrawn / reversed)."""
    rate = effective_commission_rate(db, shop.id)
    rows = []
    settled = (db.query(SettlementItem, Settlement, models.Redemption)
               .join(Settlement, Settlement.id == SettlementItem.settlement_id)
               .join(models.Redemption,
                     models.Redemption.id == SettlementItem.redemption_id)
               .filter(Settlement.shop_id == shop.id)
               .order_by(models.Redemption.scanned_at.desc()).all())
    reversals = {rev.item_id: rev for rev in
                 db.query(SettlementReversal)
                 .join(SettlementItem,
                       SettlementItem.id == SettlementReversal.item_id)
                 .join(Settlement, Settlement.id == SettlementItem.settlement_id)
                 .filter(Settlement.shop_id == shop.id).all()}
    seen_redemptions = set()
    for item, s, red in settled:
        seen_redemptions.add(red.id)
        rev = reversals.get(item.id)
        state = ("reversed" if rev is not None else
                 "pending_settlement" if s.status in ("pending", "disputed")
                 else "withdrawable" if s.status == "approved"
                 else "withdrawn")
        coupon = db.get(models.Coupon, red.coupon_id)
        rows.append({
            "redemption_id": red.id, "coupon_id": red.coupon_id,
            "coupon_name": coupon.coupon_name if coupon else "",
            "scanned_at": red.scanned_at,
            "period": red.scanned_at.strftime("%Y-%m"),
            "basis_amount": _money(item.amount),
            "basis_note": settlement_basis_note(db, coupon, redemption=red),
            "commission": _money(item.commission),
            "net_amount": _money(item.amount - item.commission),
            "state": state,
            "settlement_id": s.id,
            "reversed": rev is not None,
        })
    unsettled = (db.query(models.Redemption)
                 .filter(models.Redemption.shop_id == shop.id,
                         models.Redemption.revoked_at.is_(None))
                 .order_by(models.Redemption.scanned_at.desc()).all())
    for red in unsettled:
        if red.id in seen_redemptions:
            continue
        amount = settlement_basis(db, redemption=red)
        if amount is None:
            continue
        coupon = db.get(models.Coupon, red.coupon_id)
        fee = (amount * rate).quantize(CENT)
        rows.append({
            "redemption_id": red.id, "coupon_id": red.coupon_id,
            "coupon_name": coupon.coupon_name if coupon else "",
            "scanned_at": red.scanned_at,
            "period": red.scanned_at.strftime("%Y-%m"),
            "basis_amount": _money(amount),
            "basis_note": settlement_basis_note(db, coupon, redemption=red),
            "commission": _money(fee),
            "net_amount": _money(amount - fee),
            "state": "pending_settlement",
            "settlement_id": None,
            "reversed": False,
        })
    rows.sort(key=lambda e: e["scanned_at"] or datetime.min, reverse=True)
    total = len(rows)
    return {
        "total": total,
        "current_page": page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "entries": rows[(page - 1) * per_page: page * per_page],
    }


# ---------- dashboard (§2.4) ----------
def shop_dashboard(db: Session, shop: models.Shop, days: int = 30,
                   end: datetime = None) -> dict:
    """Redemption trend (daily), revenue, average order value and
    per-coupon conversion for one shop. All amounts are settlement
    basis amounts — the same numbers the merchant will be settled on.
    """
    end_date = (end or datetime.now(timezone.utc)).date()
    start_date = end_date - timedelta(days=days - 1)
    start = datetime(start_date.year, start_date.month, start_date.day)
    stop = datetime(end_date.year, end_date.month, end_date.day) + timedelta(days=1)
    rate = effective_commission_rate(db, shop.id)
    redemptions = (db.query(models.Redemption)
                   .filter(models.Redemption.shop_id == shop.id,
                           models.Redemption.scanned_at >= start,
                           models.Redemption.scanned_at < stop,
                           models.Redemption.revoked_at.is_(None))
                   .order_by(models.Redemption.scanned_at).all())
    by_day: dict[str, dict] = {}
    for i in range(days):
        d = (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
        by_day[d] = {"date": d, "redemptions": 0, "gross_amount": Decimal("0"),
                     "net_amount": Decimal("0")}
    by_coupon: dict[int, dict] = {}
    gross = Decimal("0")
    net = Decimal("0")
    for red in redemptions:
        amount = settlement_basis(db, redemption=red)
        if amount is None:
            continue
        fee = (amount * rate).quantize(CENT)
        gross += amount
        net += amount - fee
        day = red.scanned_at.strftime("%Y-%m-%d")
        bucket = by_day.get(day)
        if bucket is not None:
            bucket["redemptions"] += 1
            bucket["gross_amount"] += amount
            bucket["net_amount"] += amount - fee
        row = by_coupon.setdefault(red.coupon_id, {
            "coupon_id": red.coupon_id, "claims": 0, "redemptions": 0,
            "expired": 0, "gross_amount": Decimal("0")})
        row["redemptions"] += 1
        row["gross_amount"] += amount
    coupon_ids = [c.id for c in db.query(models.Coupon.id)
                  .filter(models.Coupon.shop_id == shop.id).all()]
    claims = (db.query(models.WalletItem.coupon_id,
                       func.count(models.WalletItem.id))
              .filter(models.WalletItem.coupon_id.in_(coupon_ids),
                      models.WalletItem.claimed_at >= start,
                      models.WalletItem.claimed_at < stop)
              .group_by(models.WalletItem.coupon_id).all()) if coupon_ids else []
    for coupon_id, count in claims:
        row = by_coupon.setdefault(coupon_id, {
            "coupon_id": coupon_id, "claims": 0, "redemptions": 0,
            "expired": 0, "gross_amount": Decimal("0")})
        row["claims"] = count
    expired = (db.query(models.WalletItem.coupon_id,
                        func.count(models.WalletItem.id))
               .filter(models.WalletItem.coupon_id.in_(coupon_ids),
                       models.WalletItem.claimed_at >= start,
                       models.WalletItem.claimed_at < stop,
                       models.WalletItem.status == 3)
               .group_by(models.WalletItem.coupon_id).all()) if coupon_ids else []
    for coupon_id, count in expired:
        row = by_coupon.setdefault(coupon_id, {
            "coupon_id": coupon_id, "claims": 0, "redemptions": 0,
            "expired": 0, "gross_amount": Decimal("0")})
        row["expired"] = count
    coupons = []
    for coupon_id, row in sorted(by_coupon.items()):
        coupon = db.get(models.Coupon, coupon_id)
        claims_n = row["claims"]
        coupons.append({
            "coupon_id": coupon_id,
            "coupon_name": coupon.coupon_name if coupon else "",
            "claims": claims_n,
            "redemptions": row["redemptions"],
            "expired": row["expired"],
            "conversion_rate": (row["redemptions"] / claims_n
                                if claims_n else 0),
            "gross_amount": _money(row["gross_amount"]),
        })
    count = sum(b["redemptions"] for b in by_day.values())
    trend = [{"date": b["date"], "redemptions": b["redemptions"],
              "gross_amount": _money(b["gross_amount"]),
              "net_amount": _money(b["net_amount"])}
             for b in by_day.values()]
    return {
        "shop_id": shop.id,
        "shop_name": shop.name,
        "period": {"from": start_date.strftime("%Y-%m-%d"),
                   "to": end_date.strftime("%Y-%m-%d"), "days": days},
        "totals": {
            "redemptions": count,
            "gross_amount": _money(gross),
            "commission": _money(gross - net),
            "net_amount": _money(net),
            "avg_order_value": _money(gross / count) if count else 0,
            "claims": sum(c["claims"] for c in coupons),
            "conversion_rate": (
                count / sum(c["claims"] for c in coupons)
                if sum(c["claims"] for c in coupons) else 0),
        },
        "trend": trend,
        "coupons": coupons,
    }


# ---------- settlement generation (shared core, §2.3) ----------
def generate_settlement_for_shop(db: Session, shop: models.Shop,
                                 period: str, actor_id=None,
                                 commission_rate=None):
    """The one generation implementation, shared by the admin manual
    endpoint (POST /admin/settlements/generate) and the monthly
    scheduler sweep. Same checks, same basis, same 409 on duplicates.

    Returns (settlement, [(redemption, amount), ...]). Commits.
    Raises HTTPException(400) on a rate mismatch, (409) when the
    settlement already exists (including a concurrent insert racing
    past the pre-check).
    """
    lock_shop(db, shop.id)
    rate = effective_commission_rate(db, shop.id)
    if (commission_rate is not None
            and Decimal(str(commission_rate)) != rate):
        raise HTTPException(
            status_code=400,
            detail="传入的佣金率与生效费率不一致（生效费率 %s）" % rate)
    existing = (db.query(Settlement)
                .filter(Settlement.shop_id == shop.id,
                        Settlement.period == period).first())
    if existing is not None:
        raise HTTPException(status_code=409, detail="此商户此結算週期已有結算單")
    start, end = _period_bounds(period)
    settled_ids = {i.redemption_id for i in
                   db.query(SettlementItem.redemption_id).all()}
    redemptions = (db.query(models.Redemption)
                   .filter(models.Redemption.shop_id == shop.id,
                           models.Redemption.scanned_at >= start,
                           models.Redemption.scanned_at < end,
                           models.Redemption.revoked_at.is_(None))
                   .order_by(models.Redemption.id).all())
    fresh = [r for r in redemptions if r.id not in settled_ids]
    gross = Decimal("0")
    items = []
    for r in fresh:
        coupon = db.query(models.Coupon).filter(
            models.Coupon.id == r.coupon_id).first()
        amount = settlement_basis(db, coupon, redemption=r)
        if amount is None:
            continue  # refunded order: never settles
        gross += amount
        items.append((r, amount))
    commission = sum(((amount * rate).quantize(CENT)
                      for _, amount in items), Decimal("0"))
    net = gross - commission
    settlement = Settlement(
        shop_id=shop.id, period=period, period_start=start,
        period_end=end, redemption_count=len(items), gross_amount=gross,
        commission_rate=rate, commission=commission, net_amount=net,
        status="pending", generated_by=actor_id)
    db.add(settlement)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="此商户此結算週期已有結算單（重複生成衝突），請查詢已有結算單")
    for r, amount in items:
        db.add(SettlementItem(
            settlement_id=settlement.id, redemption_id=r.id,
            coupon_id=r.coupon_id, amount=amount,
            commission=(amount * rate).quantize(Decimal("0.01"))))
    if actor_id is not None:
        from .models_line_b import AdminAuditLog
        configured = (db.query(MerchantCommissionRate)
                      .filter(MerchantCommissionRate.shop_id == shop.id)
                      .first())
        db.add(AdminAuditLog(admin_id=actor_id, action="settlement.generate",
                             target_type="settlement", target_id=settlement.id,
                             detail=f"shop_id={shop.id}; period={period}; "
                             f"redemptions={len(items)}; gross={gross}; rate={rate}"
                             + ("; 无商户费率配置，使用默认 10%"
                                if configured is None else "; 使用商户配置费率")))
    db.commit()
    db.refresh(settlement)
    return settlement, items


def sweep_monthly_settlements(db: Session, period: str = None) -> dict:
    """Generate `period` (default: last month, UTC) settlements for
    every shop with redemptions in that period. One bad shop never
    blocks the rest; a second run is a no-op (409 -> skipped).

    This is the sweep the monthly-settlements scheduler job runs; the
    admin manual endpoint stays as the backfill path.
    """
    period = period or _previous_period()
    start, end = _period_bounds(period)
    shop_ids = [s for (s,) in
                db.query(models.Redemption.shop_id)
                .filter(models.Redemption.scanned_at >= start,
                        models.Redemption.scanned_at < end,
                        models.Redemption.revoked_at.is_(None))
                .distinct().order_by(models.Redemption.shop_id).all()]
    generated = skipped = failed = 0
    failures = []
    for shop_id in shop_ids:
        shop = db.get(models.Shop, shop_id)
        if shop is None:
            continue
        try:
            generate_settlement_for_shop(db, shop, period, actor_id=None)
            generated += 1
        except HTTPException as e:
            db.rollback()
            if e.status_code == 409:
                skipped += 1
            else:
                failed += 1
                failures.append({"shop_id": shop_id, "error": e.detail})
                logger.exception("monthly settlement failed shop=%s "
                                 "period=%s", shop_id, period)
        except Exception:
            db.rollback()
            failed += 1
            failures.append({"shop_id": shop_id, "error": "job failed; see server log"})
            logger.exception("monthly settlement failed shop=%s period=%s",
                             shop_id, period)
    return {"period": period, "generated": generated, "skipped": skipped,
            "failed": failed, "failures": failures}


# ---------- CSV export ----------
def settlements_csv(rows) -> "PlainTextResponse":
    """Settlement-list CSV (same ?format=csv pattern as the redemption
    export). `rows` are _settlement_out dicts."""
    from fastapi.responses import PlainTextResponse
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "shop_id", "shop_name", "period", "redemption_count",
                "gross_amount", "commission", "net_amount",
                "transferred_net", "outstanding_net", "status"])
    for s in rows:
        w.writerow([s["id"], s["shop_id"], s["shop_name"], s["period"],
                    s["redemption_count"], s["gross_amount"], s["commission"],
                    s["net_amount"], s["transferred_net"],
                    s["outstanding_net"], s["status"]])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv; charset=utf-8")


def settlement_items_csv(settlement_id: int, period: str, items) -> "PlainTextResponse":
    """Per-line CSV for one settlement (basis_note included, §2.5)."""
    from fastapi.responses import PlainTextResponse
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["settlement_id", "period", "redemption_id", "coupon_id",
                "coupon_name", "amount", "commission", "basis_note"])
    for item in items:
        w.writerow([settlement_id, period, item["redemption_id"],
                    item["coupon_id"], item.get("coupon_name", ""),
                    item["amount"], item["commission"],
                    item.get("basis_note", "")])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv; charset=utf-8")


# ---------- withdrawals ----------
def withdrawal_out(db: Session, w: MerchantWithdrawal) -> dict:
    shop = db.get(models.Shop, w.shop_id)
    s = db.get(Settlement, w.settlement_id)
    return {
        "id": w.id, "shop_id": w.shop_id,
        "shop_name": shop.name if shop else "",
        "settlement_id": w.settlement_id,
        "settlement_period": s.period if s else "",
        "user_id": w.user_id,
        "amount": _money(w.amount), "status": w.status,
        "payout_reference": w.payout_reference or "",
        "payout_provider": w.payout_provider or "",
        "reject_reason": w.reject_reason or "",
        "decided_by": w.decided_by, "decided_at": w.decided_at,
        "paid_at": w.paid_at, "created_at": w.created_at,
        "updated_at": w.updated_at,
    }


def _lock_withdrawal(db: Session, withdrawal_id: int):
    if db.get_bind().dialect.name == "sqlite":
        db.query(MerchantWithdrawal).filter_by(id=withdrawal_id).update(
            {"status": MerchantWithdrawal.status,
             "updated_at": MerchantWithdrawal.updated_at},
            synchronize_session=False)
    return (db.query(MerchantWithdrawal).filter_by(id=withdrawal_id)
            .with_for_update().populate_existing().first())


def create_withdrawal(db: Session, user: models.User, shop: models.Shop,
                      settlement_id: int, amount) -> MerchantWithdrawal:
    """Merchant files a withdrawal against an APPROVED settlement."""
    s = db.get(Settlement, settlement_id)
    if s is None:
        raise HTTPException(status_code=404, detail="找不到此結算單")
    if s.shop_id != shop.id:
        raise HTTPException(status_code=400, detail="結算單不屬於此商户")
    if s.status != "approved":
        raise HTTPException(status_code=400,
                            detail="只有已確認的結算單可以提款")
    try:
        amount = Decimal(str(amount))
    except Exception:
        raise HTTPException(status_code=400, detail="提款金額無效")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="提款金額須大於 0")
    if amount != amount.quantize(Decimal("0.01")):
        raise HTTPException(status_code=400, detail="提款金額最多兩位小數")
    available = settlement_available(db, s)
    if amount > available:
        raise HTTPException(
            status_code=400,
            detail="提款金額超過可提餘額（可提 %s）" % available)
    w = MerchantWithdrawal(shop_id=shop.id, settlement_id=s.id,
                           user_id=user.id, amount=amount, status="pending")
    db.add(w)
    db.commit()
    db.refresh(w)
    return w


def approve_withdrawal(db: Session, w: MerchantWithdrawal,
                       admin: models.User) -> MerchantWithdrawal:
    w = _lock_withdrawal(db, w.id)
    if w is None:
        raise HTTPException(status_code=404, detail="找不到此提款申請")
    if w.status != "pending":
        raise HTTPException(status_code=400, detail="只有待審核的提款可以批准")
    s = lock_statement(db, w.settlement_id)
    if s is None or s.status != "approved":
        raise HTTPException(status_code=400,
                            detail="結算單狀態已變更，提款無法批准")
    w.status = "approved"
    w.decided_by = admin.id
    w.decided_at = _now()
    db.commit()
    db.refresh(w)
    return w


def reject_withdrawal(db: Session, w: MerchantWithdrawal,
                      admin: models.User, reason: str) -> MerchantWithdrawal:
    w = _lock_withdrawal(db, w.id)
    if w is None:
        raise HTTPException(status_code=404, detail="找不到此提款申請")
    if w.status != "pending":
        raise HTTPException(status_code=400, detail="只有待審核的提款可以駁回")
    reason = (reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="請填寫駁回原因")
    w.status = "rejected"
    w.reject_reason = reason
    w.decided_by = admin.id
    w.decided_at = _now()
    db.commit()
    db.refresh(w)
    return w


def cancel_withdrawal(db: Session, w: MerchantWithdrawal) -> MerchantWithdrawal:
    w = _lock_withdrawal(db, w.id)
    if w is None:
        raise HTTPException(status_code=404, detail="找不到此提款申請")
    if w.status != "pending":
        raise HTTPException(status_code=400, detail="只有待審核的提款可以取消")
    w.status = "cancelled"
    db.commit()
    db.refresh(w)
    return w


def execute_withdrawal_payout(db: Session, w: MerchantWithdrawal,
                              admin: models.User) -> MerchantWithdrawal:
    """Run the payout through app.payouts (P1-B). Fail closed: while
    no provider is wired in, this raises 503 and NOTHING is marked
    paid — no transfer row, no status change, no reservation release.
    """
    w = _lock_withdrawal(db, w.id)
    if w is None:
        raise HTTPException(status_code=404, detail="找不到此提款申請")
    if w.status != "approved":
        raise HTTPException(status_code=400, detail="只有已批准的提款可以打款")
    s = lock_statement(db, w.settlement_id)
    if s is None or s.status != "approved":
        raise HTTPException(status_code=400,
                            detail="結算單狀態已變更，提款無法打款")
    try:
        result = payouts.execute_merchant_payout(db, w)
    except payouts.PayoutNotConfigured as e:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(e))
    except payouts.PayoutFailed as e:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(e))
    reference = (result or {}).get("payout_reference") or ""
    if not reference:
        db.rollback()
        raise HTTPException(status_code=502, detail="打款未返回付款憑證")
    w.status = "paid"
    w.payout_reference = reference
    w.payout_provider = (result or {}).get("provider") or ""
    w.paid_at = _now()
    w.decided_by = admin.id
    w.decided_at = _now()
    db.add(SettlementTransfer(settlement_id=s.id, amount=w.amount,
                              kind="payment", reference=reference,
                              actor_id=admin.id))
    transferred, _ = cash_totals(db, s)
    if transferred >= s.net_amount:
        s.status = "paid"
        s.paid_at = _now()
        s.decided_by = admin.id
        s.decided_at = _now()
    db.commit()
    db.refresh(w)
    return w


def reverse_withdrawal(db: Session, w: MerchantWithdrawal,
                       admin: models.User, reason: str) -> MerchantWithdrawal:
    """The payout bounced after the fact: reverse it. A negative
    transfer restores the settlement's outstanding balance and the
    settlement goes back to approved (withdrawable again)."""
    w = _lock_withdrawal(db, w.id)
    if w is None:
        raise HTTPException(status_code=404, detail="找不到此提款申請")
    if w.status != "paid":
        raise HTTPException(status_code=400, detail="只有已打款的提款可以沖回")
    reason = (reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="請填寫沖回原因")
    s = lock_statement(db, w.settlement_id)
    reference = f"reversal-{w.payout_reference or w.id}"
    prior = (db.query(SettlementTransfer)
             .filter_by(settlement_id=s.id, reference=reference).first())
    if prior is None:
        db.add(SettlementTransfer(settlement_id=s.id, amount=-w.amount,
                                  kind="recovery", reference=reference,
                                  actor_id=admin.id))
    w.status = "reversed"
    w.reject_reason = reason
    w.decided_by = admin.id
    w.decided_at = _now()
    transferred, _ = cash_totals(db, s)
    if s.status == "paid" and transferred < s.net_amount:
        s.status = "approved"
        s.paid_at = None
    db.commit()
    db.refresh(w)
    return w
