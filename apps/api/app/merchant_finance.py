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
from sqlalchemy import extract, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config, models
from .models_line_f import (
    MerchantCommissionRate, Settlement, SettlementItem,
    SettlementReversal, SettlementTransfer,
)
from .models_packages import Order
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


def connect_mode() -> str:
    """Describe the configured ledger provider without claiming a live transfer."""
    if not config.connect_enabled().get('enabled'):
        return 'unavailable'
    key = config.STRIPE_SECRET_KEY or ''
    if key.startswith('sk_test_'):
        return 'stripe_test'
    if not key and config.APP_ENV == 'dev':
        return 'simulated'
    return 'unavailable'


def shop_balance(db: Session, shop: models.Shop) -> dict:
    """Connect is the single source of truth for payout balances."""
    from .connect_service import merchant_balance
    from .models_connect import ConnectLedgerEntry
    balance = merchant_balance(db, shop.id)
    uncertain_transfers = (db.query(ConnectLedgerEntry)
                           .filter_by(shop_id=shop.id, kind='transfer', status='transfer_unknown')
                           .all())
    uncertain_reversals = (db.query(ConnectLedgerEntry)
                           .filter_by(shop_id=shop.id, kind='transfer', pending_reversal=True,
                                      note='reversal_result_unknown').count())
    recorded = db.query(ConnectLedgerEntry.redemption_id).filter(
        ConnectLedgerEntry.shop_id == shop.id, ConnectLedgerEntry.kind == "transfer",
        ConnectLedgerEntry.redemption_id.is_not(None))
    candidates = db.query(models.Redemption).filter(
        models.Redemption.shop_id == shop.id, models.Redemption.revoked_at.is_(None),
        ~models.Redemption.id.in_(recorded)).all()
    legacy_count = 0
    for red in candidates:
        item = db.get(models.WalletItem, red.wallet_item_id)
        coupon = db.get(models.Coupon, red.coupon_id)
        if item is None:
            legacy_count += 1
            continue
        if coupon is None:
            legacy_count += 1
            continue
        if item.order_id is None and (coupon.coupon_type or "").lower() != "price":
            continue
        statement = (db.query(Settlement).join(SettlementItem, SettlementItem.settlement_id == Settlement.id)
                     .filter(SettlementItem.redemption_id == red.id).first())
        if statement is not None and statement.status == "paid" and cash_totals(db, statement)[1] == 0:
            continue  # prior manual payment fully recorded
        try:
            basis = settlement_basis(db, coupon=coupon, redemption=red)
        except HTTPException:
            legacy_count += 1  # unknown historical allocation needs review
            continue
        if basis is not None and basis > 0:
            legacy_count += 1
    return {
        "shop_id": shop.id, "shop_name": shop.name,
        "connect_mode": connect_mode(),
        "pending_settlement": _money(balance["pending"]),
        "withdrawable": _money(balance["available"]),
        "reserved": _money(balance["processing"]),
        "withdrawn": _money(balance["paid_out"]),
        "commission_rate": float(effective_commission_rate(db, shop.id)),
        "balance_complete": legacy_count == 0,
        "legacy_untracked_redemptions": legacy_count,
        "unknown_transfer_count": len(uncertain_transfers),
        "unknown_transfer_amount": _money(sum((Decimal(str(e.net_amount)) for e in uncertain_transfers), Decimal('0'))),
        "unknown_reversal_count": uncertain_reversals,
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
        if entry.pending_reversal:
            return "reversal_unknown" if entry.note == "reversal_result_unknown" else "reversal_pending"
        return entry.status

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
            "reversed": rev is not None or red.revoked_at is not None,
        })
    unsettled = (db.query(models.Redemption)
                 .filter(models.Redemption.shop_id == shop.id)
                 .order_by(models.Redemption.scanned_at.desc()).all())
    for red in unsettled:
        if red.id in seen_redemptions:
            continue
        transfer = transfers.get(red.id)
        if red.revoked_at and transfer is None:
            continue
        amount = (Decimal(str(transfer.gross_amount)) if red.revoked_at and transfer
                  else settlement_basis(db, redemption=red))
        if amount is None:
            continue
        coupon = db.get(models.Coupon, red.coupon_id)
        fee = (Decimal(str(transfer.commission)) if red.revoked_at and transfer
               else (amount * redemption_commission_rate(red, rate)).quantize(CENT))
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
            "reversed": red.revoked_at is not None,
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
    wallet_ids = {r.wallet_item_id for r in redemptions if r.wallet_item_id}
    wallets = {item.id: item for item in db.query(models.WalletItem)
               .filter(models.WalletItem.id.in_(wallet_ids)).all()} if wallet_ids else {}
    order_keys = set()
    redeemed_claim_ids = set()
    gross = Decimal("0")
    net = Decimal("0")
    for red in redemptions:
        item = wallets.get(red.wallet_item_id)
        amount = settlement_basis(db, redemption=red, wallet_item=item)
        if amount is None:
            continue
        order_keys.add(('order', item.order_id) if item and item.order_id
                       else ('voucher', red.wallet_item_id or red.id))
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
            "redeemed_claims": 0, "expired": 0, "gross_amount": Decimal("0")})
        row["redemptions"] += 1
        if (item and item.claimed_at and start <= item.claimed_at < stop
                and item.id not in redeemed_claim_ids):
            row["redeemed_claims"] += 1
            redeemed_claim_ids.add(item.id)
        row["gross_amount"] += amount
    coupon_ids = [c.id for c in db.query(models.Coupon.id)
                  .filter(models.Coupon.shop_id == shop.id).all()]
    claims = (db.query(models.WalletItem.coupon_id,
                       func.count(models.WalletItem.id))
              .filter(models.WalletItem.coupon_id.in_(coupon_ids),
                      models.WalletItem.source != "gift",
                      models.WalletItem.claimed_at >= start,
                      models.WalletItem.claimed_at < stop)
              .group_by(models.WalletItem.coupon_id).all()) if coupon_ids else []
    for coupon_id, count in claims:
        row = by_coupon.setdefault(coupon_id, {
            "coupon_id": coupon_id, "claims": 0, "redemptions": 0,
            "redeemed_claims": 0, "expired": 0, "gross_amount": Decimal("0")})
        row["claims"] = count
    expired = (db.query(models.WalletItem.coupon_id,
                        func.count(models.WalletItem.id))
               .filter(models.WalletItem.coupon_id.in_(coupon_ids),
                       models.WalletItem.claimed_at >= start,
                       models.WalletItem.claimed_at < stop,
                       models.WalletItem.status == 3,
                       ~db.query(models.CouponGift).filter(
                           models.CouponGift.sender_wallet_item_id == models.WalletItem.id,
                           models.CouponGift.recipient_wallet_item_id.is_not(None)).exists())
               .group_by(models.WalletItem.coupon_id).all()) if coupon_ids else []
    for coupon_id, count in expired:
        row = by_coupon.setdefault(coupon_id, {
            "coupon_id": coupon_id, "claims": 0, "redemptions": 0,
            "redeemed_claims": 0, "expired": 0, "gross_amount": Decimal("0")})
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
            "redeemed_claims": row["redeemed_claims"],
            "expired": row["expired"],
            "conversion_rate": (row["redeemed_claims"] / claims_n
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
            "redeemed_orders": len(order_keys),
            "gross_amount": _money(gross),
            "commission": _money(gross - net),
            "net_amount": _money(net),
            "avg_order_value": _money(gross / len(order_keys)) if order_keys else 0,
            "claims": sum(c["claims"] for c in coupons),
            "redeemed_claims": sum(c["redeemed_claims"] for c in coupons),
            "conversion_rate": (
                sum(c["redeemed_claims"] for c in coupons) / sum(c["claims"] for c in coupons)
                if sum(c["claims"] for c in coupons) else 0),
        },
        "trend": trend,
        "coupons": coupons,
    }


_WEEKLY_COMPARISON_FIELDS = (
    "claims", "redemptions", "redeemed_claims", "gross_amount", "commission", "net_amount",
)


def shop_weekly_report(db: Session, shop: models.Shop, *, as_of: datetime = None) -> dict:
    """Compare adjacent completed UTC calendar weeks in existing dashboard terms."""
    as_of = as_of or _now()
    if as_of.tzinfo is not None:
        as_of = as_of.astimezone(timezone.utc)
    today = as_of.date()
    current_monday = today - timedelta(days=today.weekday())
    current_end = current_monday - timedelta(days=1)
    previous_end = current_end - timedelta(days=7)

    def period_ending(sunday):
        dashboard = shop_dashboard(db, shop, days=7,
                                   end=datetime.combine(sunday, datetime.min.time()))
        totals = dict(dashboard["totals"])
        # Zero claims is missing evidence for a conversion rate, not 0%.
        if not totals["claims"]:
            totals["conversion_rate"] = None
        return {"period": dashboard["period"], "totals": totals}

    current = period_ending(current_end)
    previous = period_ending(previous_end)
    current_totals, previous_totals = current["totals"], previous["totals"]

    def compare(field):
        new = current_totals[field]
        prior = Decimal(str(previous_totals[field]))
        delta = Decimal(str(new)) - prior
        return {"delta": int(delta) if isinstance(new, int) else float(delta),
                "percent": float((delta * 100 / prior).quantize(CENT)) if prior else None}

    def rate(totals):
        return Decimal(totals["redeemed_claims"]) / totals["claims"] if totals["claims"] else None

    cur_rate, prev_rate = rate(current_totals), rate(previous_totals)
    shift = (((cur_rate - prev_rate) * 100).quantize(CENT)
             if cur_rate is not None and prev_rate is not None else None)
    return {
        "shop_id": shop.id, "shop_name": shop.name, "currency": "HKD", "timezone": "UTC",
        "current": current, "previous": previous,
        "changes": {**{field: compare(field) for field in _WEEKLY_COMPARISON_FIELDS},
                    "conversion_percentage_points": float(shift) if shift is not None else None},
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
    redemptions = (db.query(models.Redemption)
                   .filter(models.Redemption.shop_id == shop.id,
                           models.Redemption.scanned_at >= start,
                           models.Redemption.scanned_at < end,
                           models.Redemption.revoked_at.is_(None),
                           ~models.Redemption.id.in_(db.query(SettlementItem.redemption_id)))
                   .order_by(models.Redemption.id).all())
    gross = Decimal("0")
    items = []
    for r in redemptions:
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
            if (e.status_code == 409 and db.query(Settlement.id)
                    .filter_by(shop_id=shop_id, period=period).first()):
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


def catch_up_monthly_settlements(db: Session) -> dict:
    """Retry every missing closed-month statement after downtime or failure."""
    now = _now()
    cutoff = datetime(now.year, now.month, 1)
    periods = (db.query(models.Redemption.shop_id,
                        extract('year', models.Redemption.scanned_at),
                        extract('month', models.Redemption.scanned_at))
               .outerjoin(models.WalletItem, models.WalletItem.id == models.Redemption.wallet_item_id)
               .outerjoin(Order, Order.id == models.WalletItem.order_id)
               .filter(models.Redemption.scanned_at < cutoff,
                       models.Redemption.revoked_at.is_(None),
                       or_(Order.id.is_(None), Order.refund_status != 'refunded'),
                       ~models.Redemption.id.in_(db.query(SettlementItem.redemption_id)))
               .distinct().all())
    existing = set(db.query(Settlement.shop_id, Settlement.period).all())
    blocked = sorted({(shop_id, f'{int(year):04d}-{int(month):02d}')
                      for shop_id, year, month in periods
                      if (shop_id, f'{int(year):04d}-{int(month):02d}') in existing})
    if blocked:
        logger.warning('Monthly catch-up found %d existing periods with unmatched redemptions: %s',
                       len(blocked), blocked)
    missing = sorted({(shop_id, f'{int(year):04d}-{int(month):02d}')
                      for shop_id, year, month in periods
                      if (shop_id, f'{int(year):04d}-{int(month):02d}') not in existing})
    results_by_period = {}
    for shop_id, period in missing:
        result = results_by_period.setdefault(period, {
            'period': period, 'generated': 0, 'skipped': 0, 'failed': 0, 'failures': []})
        shop = db.get(models.Shop, shop_id)
        if shop is None:
            result['failed'] += 1
            result['failures'].append({'shop_id': shop_id, 'error': 'shop not found'})
            continue
        try:
            generate_settlement_for_shop(db, shop, period)
            result['generated'] += 1
        except HTTPException as exc:
            db.rollback()
            if (exc.status_code == 409 and db.query(Settlement.id)
                    .filter_by(shop_id=shop_id, period=period).first()):
                result['skipped'] += 1
            else:
                result['failed'] += 1
                result['failures'].append({'shop_id': shop_id, 'error': exc.detail})
                logger.exception('monthly catch-up failed shop=%s period=%s', shop_id, period)
        except Exception:
            db.rollback()
            result['failed'] += 1
            result['failures'].append({'shop_id': shop_id, 'error': 'job failed; see server log'})
            logger.exception('monthly catch-up failed shop=%s period=%s', shop_id, period)
    results = list(results_by_period.values())
    return {"periods": results,
            "blocked_existing_periods": [{"shop_id": shop_id, "period": period}
                                         for shop_id, period in blocked],
            "generated": sum(result['generated'] for result in results),
            "skipped": sum(result['skipped'] for result in results),
            "failed": sum(result['failed'] for result in results)}


# ---------- CSV export ----------
def settlements_csv(rows) -> "PlainTextResponse":
    """Settlement-list CSV (same ?format=csv pattern as the redemption
    export). `rows` are _settlement_out dicts."""
    from fastapi.responses import PlainTextResponse
    from .shared import sanitize_csv_cell
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "shop_id", "shop_name", "period", "redemption_count",
                "gross_amount", "commission", "net_amount",
                "transferred_net", "outstanding_net", "status"])
    for s in rows:
        w.writerow([sanitize_csv_cell(v) for v in (
                    s["id"], s["shop_id"], s["shop_name"], s["period"],
                    s["redemption_count"], s["gross_amount"], s["commission"],
                    s["net_amount"], s["transferred_net"],
                    s["outstanding_net"], s["status"])])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv; charset=utf-8")


def settlement_items_csv(settlement_id: int, period: str, items) -> "PlainTextResponse":
    """Per-line CSV for one settlement (basis_note included, §2.5)."""
    from fastapi.responses import PlainTextResponse
    from .shared import sanitize_csv_cell
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["settlement_id", "period", "redemption_id", "coupon_id",
                "coupon_name", "amount", "commission", "basis_note"])
    for item in items:
        w.writerow([sanitize_csv_cell(v) for v in (
                    settlement_id, period, item["redemption_id"],
                    item["coupon_id"], item.get("coupon_name", ""),
                    item["amount"], item["commission"],
                    item.get("basis_note", ""))])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv; charset=utf-8")
