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
  ledger entry id. A transfer whose result is unknown is frozen for
  reconciliation rather than automatically retried.
- Payouts lock the shop, reserve balance and commit a ``processing``
  entry before calling Stripe. An uncertain provider response keeps
  the money reserved until the operation is verified, and blocks
  additional payouts even when the remaining balance is sufficient.
- A balance MAY go negative (a transfer reversed after the merchant
  already paid the money out). While negative, new payouts are
  refused; the admin recoveries board lists the shop.
- An uncertain transfer or reversal is recorded and reported for
  provider reconciliation; it is not automatically resent. Legacy
  ``needs_manual`` entries remain visible for review.
- A later scan of the same voucher is held while its previous transfer
  or reversal remains unresolved; a confirmed reversal permits retry.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from . import config, connect_gateway, models
from .models import Notification
from .models_connect import ConnectAccount, ConnectLedgerEntry
from .models_line_b import AdminAuditLog
from .models_line_f import MerchantCommissionRate
from .payments_gateway import GatewayError
from .settlement_basis import settlement_basis
from .settlement_ledger import lock_shop

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")
# F5 platform default, mirrors routers.admin_v2.DEFAULT_COMMISSION_RATE.
DEFAULT_COMMISSION_RATE = Decimal("0.10")
CURRENCY = "HKD"


def connect_configured() -> bool:
    key = (config.STRIPE_SECRET_KEY or "").strip()
    return bool(config.connect_enabled().get("enabled") and
                (key.startswith("sk_test_") or (not key and config.APP_ENV == "dev")))


def connect_mode() -> str:
    """Current runtime mode; this does not assert a historical entry's origin."""
    if not connect_configured():
        return "unavailable"
    return "stripe_test" if (config.STRIPE_SECRET_KEY or "").strip() else "simulated"



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
                and account.payouts_enabled and account.transfers_enabled)


def merchant_balance(db: Session, shop_id: int) -> dict:
    entries = db.query(ConnectLedgerEntry).filter_by(shop_id=shop_id).all()
    pending = sum((Decimal(str(e.net_amount)) for e in entries
                   if e.kind == "transfer"
                   and e.status in ("pending", "needs_manual", "transferring", "transfer_unknown")
                   and not e.pending_reversal),
                  Decimal("0"))
    transferred = sum((Decimal(str(e.net_amount)) for e in entries
                       if e.kind == "transfer" and e.status == "transferred"
                       and not e.pending_reversal),
                      Decimal("0"))
    paid_out = sum((-Decimal(str(e.net_amount)) for e in entries
                    if e.kind == "payout" and e.status == "paid_out"),
                   Decimal("0"))
    processing = sum((-Decimal(str(e.net_amount)) for e in entries
                      if e.kind == "payout" and e.status in ("processing", "payout_unknown", "submitted")),
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
        "onboarding_status": (("incomplete" if account.onboarding_status == "active" and not account_ready(account)
                               else account.onboarding_status) if account else "pending"),
        "charges_enabled": bool(account.charges_enabled) if account else False,
        "payouts_enabled": bool(account.payouts_enabled) if account else False,
        "transfers_enabled": bool(account.transfers_enabled) if account else False,
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
            detail="Stripe Connect 尚未配置，請聯絡平台")
    try:
        info = connect_gateway.retrieve_account(account.stripe_account_id)
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=f"Stripe 查询帐户失败：{exc}")
    account.charges_enabled = bool(info.get("charges_enabled"))
    account.payouts_enabled = bool(info.get("payouts_enabled"))
    account.transfers_enabled = bool(info.get("transfers_enabled"))
    account.details_submitted = bool(info.get("details_submitted"))
    account.onboarding_status = (
        "active" if account.charges_enabled and account.payouts_enabled and account.transfers_enabled
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


def ensure_prior_transfer_closed(db: Session, wallet_item_id: int) -> None:
    """Check earlier settlements after the caller locks this wallet item.

    A closed prior transfer is either proven never sent, or has both a
    provider transfer and a provider reversal. NULL dispatch markers are
    legacy history and cannot establish that money never left the platform.
    """
    entries = (db.query(ConnectLedgerEntry)
               .join(models.Redemption, models.Redemption.id == ConnectLedgerEntry.redemption_id)
               .filter(models.Redemption.wallet_item_id == wallet_item_id,
                       ConnectLedgerEntry.kind == "transfer")
               .with_for_update(of=ConnectLedgerEntry).all())
    for entry in entries:
        cancelled_unsent = (entry.status == "reversed" and entry.dispatch_started is False
                            and not entry.stripe_transfer_id and not entry.pending_reversal)
        confirmed_reversal = (entry.status == "reversed" and entry.stripe_transfer_id
                              and entry.stripe_reversal_id and not entry.pending_reversal)
        if not (cancelled_unsent or confirmed_reversal):
            raise HTTPException(409, "上一筆核銷的商戶結算仍待確認，請聯絡平台處理後再試")


def record_redemption(db: Session, red: models.Redemption) -> dict:
    """Post-redemption hook (called from scan_coupon, same transaction).

    Writes the pending ledger entry ONLY — it never calls Stripe
    (queue §F item 3). The scheduler job ``connect-transfers`` (or
    the manual retry endpoint) moves the money afterwards, so a slow
    or failing Stripe can never delay the redemption itself. A prior
    unresolved transfer raises 409 rather than creating duplicate dues.
    """
    ensure_prior_transfer_closed(db, red.wallet_item_id)
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
        net_amount=net, dispatch_started=False)
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

    Waiting states do not count as attempts. A provider exception can
    mean that funds moved, so freeze it and notify the admin instead of
    assuming a retry is safe after the idempotency retention window.
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
    if entry.dispatch_started is not False:
        # Legacy pending records cannot prove that no provider request
        # succeeded before its original transaction was lost.
        entry.status = "transfer_unknown"
        entry.note = "legacy_transfer_result_unknown"
        _maybe_notify_manual(db, entry, "Connect 旧转帐结果待对账")
        return {"transferred": False, "reason": "transfer_unknown"}
    # Commit evidence that the dispatch has started before crossing the
    # provider boundary. A crash after this commit remains ambiguous and
    # must never be mistaken for a transfer that was never sent.
    entry_id, redemption_id = entry.id, entry.redemption_id
    amount, account_id = entry.net_amount, account.stripe_account_id
    entry.transfer_account_id = account_id
    entry.dispatch_started = True
    entry.status = "transferring"
    entry.note = "transfer_started"
    db.commit()
    try:
        transfer = connect_gateway.create_transfer(
            account_id, amount,
            transfer_group=f"coupon-go-redemption-{redemption_id}",
            idempotency_key=f"transfer-{entry_id}")
        transfer_id = transfer.get("transfer_id")
        if not transfer_id:
            raise ValueError("provider response omitted transfer id")
    except Exception:  # An error can occur after the provider moved funds.
        db.rollback()
        current = (db.query(ConnectLedgerEntry).filter_by(id=entry_id)
                   .with_for_update().populate_existing().one())
        current.status = "transfer_unknown"
        current.retry_count = (current.retry_count or 0) + 1
        current.note = "transfer_result_unknown"
        _maybe_notify_manual(db, current, "Connect 转帐结果待对账")
        db.flush()
        return {"transferred": False, "reason": "transfer_unknown"}
    entry = (db.query(ConnectLedgerEntry).filter_by(id=entry_id)
             .with_for_update().populate_existing().one())
    entry.status = "transferred"
    entry.stripe_transfer_id = transfer_id
    entry.retry_count = 0
    entry.note = "reversal_queued" if entry.pending_reversal else ""
    db.flush()
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
    # Reversals are dispatched only after the business transaction that
    # requested them has committed. Older failed reversals are ambiguous;
    # only this version's explicit, durable queue marker may be dispatched.
    reversal_ids = (db.query(ConnectLedgerEntry.id)
                    .filter_by(kind="transfer", status="transferred",
                               pending_reversal=True, note="reversal_queued")
                    .order_by(ConnectLedgerEntry.id).limit(size).all())
    for (entry_id,) in reversal_ids:
        try:
            entry = (db.query(ConnectLedgerEntry).filter_by(id=entry_id)
                     .with_for_update().populate_existing().one())
            if entry.status != "transferred" or not entry.pending_reversal or entry.note != "reversal_queued":
                db.rollback()
                continue
            _attempt_reversal(db, entry)
            db.commit()
        except Exception:
            db.rollback()
            out["failed"] += 1
            logger.exception("Connect reversal sweep failed entry=%s", entry_id)
    # A crash after dispatch leaves an intentionally non-retryable marker.
    # Surface stale/legacy ambiguous rows to admins rather than leaving them
    # silently blocked forever. Never send another provider request here.
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=15)
    stale = (db.query(ConnectLedgerEntry.id)
             .filter(ConnectLedgerEntry.kind == "transfer",
                     ConnectLedgerEntry.status == "transferred",
                     ConnectLedgerEntry.pending_reversal.is_(True),
                     ConnectLedgerEntry.note != "reversal_queued",
                     ConnectLedgerEntry.manual_notified.is_(False),
                     ConnectLedgerEntry.updated_at < cutoff)
             .order_by(ConnectLedgerEntry.id).limit(size).all())
    for (entry_id,) in stale:
        try:
            entry = (db.query(ConnectLedgerEntry).filter_by(id=entry_id)
                     .with_for_update().populate_existing().one())
            if (entry.pending_reversal and entry.status == "transferred" and
                    entry.note != "reversal_queued" and not entry.manual_notified and
                    entry.updated_at < cutoff):
                _maybe_notify_manual(db, entry, "Connect 冲回结果待人工核对")
            db.commit()
        except Exception:
            db.rollback()
            out["failed"] += 1
            logger.exception("Connect reversal notification failed entry=%s", entry_id)
    # Poll submitted payouts and revisit paid payouts: Stripe can report a
    # failure after initially reporting payment. Separate batches prevent old
    # paid payouts from delaying confirmation of newly submitted ones.
    payouts = (db.query(ConnectLedgerEntry.id, ConnectLedgerEntry.stripe_payout_id)
               .filter(ConnectLedgerEntry.kind == "payout",
                       ConnectLedgerEntry.status == "submitted",
                       ConnectLedgerEntry.stripe_payout_id != "")
               .order_by(ConnectLedgerEntry.payout_last_polled_at.asc().nullsfirst(),
                         ConnectLedgerEntry.id).limit(size).all())
    paid_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    payouts += (db.query(ConnectLedgerEntry.id, ConnectLedgerEntry.stripe_payout_id)
                .filter(ConnectLedgerEntry.kind == "payout",
                        ConnectLedgerEntry.status == "paid_out",
                        ConnectLedgerEntry.stripe_payout_id != "",
                        ConnectLedgerEntry.payout_operation_id.isnot(None),
                        or_(ConnectLedgerEntry.payout_last_polled_at.is_(None),
                            ConnectLedgerEntry.payout_last_polled_at < paid_cutoff))
                .order_by(ConnectLedgerEntry.payout_last_polled_at.asc().nullsfirst(), ConnectLedgerEntry.id)
                .limit(size).all())
    if not connect_configured():
        payouts = []
    out["payouts_checked"] = 0
    for entry_id, provider_id in payouts:
        try:
            # Rotate even malformed or unavailable legacy entries so a
            # full batch of them cannot starve other merchants forever.
            db.query(ConnectLedgerEntry).filter(ConnectLedgerEntry.id == entry_id,
                    ConnectLedgerEntry.status.in_(("submitted", "paid_out"))).update(
                {"payout_last_polled_at": datetime.now(timezone.utc).replace(tzinfo=None)},
                synchronize_session=False)
            db.commit()
            reconcile_payout(db, entry_id, provider_id)
            out["payouts_checked"] += 1
        except Exception:
            db.rollback()
            out["failed"] += 1
            logger.exception("Connect payout status check failed entry=%s", entry_id)
    out["still_pending"] = (db.query(ConnectLedgerEntry)
                            .filter_by(kind="transfer", status="pending")
                            .count())
    return out


def _attempt_reversal(db: Session, entry: ConnectLedgerEntry) -> dict:
    """Transfer Reversal for an already-transferred entry. Returns
    {"reversed": bool, "reason": str}. Never raises: on failure the
    entry keeps its transferred status and pending_reversal. An uncertain
    provider response requires reconciliation and is not resent."""
    if entry.note != "reversal_queued":
        return {"reversed": False, "reason": "reversal_result_unknown"}
    account = get_account(db, entry.shop_id)
    if not connect_configured():
        entry.pending_reversal = True
        return {"reversed": False, "reason": "not_configured"}
    if account is None:
        entry.pending_reversal = True
        return {"reversed": False, "reason": "no_account"}
    if not entry.stripe_transfer_id:
        entry.note = "reversal_result_unknown"
        _maybe_notify_manual(db, entry, "Connect 冲回缺少转帐编号，需人工核对")
        return {"reversed": False, "reason": "reversal_result_unknown"}
    entry_id, transfer_id, amount = entry.id, entry.stripe_transfer_id, entry.net_amount
    # This commit is intentionally outside the originating revoke/refund
    # transaction: it records an irreversible dispatch boundary.
    entry.note = "reversal_dispatch_started"
    db.commit()
    try:
        reversal = connect_gateway.reverse_transfer(
            transfer_id, amount, idempotency_key=f"reversal-{entry_id}")
        if not reversal.get("reversal_id"):
            raise ValueError("provider response omitted reversal id")
    except Exception as exc:  # noqa: BLE001 — caller decides, never crash
        db.rollback()
        entry = (db.query(ConnectLedgerEntry).filter_by(id=entry_id)
                 .with_for_update().populate_existing().one())
        entry.pending_reversal = True
        entry.retry_count = (entry.retry_count or 0) + 1
        entry.note = "reversal_result_unknown"
        logger.warning("Connect reversal failed for entry %s: %s",
                       entry.id, exc)
        _maybe_notify_manual(db, entry, "Connect 冲回结果待人工核对")
        return {"reversed": False, "reason": "reversal_result_unknown"}
    entry = (db.query(ConnectLedgerEntry).filter_by(id=entry_id)
             .with_for_update().populate_existing().one())
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
             .filter_by(redemption_id=red.id, kind="transfer")
             .with_for_update().populate_existing().first())
    if entry is None:
        return {"reversed": False, "reason": "no_entry"}
    if entry.status == "reversed":
        return {"reversed": True, "reason": "already_reversed",
                "stripe_reversal_id": entry.stripe_reversal_id or None}
    if entry.status == "pending" and entry.dispatch_started is False:
        entry.status = "reversed"
        entry.note = reason
        return {"reversed": True, "reason": reason,
                "stripe_reversal_id": None}
    if entry.status in ("pending", "needs_manual", "transferring", "transfer_unknown"):
        # Legacy rows and in-flight/failed calls lack a proven outcome.
        entry.pending_reversal = True
        if entry.status in ("pending", "needs_manual"):
            entry.status = "transfer_unknown"
        entry.note = "transfer_result_unknown_reversal_requested"
        return {"reversed": False, "reason": "transfer_result_unknown"}
    # Enqueue with the revoke/refund transaction; never contact Stripe
    # until that transaction is durably committed.
    if not entry.pending_reversal:
        entry.pending_reversal = True
        entry.note = "reversal_queued"
    reason = entry.note if entry.note in ("reversal_queued", "reversal_dispatch_started") else "reversal_result_unknown"
    return {"reversed": False, "reason": reason}


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
    if entry.status in ("transferring", "transfer_unknown"):
        raise HTTPException(status_code=409, detail="上次转帐结果待确认，请先对账")
    if entry.status == "reversed":
        raise HTTPException(status_code=409, detail="此笔已冲回，无需重试")
    if entry.pending_reversal:
        if entry.note != "reversal_queued":
            raise HTTPException(status_code=409, detail="冲回结果待核对，请联系财务处理")
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
            detail="Stripe Connect 尚未配置，請聯絡平台")
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


def _payout_out(db, entry):
    balance = merchant_balance(db, entry.shop_id)
    return {"shop_id": entry.shop_id, "entry_id": entry.id, "connect_mode": connect_mode(),
            "amount": _money(-entry.net_amount), "currency": CURRENCY,
            "stripe_payout_id": entry.stripe_payout_id or None,
            "status": entry.status,
            "balance": {key: _money(value) for key, value in balance.items()}}


def request_payout(db: Session, shop: models.Shop, amount, request_id: str) -> dict:
    """Reserve once before provider I/O; unknown results never release money."""
    if not connect_configured():
        raise HTTPException(503, "Stripe Connect 未配置")
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", request_id):
        raise HTTPException(422, "需要稳定的提款请求编号")
    try:
        amount_dec = Decimal(str(amount))
        if not amount_dec.is_finite() or amount_dec <= 0 or amount_dec * 100 != (amount_dec * 100).to_integral_value():
            raise ValueError
    except Exception:
        raise HTTPException(422, "提款金额无效，最多两位小数") from None
    # Same shop lock as settlement reversal: the funds cannot change between
    # the balance check and the durable reservation.
    shop = lock_shop(db, shop.id)
    previous = db.query(ConnectLedgerEntry).filter_by(
        shop_id=shop.id, kind="payout", payout_request_id=request_id).first()
    if previous is not None:
        if -Decimal(str(previous.net_amount)) != amount_dec:
            raise HTTPException(409, "提款请求编号已用于其他金额")
        return _payout_out(db, previous)
    unresolved = db.query(ConnectLedgerEntry.id).filter(
        ConnectLedgerEntry.shop_id == shop.id, ConnectLedgerEntry.kind == "payout",
        ConnectLedgerEntry.status.in_(("processing", "payout_unknown", "submitted"))).first()
    if unresolved:
        raise HTTPException(409, "上筆提款仍待確認，請稍後查看或聯絡財務")
    account = get_account(db, shop.id)
    if account is None or not account.stripe_account_id or not account.payouts_enabled or not account.manual_payouts:
        raise HTTPException(409, "商户 Stripe 帐户未就绪，暂不能提款")
    if amount_dec > merchant_balance(db, shop.id)["available"]:
        raise HTTPException(422, "提款金额超过可提取余额")
    entry = ConnectLedgerEntry(shop_id=shop.id, kind="payout", status="processing",
        gross_amount=Decimal("0"), commission=Decimal("0"), net_amount=-amount_dec,
        payout_request_id=request_id, payout_operation_id=uuid4().hex,
        payout_account_id=account.stripe_account_id,
        payout_started_at=datetime.now(timezone.utc).replace(tzinfo=None),
        stripe_payout_id="", note="stripe_request_started")
    db.add(entry)
    db.flush()
    entry_id, shop_id, operation_id = entry.id, shop.id, entry.payout_operation_id
    account_id = entry.payout_account_id
    db.commit()  # reservation and provider operation identity are durable
    try:
        payout = connect_gateway.create_payout(account_id, amount_dec,
                                                idempotency_key=f"payout-{operation_id}")
        payout_id = payout.get("payout_id")
        if (not re.fullmatch(r"po_[A-Za-z0-9_]+", payout_id or "") or
                Decimal(str(payout.get("amount"))) != amount_dec or
                str(payout.get("currency", "")).upper() != CURRENCY):
            raise ValueError("payout response mismatch")
    except Exception:
        db.rollback()
        lock_shop(db, shop_id)
        current = (db.query(ConnectLedgerEntry).filter_by(id=entry_id)
                   .with_for_update().populate_existing().one())
        if current.status == "processing":
            current.status, current.note = "payout_unknown", "stripe_result_unknown_reconcile_required"
            db.commit()
        raise HTTPException(502, "提款結果待確認，請勿重複提交") from None
    lock_shop(db, shop_id)
    current = (db.query(ConnectLedgerEntry).filter_by(id=entry_id)
               .with_for_update().populate_existing().one())
    if current.status != "processing":
        return _payout_out(db, current)
    current.stripe_payout_id = payout_id
    provider_status = payout.get("status")
    if provider_status in ("paid", "succeeded"):
        current.status, current.note = "paid_out", ""
        current.payout_last_polled_at = datetime.now(timezone.utc).replace(tzinfo=None)
    elif provider_status in ("pending", "in_transit"):
        current.status, current.note = "submitted", "awaiting_stripe_payout_status"
    elif provider_status in ("failed", "canceled"):
        current.status, current.note = "failed", "stripe_confirmed_payout_failed"
    else:
        current.status, current.note = "payout_unknown", "stripe_result_unknown_reconcile_required"
    db.commit()
    return _payout_out(db, current)


def reconcile_payout(db: Session, entry_id: int, provider_id: str, admin_id: int | None = None) -> dict:
    """Verify provider identity and amount before changing a held balance."""
    if not connect_configured():
        raise HTTPException(503, "Stripe Connect 未配置")
    entry = db.get(ConnectLedgerEntry, entry_id)
    if entry is None or entry.kind != "payout":
        raise HTTPException(404, "找不到此提款流水")
    shop_id = entry.shop_id
    lock_shop(db, shop_id)
    entry = (db.query(ConnectLedgerEntry).filter_by(id=entry_id)
             .with_for_update().populate_existing().one())
    if entry.status not in ("processing", "payout_unknown", "submitted", "paid_out"):
        raise HTTPException(409, "此提款狀態不需要核對")
    if entry.status == "processing" and (not entry.payout_started_at or
            datetime.now(timezone.utc).replace(tzinfo=None) - entry.payout_started_at < timedelta(minutes=2)):
        raise HTTPException(409, "提款可能仍在處理，請稍後再核對")
    if not re.fullmatch(r"po_[A-Za-z0-9_]+", provider_id or ""):
        raise HTTPException(422, "Stripe 打款編號無效")
    if not entry.payout_operation_id or not entry.payout_account_id:
        raise HTTPException(409, "舊提款缺少可核對的操作資料，請人工處理")
    if entry.stripe_payout_id and entry.stripe_payout_id != provider_id:
        raise HTTPException(409, "Stripe 打款編號與流水不一致")
    status, account_id, operation_id = entry.status, entry.payout_account_id, entry.payout_operation_id
    cents = connect_gateway._to_cents(-entry.net_amount)
    entry.payout_last_polled_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()  # lookup is read-only; do not hold locks across network I/O
    try:
        obj = connect_gateway.retrieve_payout(account_id, provider_id)
    except GatewayError:
        raise HTTPException(502, "無法從 Stripe 確認提款；款項仍保留") from None
    if (obj.get("id") != provider_id or obj.get("amount") != cents or
            str(obj.get("currency", "")).upper() != CURRENCY or
            (obj.get("metadata") or {}).get("connect_operation_key") != f"payout-{operation_id}"):
        raise HTTPException(409, "Stripe 打款資料與流水不一致")
    lock_shop(db, shop_id)
    current = (db.query(ConnectLedgerEntry).filter_by(id=entry_id)
               .with_for_update().populate_existing().one())
    if (current.status != status or current.payout_account_id != account_id or
            current.payout_operation_id != operation_id):
        raise HTTPException(409, "提款狀態已變更，請重新核對")
    provider_status = obj.get("status")
    if provider_status in ("pending", "in_transit") and status != "paid_out":
        current.status = "submitted"
    elif provider_status == "paid":
        current.status = "paid_out"
    elif provider_status in ("failed", "canceled"):
        current.status = "failed"
    else:
        raise HTTPException(409, "Stripe 打款狀態未知，款項仍保留")
    current.stripe_payout_id, current.note = provider_id, "reconciled_from_stripe"
    if admin_id is not None:
        db.add(AdminAuditLog(admin_id=admin_id, action="connect.payout.reconcile",
                             target_type="connect_ledger", target_id=entry_id,
                             detail=f"{status}->{current.status}; stripe_object={provider_id}"))
    db.commit()
    return _payout_out(db, current)


def _reconcile_error(db, entry_id, admin_id, code, message, status=409):
    if admin_id is not None:
        db.add(AdminAuditLog(admin_id=admin_id, action="connect.reconcile.incomplete",
                             target_type="connect_ledger", target_id=entry_id, detail=code))
        db.commit()
    raise HTTPException(status, message)


def _transfer_evidence(obj, entry_id, redemption_id, account_id, cents, provider_id):
    return (cents > 0 and obj.get("id") == provider_id and obj.get("object") == "transfer"
            and obj.get("livemode") is False and obj.get("destination") == account_id
            and type(obj.get("amount")) is int and obj.get("amount") == cents
            and str(obj.get("currency", "")).upper() == CURRENCY
            and obj.get("transfer_group") == f"coupon-go-redemption-{redemption_id}"
            and (obj.get("metadata") or {}).get("connect_operation_key") == f"transfer-{entry_id}"
            and type(obj.get("amount_reversed")) is int
            and 0 <= obj["amount_reversed"] <= cents
            and obj.get("reversed") is (obj["amount_reversed"] == cents))


def reconcile_transfer(db: Session, entry_id: int, provider_id: str, admin_id: int | None = None) -> dict:
    """Resolve an uncertain transfer only from a matching provider object."""
    if not connect_configured():
        raise HTTPException(503, "Stripe Connect 未配置")
    if not re.fullmatch(r"tr_[A-Za-z0-9_]+", provider_id or ""):
        raise HTTPException(422, "Stripe 轉帳編號無效")
    existing = db.get(ConnectLedgerEntry, entry_id)
    if existing is None or existing.kind != "transfer":
        raise HTTPException(404, "找不到此轉帳流水")
    shop_id = existing.shop_id
    lock_shop(db, shop_id)
    entry = (db.query(ConnectLedgerEntry).filter_by(id=entry_id)
             .with_for_update().populate_existing().one())
    if entry.stripe_transfer_id:
        if entry.stripe_transfer_id == provider_id and entry.status in ("transferred", "reversed"):
            return {"entry_id": entry.id, "status": entry.status, "stripe_transfer_id": provider_id,
                    "pending_reversal": bool(entry.pending_reversal), "duplicate": True}
        _reconcile_error(db, entry_id, admin_id, "transfer_id_conflict", "轉帳編號與已有記錄不一致")
    if entry.status not in ("transfer_unknown", "transferring"):
        _reconcile_error(db, entry_id, admin_id, "transfer_state_not_unknown", "此轉帳狀態無需核對")
    if entry.status == "transferring" and (not entry.updated_at or
            datetime.now(timezone.utc).replace(tzinfo=None) - entry.updated_at < timedelta(minutes=2)):
        _reconcile_error(db, entry_id, admin_id, "transfer_in_flight", "轉帳可能仍在處理，請稍後核對")
    if entry.dispatch_started is not True or not entry.transfer_account_id or not entry.redemption_id:
        _reconcile_error(db, entry_id, admin_id, "transfer_legacy_evidence_missing", "舊轉帳缺少原帳戶或操作標識，請人工核對")
    status, account_id, redemption_id = entry.status, entry.transfer_account_id, entry.redemption_id
    cents = connect_gateway._to_cents(entry.net_amount)
    db.commit()
    try:
        obj = connect_gateway.retrieve_transfer(provider_id)
    except GatewayError:
        _reconcile_error(db, entry_id, admin_id, "transfer_provider_unavailable", "無法查詢 Stripe 轉帳，流水仍待核對", 502)
    if not _transfer_evidence(obj, entry_id, redemption_id, account_id, cents, provider_id):
        _reconcile_error(db, entry_id, admin_id, "transfer_evidence_mismatch", "Stripe 轉帳資料不足或與流水不一致")
    lock_shop(db, shop_id)
    current = (db.query(ConnectLedgerEntry).filter_by(id=entry_id)
               .with_for_update().populate_existing().one())
    if current.stripe_transfer_id == provider_id and current.status in ("transferred", "reversed"):
        return {"entry_id": current.id, "status": current.status, "stripe_transfer_id": provider_id,
                "pending_reversal": bool(current.pending_reversal), "duplicate": True}
    if (current.status != status or current.transfer_account_id != account_id or
            current.redemption_id != redemption_id or connect_gateway._to_cents(current.net_amount) != cents):
        _reconcile_error(db, entry_id, admin_id, "transfer_changed_during_lookup", "轉帳資料已變更，請重新核對")
    from .application_connect import _lock_provider_id
    _lock_provider_id(db, provider_id)
    if db.query(ConnectLedgerEntry.id).filter(ConnectLedgerEntry.stripe_transfer_id == provider_id,
                                               ConnectLedgerEntry.id != entry_id).first():
        _reconcile_error(db, entry_id, admin_id, "transfer_object_already_claimed", "此 Stripe 轉帳已關聯其他流水")
    current.status, current.stripe_transfer_id = "transferred", provider_id
    if obj["amount_reversed"]:
        current.pending_reversal, current.note = True, "reversal_result_unknown"
    else:
        current.note = "reversal_queued" if current.pending_reversal else "reconciled_from_stripe"
    if admin_id is not None:
        db.add(AdminAuditLog(admin_id=admin_id, action="connect.transfer.reconcile",
                             target_type="connect_ledger", target_id=entry_id,
                             detail=f"{status}->transferred; stripe_object={provider_id}"))
    db.commit()
    return {"entry_id": entry_id, "status": current.status, "stripe_transfer_id": provider_id,
            "pending_reversal": bool(current.pending_reversal), "duplicate": False}


def reconcile_reversal(db: Session, entry_id: int, provider_id: str, admin_id: int | None = None) -> dict:
    """Resolve an uncertain full reversal using both Stripe objects."""
    if not connect_configured():
        raise HTTPException(503, "Stripe Connect 未配置")
    if not re.fullmatch(r"trr_[A-Za-z0-9_]+", provider_id or ""):
        raise HTTPException(422, "Stripe 沖回編號無效")
    existing = db.get(ConnectLedgerEntry, entry_id)
    if existing is None or existing.kind != "transfer":
        raise HTTPException(404, "找不到此轉帳流水")
    shop_id = existing.shop_id
    lock_shop(db, shop_id)
    entry = (db.query(ConnectLedgerEntry).filter_by(id=entry_id)
             .with_for_update().populate_existing().one())
    if entry.stripe_reversal_id:
        if entry.stripe_reversal_id == provider_id and entry.status == "reversed":
            return {"entry_id": entry.id, "status": "reversed", "stripe_reversal_id": provider_id, "duplicate": True}
        _reconcile_error(db, entry_id, admin_id, "reversal_id_conflict", "沖回編號與已有記錄不一致")
    if entry.status != "transferred" or not entry.pending_reversal or entry.note not in ("reversal_result_unknown", "reversal_dispatch_started"):
        _reconcile_error(db, entry_id, admin_id, "reversal_state_not_unknown", "此沖回狀態無需核對")
    if entry.note == "reversal_dispatch_started" and (not entry.updated_at or
            datetime.now(timezone.utc).replace(tzinfo=None) - entry.updated_at < timedelta(minutes=2)):
        _reconcile_error(db, entry_id, admin_id, "reversal_in_flight", "沖回可能仍在處理，請稍後核對")
    if not entry.transfer_account_id or not entry.redemption_id or not entry.stripe_transfer_id:
        _reconcile_error(db, entry_id, admin_id, "reversal_legacy_evidence_missing", "舊沖回缺少原帳戶或操作標識，請人工核對")
    transfer_id, account_id, redemption_id, note = entry.stripe_transfer_id, entry.transfer_account_id, entry.redemption_id, entry.note
    cents = connect_gateway._to_cents(entry.net_amount)
    db.commit()
    try:
        transfer = connect_gateway.retrieve_transfer(transfer_id)
        reversal = connect_gateway.retrieve_reversal(transfer_id, provider_id)
    except GatewayError:
        _reconcile_error(db, entry_id, admin_id, "reversal_provider_unavailable", "無法查詢 Stripe 沖回，流水仍待核對", 502)
    if (not _transfer_evidence(transfer, entry_id, redemption_id, account_id, cents, transfer_id)
            or transfer.get("amount_reversed") != cents or transfer.get("reversed") is not True
            or reversal.get("id") != provider_id or reversal.get("object") != "transfer_reversal"
            or reversal.get("transfer") != transfer_id or type(reversal.get("amount")) is not int
            or reversal.get("amount") != cents
            or str(reversal.get("currency", "")).upper() != CURRENCY
            or (reversal.get("metadata") or {}).get("connect_operation_key") != f"reversal-{entry_id}"):
        _reconcile_error(db, entry_id, admin_id, "reversal_evidence_mismatch", "Stripe 沖回資料不足或與流水不一致")
    lock_shop(db, shop_id)
    current = (db.query(ConnectLedgerEntry).filter_by(id=entry_id)
               .with_for_update().populate_existing().one())
    if current.stripe_reversal_id == provider_id and current.status == "reversed":
        return {"entry_id": current.id, "status": "reversed", "stripe_reversal_id": provider_id, "duplicate": True}
    if (current.status != "transferred" or not current.pending_reversal or current.note != note
            or current.stripe_transfer_id != transfer_id or current.transfer_account_id != account_id
            or current.redemption_id != redemption_id or connect_gateway._to_cents(current.net_amount) != cents):
        _reconcile_error(db, entry_id, admin_id, "reversal_changed_during_lookup", "沖回資料已變更，請重新核對")
    from .application_connect import _lock_provider_id
    _lock_provider_id(db, provider_id)
    if db.query(ConnectLedgerEntry.id).filter(ConnectLedgerEntry.stripe_reversal_id == provider_id,
                                               ConnectLedgerEntry.id != entry_id).first():
        _reconcile_error(db, entry_id, admin_id, "reversal_object_already_claimed", "此 Stripe 沖回已關聯其他流水")
    current.status, current.stripe_reversal_id = "reversed", provider_id
    current.pending_reversal, current.note = False, "reconciled_from_stripe"
    if admin_id is not None:
        db.add(AdminAuditLog(admin_id=admin_id, action="connect.reversal.reconcile",
                             target_type="connect_ledger", target_id=entry_id,
                             detail=f"transferred->reversed; stripe_object={provider_id}"))
    db.commit()
    return {"entry_id": entry_id, "status": "reversed", "stripe_reversal_id": provider_id, "duplicate": False}


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
                "payout_operation_id": entry.payout_operation_id if entry.kind == "payout" else None,
                "stripe_payout_id": entry.stripe_payout_id if entry.kind == "payout" else None,
                "retry_count": entry.retry_count or 0,
                "note": entry.note or ""}

    needs_manual = (db.query(ConnectLedgerEntry)
                    .filter(ConnectLedgerEntry.kind == "transfer",
                            ConnectLedgerEntry.status.in_(("needs_manual", "transfer_unknown", "transferring")))
                    .order_by(ConnectLedgerEntry.id).all())
    pending_reversals = (db.query(ConnectLedgerEntry)
                         .filter_by(pending_reversal=True)
                         .order_by(ConnectLedgerEntry.id).all())
    pending_payouts = (db.query(ConnectLedgerEntry).filter(
        ConnectLedgerEntry.kind == "payout",
        ConnectLedgerEntry.status.in_(("processing", "payout_unknown", "submitted")))
        .order_by(ConnectLedgerEntry.id).all())
    return {"negative_balances": negative_balances(db),
            "needs_manual_transfers": [_min(e) for e in needs_manual],
            "pending_reversals": [_min(e) for e in pending_reversals],
            "pending_payouts": [_min(e) for e in pending_payouts]}
