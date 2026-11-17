"""Line D — cashback service: state machines, balances, withdrawal, appeals.

Two machines, kept separate (report §3.2 vs §3.2b):

  ONLINE / general (time-based confirmation):
      pending --(expected_confirm_at reached)--> confirmed --> withdrawable
  DINE-IN (next-visit unlock):
      pending --(next spend at SAME merchant within 90 days)--> confirmed
              --> withdrawable
      pending --(no next visit within 90 days)--> void (expired_90d)

Notes:
  * "tracked" is a valid status for async/unverified ingestion, but the v1
    synchronous record path writes "pending" directly: the spend is already
    verified by a redemption record (redemption_id).
  * FIX-2: there is NO user-facing record endpoint anymore. The only
    writer is record_spend(), called server-side from
    the successful vendor scan. Amount basis and rate are derived from
    server-side data only (the package order's paid amount, or the
    coupon row's own price); no client-supplied amount, rate or
    merchant is ever accepted.
  * N1: the spend basis is the ACTUAL PAID amount only — a package
    voucher's Order.paid_amount, else a price coupon's discount_price.
    min_spend is a threshold, never a spend: a free / threshold-only
    coupon earns nothing.
  * "confirmed" promotes to "withdrawable" in the same sweep in v1: there
    is no payout hold. The confirmed step is still a real transition
    (confirmed_at is stamped) and the status is kept so a future hold /
    review step can park a transaction at confirmed.
  * Withdrawals consume transactions FIFO (oldest first); partial
    consumption is tracked in withdrawn_amount.
  * Cashback never touches point_transactions (separate ledgers).
"""
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
import logging

from sqlalchemy.orm import Session

from . import config, models
from .models_cashback import (
    CashbackAdjustment,
    CashbackAppeal,
    CashbackTransaction,
    CashbackWithdrawal,
)

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def money(value) -> Decimal:
    """Quantize to HK$ cents."""
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


class CashbackError(ValueError):
    """Business-rule failure; router maps to 400 with this message."""


def _tx_out(db: Session, t: CashbackTransaction) -> dict:
    shop = db.query(models.Shop).filter(models.Shop.id == t.merchant_id).first()
    if t.status == "pending" and t.channel == "dinein":
        status_text = "待下次消費解鎖"
    elif t.status == "pending":
        status_text = "待確認"
    else:
        status_text = {
            "tracked": "已追蹤",
            "confirmed": "已確認",
            "withdrawable": "可提現",
            "void": "已失效",
            "withdrawn": "已提現",
        }.get(t.status, t.status)
    return {
        "id": t.id,
        "merchant_id": t.merchant_id,
        "shop_name": shop.name if shop else "",
        "redemption_id": t.redemption_id,
        "channel": t.channel,
        "amount": float(t.amount),
        "rate": float(t.rate),
        "status": t.status,
        "status_text": status_text,
        "funded_by": t.funded_by,
        "expected_confirm_at": t.expected_confirm_at.isoformat() if t.expected_confirm_at else None,
        "expires_at": t.expires_at.isoformat() if t.expires_at else None,
        "confirmed_at": t.confirmed_at.isoformat() if t.confirmed_at else None,
        "withdrawn_amount": float(t.withdrawn_amount or 0),
        "remaining_amount": float(money(t.amount) - money(t.withdrawn_amount or 0)),
        "void_reason": t.void_reason,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _withdrawal_out(w: CashbackWithdrawal) -> dict:
    return {
        "id": w.id,
        "amount": float(w.amount),
        "method": w.method,
        "method_text": "轉數快 (FPS)" if w.method == "fps" else "銀行轉賬",
        "account_name": w.account_name,
        "status": w.status,
        # Gap 6: server-side failure reason; null on pending / paid rows.
        "failure_reason": w.failure_reason,
        "processed_at": w.processed_at.isoformat() if w.processed_at else None,
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }


def mark_withdrawal_failed(db: Session, withdrawal_id: int,
                           reason: str) -> CashbackWithdrawal:
    """Ops path: a pending payout failed at payout time (bank / FPS
    rejection). There is no live payout integration, so this is the
    only way a withdrawal becomes failed — ops records the provider's
    reason here and the client shows it (Gap 6)."""
    w = db.query(CashbackWithdrawal).filter(
        CashbackWithdrawal.id == withdrawal_id).first()
    if w is None:
        raise CashbackError("找不到此提現記錄")
    if w.status != "pending":
        raise CashbackError("只有處理中的提現可以標記為失敗")
    if not reason or not reason.strip():
        raise CashbackError("請提供提現失敗原因")
    w.status = "failed"
    w.failure_reason = reason.strip()
    w.processed_at = _now()
    db.flush()
    return w


def _confirm(db: Session, t: CashbackTransaction, now: datetime):
    """pending -> confirmed -> withdrawable (same sweep in v1; see module doc)."""
    t.status = "confirmed"
    t.confirmed_at = now
    db.flush()
    t.status = "withdrawable"
    db.flush()


def sweep_user(db: Session, user_id: int, now: datetime = None):
    """Time-driven transitions for one user (idempotent, lazy — run on read).

    Online pending past its confirm window -> confirmed -> withdrawable.
    Dine-in pending past its 90-day unlock deadline -> void (expired_90d).
    Dine-in unlock needs a new valid redemption, so it is NOT done here —
    it happens in record_spend().
    Returns (confirmed_count, expired_count).
    """
    now = now or _now()
    confirmed = expired = 0
    txns = (db.query(CashbackTransaction)
            .filter(CashbackTransaction.user_id == user_id,
                    CashbackTransaction.status == "pending").all())
    for t in txns:
        if t.channel == "online":
            if t.expected_confirm_at and t.expected_confirm_at <= now:
                _confirm(db, t, now)
                confirmed += 1
        else:  # dinein
            deadline = t.expires_at or (
                t.created_at + timedelta(days=config.CASHBACK_DINEIN_UNLOCK_DAYS)
                if t.created_at else None)
            if deadline and deadline <= now:
                t.status = "void"
                t.void_reason = "expired_90d"
                expired += 1
    if confirmed or expired:
        db.flush()
    return confirmed, expired


def sweep_all_users(db: Session, batch_size: int = 100,
                    now: datetime = None):
    """M1: time-driven transitions for ALL users, in batches
    (cursor-paginated by user id). Same per-user logic as sweep_user,
    which only ever ran lazily on one user's own page reads.

    Online pending past its confirm window -> confirmed ->
    withdrawable. Dine-in pending past its 90-day unlock deadline ->
    void (expired_90d). Idempotent: a confirmed / voided transaction
    is no longer pending and is never selected again. One bad user
    never blocks the rest: per-user failure is logged, rolled back,
    counted, and retried on the next run.
    Returns {"users", "confirmed", "expired", "failed"}."""
    now = now or _now()
    batch_size = max(1, int(batch_size))
    confirmed = expired = failed = users = 0
    last_id = 0
    while True:
        rows = (db.query(CashbackTransaction.user_id)
                .filter(CashbackTransaction.status == "pending",
                        CashbackTransaction.user_id > last_id)
                .distinct()
                .order_by(CashbackTransaction.user_id)
                .limit(batch_size).all())
        if not rows:
            break
        ids = [r[0] for r in rows]
        for user_id in ids:
            try:
                c, e = sweep_user(db, user_id, now)
                confirmed += c
                expired += e
                users += 1
                db.commit()
            except Exception:
                db.rollback()
                failed += 1
                logger.exception("cashback sweep failed user_id=%s", user_id)
        last_id = ids[-1]
    return {"users": users, "confirmed": confirmed,
            "expired": expired, "failed": failed}


def record_spend(db: Session,
                                   redemption: models.Redemption
                                   ) -> CashbackTransaction | None:
    """Credit cashback for one successful redemption (server-side only).

    FIX-2: this is the ONLY way a cashback earning is created. It is
    called from the vendor scan after the redemption row exists, inside
    the same transaction. Nothing here comes from the client:

    * user / merchant: derived from the redemption (wallet item owner /
      redemption.shop_id) — never from request input.
    * spend basis ("实付价"): the ACTUAL PAID amount, server-side
      only — a package voucher's Order.paid_amount (WalletItem.order_id
      -> orders.id; Order.amount is only the amount payable and is
      never used), else a price coupon's discount_price. min_spend is
      a threshold, never a spend. A coupon with no paid amount (free /
      pure-threshold / priceless, or an unpaid package order) earns
      nothing (returns None) instead of inventing an amount.
    * rate ("商户比例"): the configured dine-in merchant rate
      (config.CASHBACK_DINEIN_RATE); there is no per-shop rate column.
      A redemption is always an in-store (dinein) spend, so the channel
      is always "dinein" and the 90-day next-visit unlock applies.

    Validation: the redemption must not be revoked, and one redemption
    credits at most once (checked here; also enforced by the UNIQUE
    constraint on cashback_transactions.redemption_id).

    Dine-in unlock: a new valid redemption at a merchant unlocks ALL
    earlier pending dine-in earnings of the same user at that merchant
    still inside their 90-day window — whether or not this redemption
    itself earns. The new earning itself stays pending; it waits for ITS
    next visit. Returns the new transaction, or None when the coupon
    carries no verifiable spend.
    """
    if redemption.revoked_at is not None:
        raise CashbackError("此核銷已被撤销，不能記返現")
    item = db.query(models.WalletItem).filter(
        models.WalletItem.id == redemption.wallet_item_id).first()
    if item is None:
        raise CashbackError("核銷記錄不完整，找不到優惠券領取記錄")
    coupon = db.query(models.Coupon).filter(
        models.Coupon.id == redemption.coupon_id).first()
    if coupon is None:
        raise CashbackError("核銷記錄不完整，找不到優惠券")
    shop = db.query(models.Shop).filter(
        models.Shop.id == redemption.shop_id).first()
    if shop is None:
        raise CashbackError("找不到此商戶")
    existing = db.query(CashbackTransaction).filter(
        CashbackTransaction.redemption_id == redemption.id).first()
    if existing is not None:
        raise CashbackError("此核銷已記返現，不可重複入賬")

    now = _now()
    sweep_user(db, item.user_id, now)
    # Unlock earlier pending dine-in earnings at the same merchant. This
    # runs for every new valid redemption, even one that earns nothing.
    prior = (db.query(CashbackTransaction)
             .filter(CashbackTransaction.user_id == item.user_id,
                     CashbackTransaction.merchant_id == redemption.shop_id,
                     CashbackTransaction.channel == "dinein",
                     CashbackTransaction.status == "pending").all())
    for t in prior:
        _confirm(db, t, now)
        # P1-1: record WHICH redemption unlocked this earning, so a
        # later revoke of this redemption can roll exactly this
        # unlock back (void_for_revocation). Additive provenance only
        # — the FIX-2 credit rules above are untouched.
        t.unlocked_by_redemption_id = redemption.id

    paid = None
    if item.order_id is not None:
        # N1: package voucher — the order is the payment record. Only
        # its real paid amount (Order.paid_amount, NOT Order.amount,
        # which is just the amount payable) counts, and only when > 0.
        # An unpaid / missing order has no verifiable spend: do NOT
        # fall back to the coupon price — that would credit cashback
        # for money never paid.
        order = db.query(models.Order).filter(
            models.Order.id == item.order_id).first()
        if order is not None and order.paid_amount is not None \
                and money(order.paid_amount) > 0:
            paid = money(order.paid_amount)
    elif coupon.discount_price is not None \
            and money(coupon.discount_price) > 0:
        # Price coupon: the coupon's own server-side price is the paid
        # amount. min_spend is only a threshold and NEVER a spend
        # basis — a free / threshold-only coupon used to credit on its
        # threshold (claim -> scan -> withdraw = money from nothing).
        paid = money(coupon.discount_price)
    if paid is None:
        # No verifiable paid amount (free / pure-threshold / priceless
        # coupon, or an unpaid package order): earn nothing, invent
        # nothing. The dine-in unlock above has already run — this
        # valid redemption still unlocks earlier pending earnings.
        return None
    rate = Decimal(str(config.CASHBACK_DINEIN_RATE))
    if rate <= 0 or rate > 1:
        raise CashbackError("返現比例須在 0–1 之間")
    earned = money(paid * rate)
    cap = Decimal(str(config.CASHBACK_MAX_PER_TXN_HKD))
    if cap > 0 and earned > cap:
        earned = cap
    if earned <= 0:
        return None

    txn = CashbackTransaction(
        user_id=item.user_id, merchant_id=redemption.shop_id,
        redemption_id=redemption.id,
        channel="dinein", amount=earned, rate=rate, status="pending",
        funded_by="merchant_commission",
        expected_confirm_at=None,
        expires_at=now + timedelta(days=config.CASHBACK_DINEIN_UNLOCK_DAYS),
    )
    db.add(txn)
    db.flush()
    return txn


def _adjustment_out(a: CashbackAdjustment) -> dict:
    return {
        "id": a.id,
        "user_id": a.user_id,
        "redemption_id": a.redemption_id,
        "amount": float(a.amount),
        "reason": a.reason,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def adjustments(db: Session, user_id: int) -> list:
    """Negative adjustments for one user (newest first)."""
    rows = (db.query(CashbackAdjustment)
            .filter(CashbackAdjustment.user_id == user_id)
            .order_by(CashbackAdjustment.id.desc()).all())
    return [_adjustment_out(a) for a in rows]


def void_for_revocation(db: Session,
                        redemption: models.Redemption) -> dict:
    """P1-1: reverse the cashback effects of a revoked redemption.
    Called from the revoke endpoint, inside the same transaction
    (flushes, never commits).

    Chosen rule for already-withdrawn money (the conservative one):
    withdrawn cash is never clawed back by pushing a transaction
    negative. Instead:

    * The revoked redemption's OWN earning dies entirely
      (status -> void, void_reason="revoked"). Any part of it already
      withdrawn is recorded as a negative CashbackAdjustment
      (reason=revoked_withdrawn), so withdrawable_balance drops by
      that amount — possibly below zero — and withdraw() blocks any
      further withdrawal while the balance is below the amount asked.
    * A predecessor earning unlocked BY this redemption rolls back
      to pending (its confirmation — and only its confirmation — is
      undone; the earning itself is still valid because its own
      redemption stands). If part of it was already withdrawn, only
      the un-withdrawn remainder goes back to pending (amount is
      reduced to the remainder, withdrawn_amount reset to 0); the
      paid part stays paid and no adjustment is recorded for it — the
      money was paid for a real earning. A fully-withdrawn
      predecessor is complete and is left untouched.
    * Earnings unlocked by OTHER redemptions are untouched: the
      unlock provenance (unlocked_by_redemption_id, set in
      record_spend) is what selects the rollback set.

    Returns a summary dict for the revoke response / log.
    """
    summary = {"voided_own": 0, "reverted_to_pending": 0, "adjustments": []}
    own = db.query(CashbackTransaction).filter(
        CashbackTransaction.redemption_id == redemption.id).first()
    if own is not None and own.status != "void":
        withdrawn = money(own.withdrawn_amount or 0)
        if withdrawn > 0:
            adj = CashbackAdjustment(
                user_id=own.user_id, redemption_id=redemption.id,
                amount=-withdrawn, reason="revoked_withdrawn")
            db.add(adj)
            db.flush()
            summary["adjustments"].append(_adjustment_out(adj))
            logger.info(
                "revoke cashback: redemption_id=%s earning id=%s voided "
                "after HK$%s already withdrawn; negative adjustment id=%s",
                redemption.id, own.id, withdrawn, adj.id)
        own.status = "void"
        own.void_reason = "revoked"
        summary["voided_own"] = 1
    unlocked = (db.query(CashbackTransaction)
                .filter(CashbackTransaction.unlocked_by_redemption_id
                        == redemption.id).all())
    for t in unlocked:
        if t.status not in ("confirmed", "withdrawable"):
            continue  # withdrawn (complete) / pending / void: untouched
        withdrawn = money(t.withdrawn_amount or 0)
        if withdrawn <= 0:
            t.status = "pending"
            t.confirmed_at = None
            t.unlocked_by_redemption_id = None
            summary["reverted_to_pending"] += 1
        elif withdrawn >= money(t.amount):
            # Fully paid out: nothing left to revert; the earning is
            # complete. Leave it withdrawn.
            continue
        else:
            # Partially withdrawn: only the remainder goes back to
            # pending; the paid part stays paid (no adjustment — the
            # earning is real, only its confirmation is rolled back).
            remainder = money(money(t.amount) - withdrawn)
            logger.info(
                "revoke cashback: redemption_id=%s reverts unlock of "
                "txn id=%s (HK$%s of HK$%s withdrawn); HK$%s returns "
                "to pending", redemption.id, t.id, withdrawn, t.amount,
                remainder)
            t.amount = remainder
            t.withdrawn_amount = Decimal("0")
            t.status = "pending"
            t.confirmed_at = None
            t.unlocked_by_redemption_id = None
            summary["reverted_to_pending"] += 1
    if summary["voided_own"] or summary["reverted_to_pending"] \
            or summary["adjustments"]:
        db.flush()
    return summary


def balances(db: Session, user_id: int) -> dict:
    """Separate ledgers: cashback balances never include points.

    P1-1: negative adjustments (revoked earnings already withdrawn)
    are folded into withdrawable_balance, which may therefore go
    negative; withdraw() refuses any amount above it, so a negative
    balance blocks further withdrawals until new earnings cover it.
    total_earned stays gross earned (adjustments are reported
    separately as adjustment_total, never silently netted into it).
    """
    txns = (db.query(CashbackTransaction)
            .filter(CashbackTransaction.user_id == user_id).all())
    withdrawable = pending = tracked = Decimal("0")
    total_earned = Decimal("0")
    for t in txns:
        if t.status == "void":
            continue
        total_earned += money(t.amount)
        if t.status in ("confirmed", "withdrawable"):
            withdrawable += money(t.amount) - money(t.withdrawn_amount or 0)
        elif t.status == "pending":
            pending += money(t.amount)
        elif t.status == "tracked":
            tracked += money(t.amount)
    adjustment_total = sum(
        (money(a.amount) for a in db.query(CashbackAdjustment)
         .filter(CashbackAdjustment.user_id == user_id).all()),
        Decimal("0"))
    withdrawable += adjustment_total
    return {
        "withdrawable_balance": float(withdrawable),
        "pending_balance": float(pending),
        "tracked_balance": float(tracked),
        "total_earned": float(total_earned),
        "adjustment_total": float(adjustment_total),
    }


def withdraw(db: Session, user: models.User, amount, method: str,
             account_name: str, account_ref: str = "") -> CashbackWithdrawal:
    """Withdraw withdrawable cashback. FIFO-consumes transactions."""
    if method not in ("fps", "bank"):
        raise CashbackError("提現方式須為 fps（轉數快）或 bank（銀行轉賬）")
    amt = money(amount)
    if amt <= 0:
        raise CashbackError("提現金額須大於 0")
    if amt < Decimal(str(config.CASHBACK_MIN_WITHDRAW_HKD)):
        raise CashbackError(
            f"提現門檻為 HK${config.CASHBACK_MIN_WITHDRAW_HKD:g}，未達門檻")
    if not account_name or not account_name.strip():
        raise CashbackError("請填寫收款賬戶姓名")
    # ShopBack HK rule: payout account name must match the registered name.
    # P1-4: a blank registered name no longer skips this check — the
    # user must fill in their registered name (User.name) first.
    if not user.name or not user.name.strip():
        raise CashbackError("請先完善註冊姓名後再提現")
    if account_name.strip() != user.name.strip():
        raise CashbackError("收款賬戶姓名須與註冊姓名一致")
    now = _now()
    # FIX-3: lock the user row FIRST (SELECT ... FOR UPDATE on Postgres;
    # a no-op on SQLite). Everything below — sweep, balance re-check,
    # daily-limit check, FIFO consume — runs inside this transaction
    # while the lock is held, so a second concurrent withdrawal blocks
    # here and re-reads the balance only after this one commits.
    # populate_existing() refreshes the already-loaded user row so the
    # lock query is not satisfied from the identity map alone.
    db.query(models.User).filter(models.User.id == user.id) \
        .with_for_update().populate_existing().first()
    sweep_user(db, user.id, now)
    bal = balances(db, user.id)
    if amt > Decimal(str(bal["withdrawable_balance"])):
        raise CashbackError("可提現餘額不足")
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today = (db.query(CashbackWithdrawal)
             .filter(CashbackWithdrawal.user_id == user.id,
                     CashbackWithdrawal.created_at >= day_start,
                     CashbackWithdrawal.status.in_(("pending", "paid"))).all())
    today_total = sum((money(w.amount) for w in today), Decimal("0"))
    if today_total + amt > Decimal(str(config.CASHBACK_DAILY_WITHDRAW_LIMIT_HKD)):
        raise CashbackError(
            f"每日提現上限為 HK${config.CASHBACK_DAILY_WITHDRAW_LIMIT_HKD:g}")

    w = CashbackWithdrawal(user_id=user.id, amount=amt, method=method,
                           account_name=account_name.strip(),
                           account_ref=(account_ref or "").strip(),
                           status="pending")
    db.add(w)
    db.flush()
    remaining = amt
    txns = (db.query(CashbackTransaction)
            .filter(CashbackTransaction.user_id == user.id,
                    CashbackTransaction.status.in_(("confirmed", "withdrawable")))
            .order_by(CashbackTransaction.created_at.asc(),
                      CashbackTransaction.id.asc()).all())
    for t in txns:
        if remaining <= 0:
            break
        left = money(t.amount) - money(t.withdrawn_amount or 0)
        if left <= 0:
            continue
        take = min(left, remaining)
        t.withdrawn_amount = money(money(t.withdrawn_amount or 0) + take)
        if money(t.withdrawn_amount) >= money(t.amount):
            t.status = "withdrawn"
            t.withdrawal_id = w.id
        remaining -= take
    if remaining > 0:  # pragma: no cover — balances check above prevents this
        raise CashbackError("可提現餘額不足")
    db.flush()
    return w


def void_transaction(db: Session, user: models.User, txn_id: int,
                     reason: str) -> CashbackTransaction:
    """Cancel / return: a pending (or tracked) earning goes to void.

    Confirmed / withdrawable / withdrawn earnings cannot be voided — the
    merchant has already confirmed them; a refund after confirmation must
    go through ops / appeal, not this endpoint.
    """
    if reason not in ("cancelled", "returned"):
        raise CashbackError("void 原因須為 cancelled（取消）或 returned（退貨）")
    t = db.query(CashbackTransaction).filter(
        CashbackTransaction.id == txn_id,
        CashbackTransaction.user_id == user.id).first()
    if t is None:
        raise CashbackError("找不到此返現記錄")
    if t.status not in ("tracked", "pending"):
        raise CashbackError("只有待確認的返現可以作廢")
    t.status = "void"
    t.void_reason = reason
    db.flush()
    return t


def create_appeal(db: Session, user: models.User, merchant_id: int, order_no: str,
                  spent_at: datetime, amount, evidence: str = "",
                  note: str = "") -> CashbackAppeal:
    """Missing Cashback appeal: must be filed within 30 days of the spend."""
    shop = db.query(models.Shop).filter(models.Shop.id == merchant_id).first()
    if shop is None:
        raise CashbackError("找不到此商戶")
    if not order_no or not order_no.strip():
        raise CashbackError("請提供訂單號 / 核銷憑證號")
    spend = money(amount)
    if spend <= 0:
        raise CashbackError("消費金額須大於 0")
    now = _now()
    if spent_at > now:
        raise CashbackError("消費日期不能是未來")
    if spent_at < now - timedelta(days=config.CASHBACK_MISSING_WINDOW_DAYS):
        raise CashbackError(
            f"申訴須在消費後 {config.CASHBACK_MISSING_WINDOW_DAYS} 天內提交")
    appeal = CashbackAppeal(
        user_id=user.id, merchant_id=merchant_id, order_no=order_no.strip(),
        spent_at=spent_at, amount=spend,
        evidence=(evidence or "").strip(), note=(note or "").strip(),
        status="submitted")
    db.add(appeal)
    db.flush()
    return appeal


# Gap 3: appeal status text (four states — submitted | under_review |
# resolved | rejected — defined in models_cashback.CashbackAppeal).
APPEAL_STATUS_TEXT = {
    "submitted": "已提交",
    "under_review": "處理中",
    "resolved": "已解決",
    "rejected": "已拒絕",
}


def appeal_out(a: CashbackAppeal, db: Session) -> dict:
    shop = db.query(models.Shop).filter(models.Shop.id == a.merchant_id).first()
    return {
        "id": a.id,
        "merchant_id": a.merchant_id,
        "shop_name": shop.name if shop else "",
        "order_no": a.order_no,
        "spent_at": a.spent_at.isoformat() if a.spent_at else None,
        "amount": float(a.amount),
        "evidence": a.evidence,
        "note": a.note,
        "status": a.status,
        "status_text": APPEAL_STATUS_TEXT.get(a.status, a.status),
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
    }


def list_appeals(db: Session, user_id: int, limit: int = 50) -> list:
    """Gap 3: the caller's own Missing Cashback appeals, newest first."""
    rows = (db.query(CashbackAppeal)
            .filter(CashbackAppeal.user_id == user_id)
            .order_by(CashbackAppeal.id.desc()).limit(limit).all())
    return [appeal_out(a, db) for a in rows]
