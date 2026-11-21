"""Merchant dashboard and monthly statements.

Payout balances come exclusively from the Connect ledger; settlement
statements are accounting records and do not authorize a second payout.
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
from .settlement_basis import settlement_basis, settlement_basis_note
from .settlement_ledger import CENT, cash_totals, lock_shop, lock_statement, redemption_commission_rate

logger = logging.getLogger(__name__)

DEFAULT_COMMISSION_RATE = Decimal("0.10")

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


def shop_balance(db: Session, shop: models.Shop) -> dict:
    """Connect is the single source of truth for payout balances."""
    from .connect_service import merchant_balance
    balance = merchant_balance(db, shop.id)
    return {
        "shop_id": shop.id, "shop_name": shop.name,
        "pending_settlement": _money(balance["pending"]),
        "withdrawable": _money(balance["available"]),
        "reserved": _money(balance["processing"]),
        "withdrawn": _money(balance["paid_out"]),
        "commission_rate": _money(effective_commission_rate(db, shop.id)),
    }


def balance_entries(db: Session, shop: models.Shop,
                    page: int = 1, per_page: int = 20) -> dict:
    """Redemption detail with the Connect transfer state, if available."""
    rate = effective_commission_rate(db, shop.id)
    from .models_connect import ConnectLedgerEntry
    transfers = {e.redemption_id: e for e in db.query(ConnectLedgerEntry).filter_by(
        shop_id=shop.id, kind="transfer").all()}
    def transfer_state(redemption_id):
        entry = transfers.get(redemption_id)
        if not entry:
            return "unavailable"
        return "reversal_pending" if entry.pending_reversal else entry.status

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
        state = transfer_state(red.id)
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
        fee = (amount * redemption_commission_rate(red, rate)).quantize(CENT)
        rows.append({
            "redemption_id": red.id, "coupon_id": red.coupon_id,
            "coupon_name": coupon.coupon_name if coupon else "",
            "scanned_at": red.scanned_at,
            "period": red.scanned_at.strftime("%Y-%m"),
            "basis_amount": _money(amount),
            "basis_note": settlement_basis_note(db, coupon, redemption=red),
            "commission": _money(fee),
            "net_amount": _money(amount - fee),
            "state": transfer_state(red.id),
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
        fee = (amount * redemption_commission_rate(red, rate)).quantize(CENT)
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
    commission = sum(((amount * redemption_commission_rate(r, rate)).quantize(CENT)
                      for r, amount in items), Decimal("0"))
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
            commission=(amount * redemption_commission_rate(r, rate)).quantize(CENT)))
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


