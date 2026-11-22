"""P1-C: points by spend (docs/FEATURE_GAP_20261120.md §3.4,
docs/P1C_API.md).

Rules, all amounts from env (app.config) so they change without a
code change:
- paid voucher redeem: floor(Order.paid_amount * POINTS_PER_HKD),
  credited at redemption. Free coupon redeem keeps POINTS_REDEEM.
- every positive grant expires POINTS_EXPIRE_MONTHS after it is
  earned (PointTransaction.expires_at, set by deps.add_points);
  the `points-expire` scheduler job clears expired lots and writes
  one reason=expire ledger row per lot.
- a refund claws back the points that order's redemption earned
  (reason=refund_clawback, ref_id=order id, once per order). The
  balance may go negative; a negative balance blocks exchange (and
  any future transfer) via require_spendable_balance().

Lot accounting (FIFO): spends and clawbacks consume the oldest
grant first; expiry rows consume their referenced grant. Therefore
"expiring soon" is the unspent remainder of lots
whose expires_at falls inside the window. points_balance() itself
stays the plain ledger sum — the sweep writes real negative rows,
so the sum is always the available balance, negative included.

Timing note: a spend that lands after a lot's expires_at but before
the next sweep run consumes that lot (the sweep then finds nothing
left to expire). The sweep runs every SCHEDULER_INTERVAL_SECONDS
(default 300s), so the window is minutes, not days.
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session, aliased

from . import config, models
from .deps import add_months, add_points, points_balance
from .models_packages import Order

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def points_expiry_for(earned_at=None):
    """Expiry of a grant earned at earned_at (default now)."""
    base = earned_at or _now()
    return add_months(base, config.POINTS_EXPIRE_MONTHS)


def award_redeem_points(db: Session, user_id: int,
                        wallet_item: models.WalletItem,
                        coupon: models.Coupon = None) -> int:
    """Points for one redemption. Paid voucher (wallet item links an
    order) -> floor(paid_amount * POINTS_PER_HKD); anything else
    (free claim, bargain voucher with no order) -> POINTS_REDEEM.
    Returns the points awarded (0 when a paid order's amount earns
    nothing, e.g. a HK$0 order — no ledger row is written then)."""
    order = None
    if getattr(wallet_item, "order_id", None) is not None:
        order = db.query(Order).filter(
            Order.id == wallet_item.order_id).first()
    if order is not None:
        pts = int((Decimal(str(order.paid_amount or 0))
                   * Decimal(str(config.POINTS_PER_HKD))).to_integral_value(
                       rounding=ROUND_FLOOR))
        if pts <= 0:
            return 0
        add_points(db, user_id, pts, "redeem", ref_id=wallet_item.id)
        return pts
    add_points(db, user_id, config.POINTS_REDEEM, "redeem",
               ref_id=wallet_item.id)
    return config.POINTS_REDEEM


def lot_remaining(db: Session, user_id: int):
    """[(grant tx, unspent remainder)] for positive grants, oldest
    first, after allocating spend and clawback deltas FIFO and each
    expiry delta to its referenced grant. A negative remainder beyond all grants is
    negative-balance debt and is tracked by no lot — the plain
    ledger sum in points_balance() is the balance."""
    txs = (db.query(models.PointTransaction)
           .filter(models.PointTransaction.user_id == user_id)
           .order_by(models.PointTransaction.id).all())
    lots = []
    grants = {}
    for t in txs:
        if t.delta > 0:
            lot = [t, t.delta]
            lots.append(lot)
            grants[t.id] = lot
        elif t.delta < 0:
            need = -t.delta
            # Expiry rows explicitly name the grant they remove. Applying
            # these to a different older, still-valid grant can expire the
            # intended grant again on the next sweep when expiry dates vary.
            if t.reason == "expire" and t.ref_id in grants:
                target = grants[t.ref_id]
                take = min(target[1], need)
                target[1] -= take
                need -= take
            for lot in lots:
                if need <= 0:
                    break
                take = min(lot[1], need)
                lot[1] -= take
                need -= take
    return [(t, rem) for t, rem in lots]


def expiring_soon_points(db: Session, user_id: int, days=None) -> int:
    """Unspent points whose grant expires within `days`
    (default POINTS_EXPIRE_SOON_DAYS). Grants without expires_at
    (legacy rows the migration did not backfill) never expire and
    are excluded."""
    window = days if days is not None else config.POINTS_EXPIRE_SOON_DAYS
    now = _now()
    horizon = now + timedelta(days=window)
    total = 0
    for tx, rem in lot_remaining(db, user_id):
        if (rem > 0 and tx.expires_at is not None
                and now < tx.expires_at <= horizon):
            total += rem
    return total


def sweep_expired_points(db: Session) -> dict:
    """Expire every lot past expires_at. One reason=expire row per
    lot (ref_id = the grant's tx id). Each user's lots are calculated
    while holding that customer's transaction lock; savepoints isolate
    individual bad lots. Idempotent: an expired lot has zero
    remainder afterwards, so a re-run finds nothing. Returns
    {expired, points, users, failed}."""
    now = _now()
    # First restrict work to accounts with a positive grant whose expiry has
    # arrived. FIFO allocation below still determines whether that grant has
    # any unspent remainder; an already-consumed lot must never be expired.
    # The expiry-first index supports this global range scan on large ledgers.
    grant = aliased(models.PointTransaction)
    expiry = aliased(models.PointTransaction)
    expired_already = (db.query(expiry.id)
                       .filter(expiry.user_id == grant.user_id,
                               expiry.reason == "expire",
                               expiry.ref_id == grant.id).exists())
    finalized = (db.query(models.PointExpiryFinalization.grant_id)
                 .filter(models.PointExpiryFinalization.grant_id == grant.id).exists())
    user_ids = [r[0] for r in db.query(grant.user_id)
                .filter(grant.delta > 0,
                        grant.expires_at.isnot(None), grant.expires_at <= now,
                        ~expired_already, ~finalized)
                .distinct().all()]
    expired = points = failed = 0
    expired_users = set()
    from .cashback_service import lock_account
    for uid in user_ids:
        completed = []
        user_failures = 0
        try:
            lock_account(db, uid)
            eligible = {r[0] for r in db.query(grant.id)
                        .filter(grant.user_id == uid, grant.delta > 0,
                                grant.expires_at.isnot(None), grant.expires_at <= now,
                                ~expired_already, ~finalized)}
            lots = [(tx.id, rem) for tx, rem in lot_remaining(db, uid)
                    if tx.id in eligible]
            for tx_id, rem in lots:
                try:
                    with db.begin_nested():
                        if rem > 0:
                            add_points(db, uid, -rem, "expire", ref_id=tx_id)
                        else:
                            # Once spent, an immutable grant cannot regain
                            # points. Remember it without adding a zero-value
                            # ledger row, so future sweeps can skip this user.
                            db.add(models.PointExpiryFinalization(grant_id=tx_id,
                                                                  checked_at=now))
                            db.flush()
                    if rem > 0:
                        completed.append(rem)
                except Exception:
                    logger.exception("points-expire failed user=%s grant_tx=%s", uid, tx_id)
                    user_failures += 1
            db.commit()
            expired += len(completed)
            points += sum(completed)
            failed += user_failures
            if completed:
                expired_users.add(uid)
        except Exception:
            logger.exception("points-expire transaction failed user=%s", uid)
            try:
                db.rollback()
            except Exception:
                pass
            failed += user_failures + max(1, len(completed))
    return {"expired": expired, "points": points,
            "users": len(expired_users), "failed": failed}


def clawback_order_points(db: Session, order: Order) -> int:
    """Claw back the points one order's redemption earned. Sums the
    net redeem award for the order's wallet items: redeem grants
    minus any revoke reversals already written for those items'
    redemptions (a revoked-then-refunded voucher must not be clawed
    back twice). Writes one reason=refund_clawback row (ref_id =
    order id) and returns the points clawed back, 0 when there is
    nothing to claw back or this order was already clawed back.
    The caller owns the surrounding transaction (the refund path
    commits); this only flushes."""
    from .cashback_service import lock_account
    lock_account(db, order.user_id)
    item_ids = [i.id for i in db.query(models.WalletItem).filter(
        models.WalletItem.order_id == order.id).all()]
    if not item_ids:
        return 0
    already = db.query(models.PointTransaction).filter_by(
        user_id=order.user_id, reason="refund_clawback",
        ref_id=order.id).first()
    if already is not None:
        return 0
    redeem_sum = db.query(
        func.coalesce(func.sum(models.PointTransaction.delta), 0)).filter(
            models.PointTransaction.user_id == order.user_id,
            models.PointTransaction.reason == "redeem",
            models.PointTransaction.ref_id.in_(item_ids)).scalar() or 0
    redemption_ids = [r[0] for r in db.query(models.Redemption.id).filter(
        models.Redemption.wallet_item_id.in_(item_ids)).all()]
    revoke_sum = 0
    if redemption_ids:
        revoke_sum = db.query(
            func.coalesce(func.sum(models.PointTransaction.delta), 0)).filter(
                models.PointTransaction.user_id == order.user_id,
                models.PointTransaction.reason == "revoke",
                models.PointTransaction.ref_id.in_(redemption_ids)
                ).scalar() or 0
    net = int(redeem_sum) + int(revoke_sum)
    if net <= 0:
        return 0
    add_points(db, order.user_id, -net, "refund_clawback", ref_id=order.id)
    return net


def require_spendable_balance(db: Session, user_id: int) -> int:
    """Spend gate for exchange (and any future transfer): a negative
    balance — left by a refund clawback — blocks spending outright,
    with an explicit error instead of the generic 'insufficient'
    message. Returns the balance when spending is allowed."""
    from .cashback_service import lock_account
    lock_account(db, user_id)
    balance = points_balance(db, user_id)
    if balance < 0:
        raise HTTPException(
            status_code=400,
            detail="積分餘額為負數，退款扣回後請先累積積分再兌換")
    return balance
