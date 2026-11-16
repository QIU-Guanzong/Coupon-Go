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
    verified by a redemption record (redemption_id) or recorded as a
    merchant/POS-verified spend. Tracked is reserved, not produced here.
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

from sqlalchemy.orm import Session

from . import config, models
from .models_cashback import (
    CashbackAppeal,
    CashbackTransaction,
    CashbackWithdrawal,
)

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
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }


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
    Dine-in unlock needs a new spend event, so it is NOT done here — it
    happens in record_spend().
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


def record_spend(db: Session, user: models.User, merchant_id: int, amount,
                 channel: str, rate=None, redemption_id=None) -> CashbackTransaction:
    """Record one verified spend and earn cashback (status: pending).

    Dine-in unlock: a new dine-in spend at the same merchant unlocks ALL
    earlier pending dine-in transactions of that merchant still inside
    their 90-day window. The new transaction itself stays pending — it
    waits for ITS next visit.
    """
    if channel not in ("dinein", "online"):
        raise CashbackError("channel 須為 dinein 或 online")
    spend = money(amount)
    if spend <= 0:
        raise CashbackError("消費金額須大於 0")
    shop = db.query(models.Shop).filter(models.Shop.id == merchant_id).first()
    if shop is None:
        raise CashbackError("找不到此商戶")
    if redemption_id is not None:
        red = db.query(models.Redemption).filter(
            models.Redemption.id == redemption_id).first()
        if red is None or red.shop_id != merchant_id:
            raise CashbackError("核銷記錄不存在或不屬於此商戶")
        if red.wallet_item_id is not None:
            item = db.query(models.WalletItem).filter(
                models.WalletItem.id == red.wallet_item_id).first()
            if item is None or item.user_id != user.id:
                raise CashbackError("核銷記錄不屬於此用戶")
    r = Decimal(str(rate)) if rate is not None else Decimal(str(config.CASHBACK_DEFAULT_RATE))
    if r <= 0 or r > 1:
        raise CashbackError("返現比例須在 0–1 之間")
    earned = money(spend * r)
    cap = Decimal(str(config.CASHBACK_MAX_PER_TXN_HKD))
    if cap > 0 and earned > cap:
        earned = cap
    if earned <= 0:
        raise CashbackError("返現金額為 0，請檢查消費金額與比例")

    now = _now()
    sweep_user(db, user.id, now)
    if channel == "dinein":
        # Unlock earlier pending dine-in earnings at the same merchant.
        prior = (db.query(CashbackTransaction)
                 .filter(CashbackTransaction.user_id == user.id,
                         CashbackTransaction.merchant_id == merchant_id,
                         CashbackTransaction.channel == "dinein",
                         CashbackTransaction.status == "pending").all())
        for t in prior:
            _confirm(db, t, now)

    txn = CashbackTransaction(
        user_id=user.id, merchant_id=merchant_id, redemption_id=redemption_id,
        channel=channel, amount=earned, rate=r, status="pending",
        funded_by="merchant_commission",
        expected_confirm_at=(
            now + timedelta(days=config.CASHBACK_ONLINE_CONFIRM_DAYS)
            if channel == "online" else None),
        expires_at=(
            now + timedelta(days=config.CASHBACK_DINEIN_UNLOCK_DAYS)
            if channel == "dinein" else None),
    )
    db.add(txn)
    db.flush()
    return txn


def balances(db: Session, user_id: int) -> dict:
    """Separate ledgers: cashback balances never include points."""
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
    return {
        "withdrawable_balance": float(withdrawable),
        "pending_balance": float(pending),
        "tracked_balance": float(tracked),
        "total_earned": float(total_earned),
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
    if user.name and user.name.strip() and account_name.strip() != user.name.strip():
        raise CashbackError("收款賬戶姓名須與註冊姓名一致")
    now = _now()
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
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }
