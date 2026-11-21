"""P1-B service: post-redemption transfers, reversals, balances, payouts.

Rules (FEATURE_GAP_20261120 §3 decision 3; docs/P1B_API.md; merge
queue §F 2026-11-21):
- Settlement basis is the one shared M6 function (settlement_basis):
  package = order paid_amount, price coupon = discount_price, cash /
  free / pure-threshold = 0 under the生效 basis (M8 makes cash 0).
  A zero basis writes no ledger entry at all.
- Commission reuses MerchantCommissionRate (F5), else the platform
  default 10%. The rate is read at redemption time and frozen on the
  ledger entry, like the Settlement snapshot.
- Money never moves without Stripe: when Connect is not configured,
  or the merchant has no ready account, the entry stays pending with
  an explicit note. Nothing here ever marks a monthly Settlement paid
  — Settlement stays the reconciliation record (admin_v2.pay is
  untouched).
- Transfers NEVER run inside the redemption transaction (queue §F
  item 3): record_redemption only writes the pending ledger entry and
  returns. The scheduler job ``connect-transfers`` (batch, capped)
  and the manual retry endpoint move the money, always with the
  idempotency key ``transfer-{entry_id}``. The scan response therefore
  reports ``pending`` (queued) even when the account is ready — the
  transfer lands shortly after, not inside the scan request.
- Every Stripe money call carries an idempotency key derived from the
  ledger entry id (transfer- / reversal- / payout-{entry_id}); a
  retry reuses the same key, so a first request that timed out at
  Stripe but already executed never moves money twice.
- Payouts lock the merchant account row first, re-read the balance
  inside the lock, write a ``processing`` entry (balance already
  deducted) and COMMIT it, and only then call Stripe. A concurrent
  payout sees the processing entry and is rejected when the remaining
  balance is insufficient. Gateway failure marks the entry ``failed``
  and releases the balance.
- A balance MAY go negative (a transfer reversed after the merchant
  already paid the money out). While negative, new payouts are
  refused; the admin recoveries board lists the shop.
- A failed transfer / reversal attempt increments retry_count; after
  CONNECT_MAX_TRANSFER_RETRIES (default 5) the entry goes
  ``needs_manual`` and an admin notification is raised once. The
  manual retry endpoint is the recovery path.
- The scan hook (record_redemption) must never fail a redemption.
"""
import logging
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import config, connect_gateway, models
from .models import Notification
from .models_connect import ConnectAccount, ConnectLedgerEntry
from .models_line_f import MerchantCommissionRate
from .payments_gateway import GatewayError
from .settlement_basis import settlement_basis

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")
# F5 platform default, mirrors routers.admin_v2.DEFAULT_COMMISSION_RATE.
DEFAULT_COMMISSION_RATE = Decimal("0.10")
CURRENCY = "HKD"


def connect_configured() -> bool:
    return bool(config.connect_enabled().get("enabled"))


def _max_retries() -> int:
    """Failed attempts per entry before it goes needs_manual. Read at
    call time so tests can monkeypatch config."""
    return int(config.CONNECT_MAX_TRANSFER_RETRIES)


def effective_commission_rate(db: Session, shop_id: int) -> Decimal:
    configured = (db.query(MerchantCommissionRate)
                  .filter(MerchantCommissionRate.shop_id == shop_id).first())
    if configured is not None:
        return Decimal(str(configured.rate))
    return DEFAULT_COMMISSION_RATE


def get_account(db: Session, shop_id: int):
    return db.query(ConnectAccount).filter_by(shop_id=shop_id).first()


def account_ready(account) -> bool:
    return bool(account is not None and account.charges_enabled
                and account.payouts_enabled)


def merchant_balance(db: Session, shop_id: int) -> dict:
    entries = db.query(ConnectLedgerEntry).filter_by(shop_id=shop_id).all()
    pending = sum((Decimal(str(e.net_amount)) for e in entries
                   if e.kind == "transfer"
                   and e.status in ("pending", "needs_manual")),
                  Decimal("0"))
    transferred = sum((Decimal(str(e.net_amount)) for e in entries
                       if e.kind == "transfer" and e.status == "transferred"),
                      Decimal("0"))
    paid_out = sum((-Decimal(str(e.net_amount)) for e in entries
                    if e.kind == "payout" and e.status == "paid_out"),
                   Decimal("0"))
    processing = sum((-Decimal(str(e.net_amount)) for e in entries
                      if e.kind == "payout" and e.status == "processing"),
                     Decimal("0"))
    return {"pending": pending,
            "available": transferred - paid_out - processing,
            "paid_out": paid_out, "processing": processing}


def _money(value) -> float:
    return float(Decimal(str(value)).quantize(CENT))


def account_out(db: Session, account, shop_id: int) -> dict:
    return {
        "shop_id": shop_id,
        "stripe_account_id": account.stripe_account_id if account else None,
        "onboarding_status": account.onboarding_status if account else "pending",
        "charges_enabled": bool(account.charges_enabled) if account else False,
        "payouts_enabled": bool(account.payouts_enabled) if account else False,
        "details_submitted": bool(account.details_submitted) if account else False,
        "manual_payouts": bool(account.manual_payouts) if account else False,
        "connect_configured": connect_configured(),
        "payout_hold_days": config.STRIPE_CONNECT_PAYOUT_HOLD_DAYS,
    }


def sync_account(db: Session, account: ConnectAccount) -> ConnectAccount:
    """Refresh readiness flags from Stripe (source of truth). Sets the
    account to manual payouts once payouts are enabled. Raises 503
    when Connect is not configured, 502 when the gateway fails."""
    if not connect_configured():
        raise HTTPException(
            status_code=503,
            detail="Stripe Connect 未配置：请先在部署环境开启 "
                   "STRIPE_CONNECT_ENABLED")
    try:
        info = connect_gateway.retrieve_account(account.stripe_account_id)
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=f"Stripe 查询帐户失败：{exc}")
    account.charges_enabled = bool(info.get("charges_enabled"))
    account.payouts_enabled = bool(info.get("payouts_enabled"))
    account.details_submitted = bool(info.get("details_submitted"))
    account.onboarding_status = (
        "active" if account.charges_enabled and account.payouts_enabled
        else "incomplete")
    if account.payouts_enabled and not account.manual_payouts:
        try:
            connect_gateway.set_manual_payouts(account.stripe_account_id)
            account.manual_payouts = True
        except GatewayError as exc:
            # Sync still succeeds; manual stays False and the next sync
            # retries. Payouts stay blocked until manual is set, so no
            # money can leak through an automatic schedule.
            logger.warning("Connect set_manual_payouts failed for %s: %s",
                           account.stripe_account_id, exc)
    return account


def _maybe_notify_manual(db: Session, entry: ConnectLedgerEntry,
                         title: str):
    """Raise the admin notification for a needs_manual entry, once.
    Broadcast (user_id NULL) so every admin sees it; the body carries
    the entry id for the recoveries board."""
    if entry.manual_notified:
        return
    entry.manual_notified = True
    db.add(Notification(
        user_id=None, title=title,
        body=(f"Connect 流水 #{entry.id}（商户 shop_id={entry.shop_id}）"
              f"已失败 {entry.retry_count} 次，需人工处理：{entry.note or ''}"),
        notification_type="connect_manual_review",
        target_shop_id=entry.shop_id))


def record_redemption(db: Session, red: models.Redemption) -> dict:
    """Post-redemption hook (called from scan_coupon, same transaction).

    Writes the pending ledger entry ONLY — it never calls Stripe
    (queue §F item 3). The scheduler job ``connect-transfers`` (or
    the manual retry endpoint) moves the money afterwards, so a slow
    or failing Stripe can never delay or fail the redemption itself.
    Never raises.
    """
    coupon = db.query(models.Coupon).filter(
        models.Coupon.id == red.coupon_id).first()
    basis = settlement_basis(db, coupon, redemption=red)
    if basis is None or basis <= 0:
        return {"transfer": "skipped_zero", "net_amount": 0.0,
                "gross_amount": 0.0, "commission": 0.0}
    from .settlement_ledger import redemption_commission_rate
    rate = redemption_commission_rate(red, effective_commission_rate(db, red.shop_id))
    commission = (basis * rate).quantize(CENT)
    net = basis - commission
    if net <= 0:
        return {"transfer": "skipped_zero", "net_amount": 0.0,
                "gross_amount": _money(basis), "commission": _money(commission)}
    entry = ConnectLedgerEntry(
        shop_id=red.shop_id, redemption_id=red.id, kind="transfer",
        status="pending", gross_amount=basis, commission=commission,
        net_amount=net)
    db.add(entry)
    db.flush()
    result = {"transfer": "pending", "entry_id": entry.id,
              "gross_amount": _money(basis), "commission": _money(commission),
              "net_amount": _money(net), "stripe_transfer_id": None}
    if not connect_configured():
        entry.note = "not_configured"
        result["transfer"] = "not_configured"
        result["reason"] = "not_configured"
        return result
    account = get_account(db, red.shop_id)
    if account is None:
        entry.note = "no_account"
        result["reason"] = "no_account"
        return result
    if not account_ready(account):
        entry.note = "account_not_ready"
        result["reason"] = "account_not_ready"
        return result
    result["reason"] = "queued_for_transfer"
    return result


def _attempt_transfer(db: Session, entry: ConnectLedgerEntry) -> dict:
    """One transfer attempt for a pending transfer entry. Shared by
    the scheduler sweep and the manual retry endpoint, so both use
    the same idempotency key and the same failure accounting.

    Only a real gateway failure counts toward retry_count; waiting
    states (not_configured / no_account / account_not_ready) do not.
    After CONNECT_MAX_TRANSFER_RETRIES failures the entry goes
    needs_manual and an admin notification is raised once.
    Returns {"transferred": bool, "reason": str, ...}. Never raises.
    """
    if not connect_configured():
        entry.note = "not_configured"
        return {"transferred": False, "reason": "not_configured"}
    account = get_account(db, entry.shop_id)
    if account is None:
        entry.note = "no_account"
        return {"transferred": False, "reason": "no_account"}
    if not account_ready(account):
        entry.note = "account_not_ready"
        return {"transferred": False, "reason": "account_not_ready"}
    try:
        transfer = connect_gateway.create_transfer(
            account.stripe_account_id, entry.net_amount,
            transfer_group=f"coupon-go-redemption-{entry.redemption_id}",
            idempotency_key=f"transfer-{entry.id}")
    except Exception as exc:  # noqa: BLE001 — caller decides, never crash
        entry.retry_count = (entry.retry_count or 0) + 1
        entry.note = f"failed: {exc}"[:255]
        logger.warning("Connect transfer failed for entry %s: %s",
                       entry.id, exc)
        if entry.retry_count >= _max_retries():
            entry.status = "needs_manual"
            entry.note = f"needs_manual: {exc}"[:255]
            _maybe_notify_manual(db, entry, "Connect 转帐多次失败，需人工处理")
        return {"transferred": False, "reason": "failed"}
    entry.status = "transferred"
    entry.stripe_transfer_id = transfer.get("transfer_id", "")
    entry.retry_count = 0
    entry.note = ""
    return {"transferred": True, "reason": "transferred",
            "stripe_transfer_id": entry.stripe_transfer_id}


def sweep_pending_transfers(db: Session, batch_size=None) -> dict:
    """Scheduler job ``connect-transfers``: move queued transfers to
    Stripe, oldest first, at most batch_size per run. One item never
    blocks the rest: each item commits on its own, and an unexpected
    error rolls back just that item and counts as failed. Entries
    already needs_manual are left for manual retry.
    Returns counts; still_pending is the number of transfer entries
    still pending after this run (whole book, not just this batch)."""
    size = (batch_size if batch_size is not None
            else config.SCHEDULER_CONNECT_BATCH_SIZE)
    rows = (db.query(ConnectLedgerEntry)
            .filter_by(kind="transfer", status="pending")
            .order_by(ConnectLedgerEntry.id).limit(size).all())
    out = {"processed": 0, "transferred": 0, "needs_manual": 0,
           "failed": 0, "still_pending": 0}
    for row in rows:
        out["processed"] += 1
        try:
            locked = (db.query(ConnectLedgerEntry)
                      .filter_by(id=row.id).with_for_update()
                      .populate_existing().first())
            target = locked or row
            if target.status != "pending":
                continue  # claimed by a concurrent retry meanwhile
            result = _attempt_transfer(db, target)
            db.commit()
            if result["transferred"]:
                out["transferred"] += 1
            elif target.status == "needs_manual":
                out["needs_manual"] += 1
        except Exception:
            db.rollback()
            out["failed"] += 1
            logger.exception("Connect transfer sweep failed entry=%s",
                             row.id)
    out["still_pending"] = (db.query(ConnectLedgerEntry)
                            .filter_by(kind="transfer", status="pending")
                            .count())
    return out


def _attempt_reversal(db: Session, entry: ConnectLedgerEntry) -> dict:
    """Transfer Reversal for an already-transferred entry. Returns
    {"reversed": bool, "reason": str}. Never raises: on failure the
    entry keeps its transferred status and is flagged pending_reversal
    for the retry endpoint. A gateway failure counts toward
    retry_count; after CONNECT_MAX_TRANSFER_RETRIES an admin
    notification is raised once."""
    account = get_account(db, entry.shop_id)
    if not connect_configured():
        entry.pending_reversal = True
        entry.note = "reversal_pending: not_configured"
        return {"reversed": False, "reason": "not_configured"}
    if account is None:
        entry.pending_reversal = True
        entry.note = "reversal_pending: no_account"
        return {"reversed": False, "reason": "no_account"}
    try:
        reversal = connect_gateway.reverse_transfer(
            entry.stripe_transfer_id, entry.net_amount,
            idempotency_key=f"reversal-{entry.id}")
    except Exception as exc:  # noqa: BLE001 — caller decides, never crash
        entry.pending_reversal = True
        entry.retry_count = (entry.retry_count or 0) + 1
        entry.note = f"reversal_pending: failed: {exc}"[:255]
        logger.warning("Connect reversal failed for entry %s: %s",
                       entry.id, exc)
        if entry.retry_count >= _max_retries():
            _maybe_notify_manual(db, entry, "Connect 冲回多次失败，需人工处理")
        return {"reversed": False, "reason": "failed"}
    entry.status = "reversed"
    entry.stripe_reversal_id = reversal.get("reversal_id", "")
    entry.pending_reversal = False
    entry.retry_count = 0
    entry.note = "reversed"
    return {"reversed": True, "reason": "reversed",
            "stripe_reversal_id": entry.stripe_reversal_id}


def reverse_redemption(db: Session, red: models.Redemption,
                       reason: str) -> dict:
    """Reversal hook (called from revoke and from the package-refund
    loop, same transaction). Idempotent: an already-reversed entry is
    a no-op. Returns {"reversed": bool, ...}."""
    entry = (db.query(ConnectLedgerEntry)
             .filter_by(redemption_id=red.id, kind="transfer").first())
    if entry is None:
        return {"reversed": False, "reason": "no_entry"}
    if entry.status == "reversed":
        return {"reversed": True, "reason": "already_reversed",
                "stripe_reversal_id": entry.stripe_reversal_id or None}
    if entry.status == "pending":
        entry.status = "reversed"
        entry.note = reason
        return {"reversed": True, "reason": reason,
                "stripe_reversal_id": None}
    out = _attempt_reversal(db, entry)
    if out["reversed"]:
        entry.note = reason
    return out


def retry_entry(db: Session, entry_id: int) -> dict:
    """Retry a pending transfer, a needs_manual transfer, or a pending
    reversal. The entry row is locked first (SELECT ... FOR UPDATE)
    and its status re-checked inside the lock, so two concurrent
    retries cannot both transfer. Raises 404 (no entry), 409
    (nothing to retry), 503 (Connect not configured), 502 (gateway
    failure — the retry_count / note update is committed before the
    raise, so the failure is recorded)."""
    entry = (db.query(ConnectLedgerEntry).filter_by(id=entry_id)
             .with_for_update().populate_existing().first())
    if entry is None:
        raise HTTPException(status_code=404, detail="找不到此流水")
    if entry.kind != "transfer":
        raise HTTPException(status_code=409, detail="提款流水无需重试")
    if entry.status == "reversed":
        raise HTTPException(status_code=409, detail="此笔已冲回，无需重试")
    if entry.pending_reversal:
        out = _attempt_reversal(db, entry)
        if not out["reversed"]:
            db.commit()  # persist retry_count / note before the 502
            raise HTTPException(status_code=502,
                                detail=f"冲回仍未成功（{out['reason']}），请稍后重试")
        db.commit()
        return {"entry_id": entry.id, "status": "reversed",
                "stripe_transfer_id": entry.stripe_transfer_id or None,
                "stripe_reversal_id": entry.stripe_reversal_id or None,
                "reversed": True}
    if entry.status == "transferred":
        raise HTTPException(status_code=409, detail="此笔已转帐，无需重试")
    if entry.status not in ("pending", "needs_manual"):
        raise HTTPException(status_code=409, detail="此笔状态无需重试")
    if not connect_configured():
        raise HTTPException(
            status_code=503,
            detail="Stripe Connect 未配置：请先在部署环境开启 "
                   "STRIPE_CONNECT_ENABLED")
    out = _attempt_transfer(db, entry)
    if out["transferred"]:
        db.commit()
        return {"entry_id": entry.id, "status": "transferred",
                "stripe_transfer_id": entry.stripe_transfer_id,
                "reversed": False}
    if out["reason"] in ("no_account", "account_not_ready"):
        db.commit()  # persist the note
        return {"entry_id": entry.id, "status": entry.status,
                "stripe_transfer_id": None, "reversed": False,
                "reason": out["reason"]}
    db.commit()  # persist retry_count / needs_manual before the 502
    raise HTTPException(status_code=502,
                        detail=f"Stripe 转帐失败：{entry.note}")


def request_payout(db: Session, shop: models.Shop, amount) -> dict:
    """Merchant-initiated withdrawal. Raises 503 (not configured),
    409 (account not ready), 422 (bad amount / exceeds available),
    502 (gateway failure — the payout entry is then marked failed and
    the balance is released).

    Concurrency (queue §F item 2): the merchant account row is locked
    FIRST and the balance is re-read inside the lock; a ``processing``
    entry (balance already deducted) is written and COMMITTED before
    Stripe is called, so a concurrent payout sees it and is rejected
    when the remaining balance is insufficient."""
    if not connect_configured():
        raise HTTPException(
            status_code=503,
            detail="Stripe Connect 未配置：请先在部署环境开启 "
                   "STRIPE_CONNECT_ENABLED")
    try:
        amount_dec = Decimal(str(amount)).quantize(CENT)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=422, detail="提款金额无效")
    if amount_dec <= 0:
        raise HTTPException(status_code=422, detail="提款金额须大于 0")
    account = (db.query(ConnectAccount).filter_by(shop_id=shop.id)
               .with_for_update().populate_existing().first())
    if account is None or not account.payouts_enabled:
        raise HTTPException(status_code=409, detail="商户 Stripe 帐户未就绪，暂不能提款")
    balance = merchant_balance(db, shop.id)
    if amount_dec > balance["available"]:
        raise HTTPException(status_code=422, detail="提款金额超过可提取余额")
    entry = ConnectLedgerEntry(
        shop_id=shop.id, redemption_id=None, kind="payout",
        status="processing", gross_amount=Decimal("0"),
        commission=Decimal("0"), net_amount=-amount_dec,
        stripe_payout_id="")
    db.add(entry)
    db.flush()  # entry.id is the idempotency key seed
    db.commit()  # processing entry visible now; balance already deducted
    try:
        payout = connect_gateway.create_payout(
            account.stripe_account_id, amount_dec,
            idempotency_key=f"payout-{entry.id}")
    except GatewayError as exc:
        entry.status = "failed"
        entry.note = f"failed: {exc}"[:255]
        db.commit()
        raise HTTPException(status_code=502, detail=f"Stripe 提款失败：{exc}")
    entry.status = "paid_out"
    entry.stripe_payout_id = payout.get("payout_id", "")
    entry.note = ""
    balance = merchant_balance(db, shop.id)
    return {"shop_id": shop.id, "amount": _money(amount_dec),
            "currency": CURRENCY,
            "stripe_payout_id": entry.stripe_payout_id,
            "status": "paid_out",
            "balance": {"pending": _money(balance["pending"]),
                        "available": _money(balance["available"]),
                        "paid_out": _money(balance["paid_out"]),
                        "processing": _money(balance["processing"])}}


def negative_balances(db: Session) -> list:
    """Shops whose available balance is negative (a transfer was
    reversed after the money was already paid out). While negative,
    request_payout refuses any new payout; the admin recoveries board
    lists these shops for follow-up."""
    shop_ids = [r[0] for r in
                db.query(ConnectLedgerEntry.shop_id).distinct().all()]
    out = []
    for shop_id in shop_ids:
        bal = merchant_balance(db, shop_id)
        if bal["available"] < 0:
            out.append({"shop_id": shop_id,
                        "pending": _money(bal["pending"]),
                        "available": _money(bal["available"]),
                        "paid_out": _money(bal["paid_out"]),
                        "processing": _money(bal["processing"])})
    return out


def recoveries(db: Session) -> dict:
    """Admin board (queue §F item 6): negative balances to chase,
    transfers stuck in needs_manual, and reversals still pending."""
    def _min(entry):
        return {"id": entry.id, "shop_id": entry.shop_id,
                "kind": entry.kind, "status": entry.status,
                "redemption_id": entry.redemption_id,
                "net_amount": _money(entry.net_amount),
                "retry_count": entry.retry_count or 0,
                "note": entry.note or ""}

    needs_manual = (db.query(ConnectLedgerEntry)
                    .filter_by(kind="transfer", status="needs_manual")
                    .order_by(ConnectLedgerEntry.id).all())
    pending_reversals = (db.query(ConnectLedgerEntry)
                         .filter_by(pending_reversal=True)
                         .order_by(ConnectLedgerEntry.id).all())
    return {"negative_balances": negative_balances(db),
            "needs_manual_transfers": [_min(e) for e in needs_manual],
            "pending_reversals": [_min(e) for e in pending_reversals]}
