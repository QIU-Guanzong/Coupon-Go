"""P1-B service: post-redemption transfers, reversals, balances, payouts.

Rules (FEATURE_GAP_20261120 §3 decision 3; docs/P1B_API.md):
- A paid order and matching successful payment are prerequisites for a
  transfer intent; free claims and simulated bargains do not qualify.
  Stripe network transfer remains disabled until authoritative payment
  evidence and settlement basis are integrated and verified.
- Commission reuses MerchantCommissionRate (F5), else the platform
  default 10%. The rate is read at redemption time and frozen on the
  ledger entry. The existing monthly statement uses a different snapshot;
  rate changes between redemption and settlement remain a merge blocker.
- Redemption and reversal transactions only record intents; a worker
  dispatches after commit. Money never moves without an explicitly enabled
  test gateway. Unknown outcomes stay reserved for reconciliation and are
  never automatically retried beyond Stripe's idempotency window.
- Monthly Settlement remains a separate reconciliation record; no Connect
  operation marks it paid.
"""
import logging
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config, connect_gateway, models
from .models_connect import ConnectAccount, ConnectLedgerEntry, ConnectReconciliationEvent
from .models_line_f import MerchantCommissionRate
from .payments_gateway import GatewayError

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")
# F5 platform default, mirrors routers.admin_v2.DEFAULT_COMMISSION_RATE.
DEFAULT_COMMISSION_RATE = Decimal("0.10")
CURRENCY = "HKD"


def connect_configured() -> bool:
    if not config.connect_enabled().get("enabled") or not connect_gateway.valid_v2_api_version(config.STRIPE_CONNECT_API_VERSION):
        return False
    return bool(config.STRIPE_SECRET_KEY.startswith("sk_test_") or
                (config.APP_ENV == "dev" and config.STRIPE_CONNECT_SIMULATOR))


def effective_commission_rate(db: Session, shop_id: int) -> Decimal:
    configured = (db.query(MerchantCommissionRate)
                  .filter(MerchantCommissionRate.shop_id == shop_id).first())
    if configured is not None:
        return Decimal(str(configured.rate))
    return DEFAULT_COMMISSION_RATE


def get_account(db: Session, shop_id: int):
    return db.query(ConnectAccount).filter_by(shop_id=shop_id).first()


def account_ready(account) -> bool:
    return bool(account is not None and account.stripe_account_id and account.charges_enabled
                and account.payouts_enabled)


def merchant_balance(db: Session, shop_id: int) -> dict:
    entries = db.query(ConnectLedgerEntry).filter_by(shop_id=shop_id).all()
    simulator = config.APP_ENV == "dev" and config.STRIPE_CONNECT_SIMULATOR and not config.STRIPE_SECRET_KEY
    pending = sum((Decimal(str(e.net_amount)) for e in entries
                   if e.kind == "transfer" and e.status in ("pending", "processing", "unknown", "payment_verification_required")),
                  Decimal("0"))
    transferred = sum((Decimal(str(e.net_amount)) for e in entries
                       if e.kind == "transfer" and e.status in ("transferred", "reversal_processing", "reversal_unknown")
                       and not e.pending_reversal and (e.stripe_charge_id or simulator)),
                      Decimal("0"))
    paid_out = sum((-Decimal(str(e.net_amount)) for e in entries
                    if e.kind == "payout" and e.status == "paid_out"),
                   Decimal("0"))
    reserved = sum((-Decimal(str(e.net_amount)) for e in entries
                    if e.kind == "payout" and e.status in ("processing", "submitted", "payout_unknown")),
                   Decimal("0"))
    return {"pending": pending, "available": transferred - paid_out - reserved,
            "paid_out": paid_out}


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


def record_redemption(db: Session, red: models.Redemption) -> dict:
    """Record a transfer intent in the redemption transaction; never call Stripe here."""
    item = db.get(models.WalletItem, red.wallet_item_id)
    order = db.get(models.Order, item.order_id) if item and item.order_id else None
    # A displayed coupon price, simulated bargain, or free claim is not a
    # platform receipt. A successful, matching payment attempt is necessary.
    if (item is None or item.source != "purchase" or order is None
            or order.user_id != item.user_id or order.payment_status != "paid"
            or order.refund_status not in ("none", "rejected")):
        return {"transfer": "skipped_unpaid", "net_amount": 0.0,
                "gross_amount": 0.0, "commission": 0.0}
    payment = (db.query(models.Payment).filter_by(order_id=order.id, status="succeeded", gateway="stripe")
               .order_by(models.Payment.id.desc()).first())
    if (payment is None or not payment.gateway_ref
            or (payment.currency or "").upper() != "HKD"
            or Decimal(str(payment.amount)) != Decimal(str(order.paid_amount or 0))):
        return {"transfer": "skipped_unverified", "net_amount": 0.0,
                "gross_amount": 0.0, "commission": 0.0}
    simulator = config.APP_ENV == "dev" and config.STRIPE_CONNECT_SIMULATOR and not config.STRIPE_SECRET_KEY
    if payment.gateway_ref.startswith("pi_test_") and not simulator:
        return {"transfer": "skipped_simulated", "net_amount": 0.0,
                "gross_amount": 0.0, "commission": 0.0}
    basis = Decimal(str(order.paid_amount or 0))
    if basis is None or basis <= 0:
        return {"transfer": "skipped_zero", "net_amount": 0.0,
                "gross_amount": 0.0, "commission": 0.0}
    rate = effective_commission_rate(db, red.shop_id)
    commission = (basis * rate).quantize(CENT)
    net = basis - commission
    if net <= 0:
        return {"transfer": "skipped_zero", "net_amount": 0.0,
                "gross_amount": _money(basis), "commission": _money(commission)}
    entry = ConnectLedgerEntry(shop_id=red.shop_id, redemption_id=red.id,
        order_id=order.id, payment_intent_id=payment.gateway_ref,
        kind="transfer", status="pending", gross_amount=basis,
        commission=commission, net_amount=net,
        note="not_configured" if not connect_configured() else "awaiting_dispatch")
    try:
        # The database unique order constraint handles parallel duplicate
        # redemptions without rolling back the surrounding redemption.
        with db.begin_nested():
            db.add(entry)
            db.flush()
    except IntegrityError:
        return {"transfer": "skipped_duplicate_order", "net_amount": 0.0,
                "gross_amount": 0.0, "commission": 0.0}
    return {"transfer": "pending" if connect_configured() else "not_configured",
            "entry_id": entry.id, "gross_amount": _money(basis),
            "commission": _money(commission), "net_amount": _money(net),
            "stripe_transfer_id": None}


def reverse_redemption(db: Session, red: models.Redemption, reason: str) -> dict:
    """Record cancellation/reversal intent in the caller's transaction only."""
    _lock_shop(db, red.shop_id)
    entry = (db.query(ConnectLedgerEntry).filter_by(redemption_id=red.id, kind="transfer")
             .with_for_update().first())
    if entry is None:
        return {"reversed": False, "reason": "no_entry"}
    if entry.status == "reversed":
        return {"reversed": True, "reason": "already_reversed",
                "stripe_reversal_id": entry.stripe_reversal_id or None}
    never_dispatched = (entry.status == "payment_verification_required"
                        and not entry.first_attempt_at and not entry.last_attempt_at
                        and not entry.attempt_count and not entry.stripe_transfer_id)
    if entry.status == "pending" or never_dispatched:
        entry.status = "reversed"  # no Stripe call was ever started
        entry.note = reason
        return {"reversed": True, "reason": reason, "stripe_reversal_id": None}
    # A transfer may already exist, including an unknown/in-flight result.
    # Never invent a reversal or issue a network request before this commits.
    entry.pending_reversal = True
    entry.note = "reversal_requested: " + reason
    return {"reversed": False, "reason": "pending_reversal"}


def _lock_shop(db: Session, shop_id: int):
    # All balance-affecting operations lock the merchant first, then entry.
    return (db.query(models.Shop).filter_by(id=shop_id)
            .with_for_update().populate_existing().one())


def _lock_entry(db: Session, entry_id: int):
    return (db.query(ConnectLedgerEntry).filter_by(id=entry_id)
            .with_for_update().populate_existing().first())


def retry_entry(db: Session, entry: ConnectLedgerEntry) -> dict:
    """Dispatch one committed transfer/reversal. Unknown outcomes require reconciliation.

    The processing marker is committed BEFORE the provider request. A process
    crash or timeout leaves funds reserved and cannot silently start a second
    request after Stripe's idempotency retention window.
    """
    from datetime import datetime, timezone
    _lock_shop(db, entry.shop_id)
    entry = _lock_entry(db, entry.id)
    if entry is None or entry.kind != "transfer":
        raise HTTPException(409, "此流水不能用于转帐重试")
    if entry.status in ("processing", "reversal_processing", "unknown", "reversal_unknown"):
        raise HTTPException(409, "上次请求结果待对账，请勿重复转帐")
    if entry.status == "reversed":
        raise HTTPException(409, "此笔已冲回，无需重试")
    reversal = entry.pending_reversal and entry.status == "transferred"
    if entry.status == "transferred" and not reversal:
        raise HTTPException(409, "此笔已转帐，无需重试")
    if entry.status != "pending" and not reversal:
        raise HTTPException(409, "流水状态不允许重试")
    if not connect_configured():
        raise HTTPException(503, "Stripe Connect 未配置")
    if not reversal and config.STRIPE_SECRET_KEY:
        # Existing order/payment schema does not persist verifiable provider
        # receipt provenance. Never dispatch a network transfer until the
        # paid-order integration can supply a verified captured charge.
        entry.status = "payment_verification_required"
        entry.note = "verified_payment_provenance_required"
        db.commit()
        return {"entry_id": entry.id, "status": entry.status,
                "reason": "payment_provenance_required", "reversed": False}
    account = get_account(db, entry.shop_id)
    if not reversal and (account is None or not account_ready(account)):
        entry.note = "no_account" if account is None else "account_not_ready"
        db.commit()
        return {"entry_id": entry.id, "status": "pending", "reason": entry.note,
                "stripe_transfer_id": None, "reversed": False}
    entry_id, operation_id, redemption_id, shop_id = entry.id, entry.operation_id, entry.redemption_id, entry.shop_id
    account_id = entry.destination_account_id or (account.stripe_account_id if account else "")
    if not account_id:
        raise HTTPException(409, "缺少原商户收款账户，请人工对账")
    if not reversal and entry.destination_account_id and entry.destination_account_id != account.stripe_account_id:
        raise HTTPException(409, "商户收款账户已变化，请人工对账")
    entry.destination_account_id = account_id
    amount, transfer_id = Decimal(str(entry.net_amount)), entry.stripe_transfer_id
    if reversal and not transfer_id:
        raise HTTPException(409, "缺少原转帐编号，请人工对账")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    entry.first_attempt_at = entry.first_attempt_at or now
    entry.last_attempt_at = now
    entry.attempt_count = (entry.attempt_count or 0) + 1
    entry.status = "reversal_processing" if reversal else "processing"
    entry.note = "stripe_request_started"
    db.commit()
    # No database transaction or row lock is held across Stripe I/O.
    try:
        if reversal:
            result = connect_gateway.reverse_transfer(transfer_id, amount,
                       idempotency_key=f"reversal-{operation_id}")
            object_id = result.get("reversal_id")
        else:
            result = connect_gateway.create_transfer(account_id, amount,
                       transfer_group=f"coupon-go-redemption-{redemption_id}",
                       idempotency_key=f"transfer-{operation_id}")
            object_id = result.get("transfer_id")
        if not object_id:
            raise ValueError("provider response omitted object id")
    except Exception:  # provider may have executed before timeout/error
        db.rollback()
        _lock_shop(db, shop_id)
        current = _lock_entry(db, entry_id)
        expected_processing = "reversal_processing" if reversal else "processing"
        if current.status != expected_processing:
            return {"entry_id": entry_id, "status": current.status,
                    "reason": "reconciled_concurrently", "reversed": current.status == "reversed"}
        current.status = "reversal_unknown" if reversal else "unknown"
        current.note = "stripe_result_unknown_reconcile_required"
        db.commit()
        return {"entry_id": entry_id, "status": current.status,
                "reason": "reconcile_required", "reversed": False}
    _lock_shop(db, shop_id)
    current = _lock_entry(db, entry_id)
    expected_processing = "reversal_processing" if reversal else "processing"
    if current.status != expected_processing:
        # An operator reconciled a stale operation while the worker was
        # returning. Never overwrite an independently verified result.
        return {"entry_id": entry_id, "status": current.status,
                "reason": "reconciled_concurrently", "reversed": current.status == "reversed"}
    if reversal:
        current.stripe_reversal_id = object_id
        current.status = "reversed"
        current.pending_reversal = False
    else:
        current.stripe_transfer_id = object_id
        current.status = "transferred"
        # A concurrent refund may have set pending_reversal while Stripe ran.
    current.note = "reversal_pending" if current.pending_reversal else ""
    db.commit()
    return {"entry_id": entry_id, "status": current.status,
            "stripe_transfer_id": current.stripe_transfer_id or None,
            "stripe_reversal_id": current.stripe_reversal_id or None,
            "reversed": current.status == "reversed"}


def request_payout(db: Session, shop: models.Shop, amount, request_id: str) -> dict:
    """Reserve funds under a merchant lock, then dispatch after committing.

    A caller MUST reuse request_id on retry. Unknown outcomes remain reserved;
    no new operation is initiated by repeating that request_id.
    """
    import re
    from datetime import datetime, timezone
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
    # Lock a row that always exists even before any Connect ledger entries.
    shop = _lock_shop(db, shop.id)
    previous = (db.query(ConnectLedgerEntry).filter_by(shop_id=shop.id,
                kind="payout", client_request_id=request_id).first())
    if previous is not None:
        if -Decimal(str(previous.net_amount)) != amount_dec:
            raise HTTPException(409, "提款请求编号已用于其他金额")
        balance = merchant_balance(db, shop.id)
        return {"shop_id": shop.id, "entry_id": previous.id, "amount": _money(amount_dec),
                "currency": CURRENCY, "stripe_payout_id": previous.stripe_payout_id or None,
                "status": previous.status, "balance": {k: _money(v) for k, v in balance.items()}}
    account = get_account(db, shop.id)
    if account is None or not account.stripe_account_id or not account.payouts_enabled or not account.manual_payouts:
        raise HTTPException(409, "商户 Stripe 帐户未就绪，暂不能提款")
    balance = merchant_balance(db, shop.id)
    if amount_dec > balance["available"]:
        raise HTTPException(422, "提款金额超过可提取余额")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    entry = ConnectLedgerEntry(shop_id=shop.id, redemption_id=None, kind="payout",
        status="processing", gross_amount=Decimal("0"), commission=Decimal("0"),
        net_amount=-amount_dec, client_request_id=request_id,
        destination_account_id=account.stripe_account_id,
        first_attempt_at=now, last_attempt_at=now, attempt_count=1,
        note="stripe_request_started")
    db.add(entry)
    db.flush()
    entry_id, operation_id, shop_id, account_id = entry.id, entry.operation_id, shop.id, account.stripe_account_id
    db.commit()  # reserve before external call; no transaction held during I/O
    try:
        payout = connect_gateway.create_payout(account_id, amount_dec,
                  idempotency_key=f"payout-{operation_id}")
        payout_id = payout.get("payout_id")
        if not payout_id:
            raise ValueError("provider response omitted payout id")
    except Exception:
        db.rollback()
        _lock_shop(db, shop_id)
        current = _lock_entry(db, entry_id)
        if current.status != "processing":
            return {"shop_id": shop_id, "entry_id": entry_id, "amount": _money(amount_dec),
                    "currency": CURRENCY, "stripe_payout_id": current.stripe_payout_id or None,
                    "status": current.status,
                    "balance": {k: _money(v) for k, v in merchant_balance(db, shop_id).items()}}
        current.status, current.note = "payout_unknown", "stripe_result_unknown_reconcile_required"
        db.commit()
        raise HTTPException(502, "提款结果待确认，请勿重复提交") from None
    _lock_shop(db, shop_id)
    current = _lock_entry(db, entry_id)
    if current.status != "processing":
        return {"shop_id": shop_id, "entry_id": entry_id, "amount": _money(amount_dec),
                "currency": CURRENCY, "stripe_payout_id": current.stripe_payout_id or None,
                "status": current.status,
                "balance": {k: _money(v) for k, v in merchant_balance(db, shop_id).items()}}
    current.stripe_payout_id = payout_id
    current.status = "paid_out" if payout.get("status") in ("paid", "succeeded") else "submitted"
    current.note = "" if current.status == "paid_out" else "awaiting_stripe_payout_status"
    db.commit()
    balance = merchant_balance(db, shop_id)
    return {"shop_id": shop_id, "entry_id": entry_id, "amount": _money(amount_dec),
            "currency": CURRENCY, "stripe_payout_id": payout_id, "status": current.status,
            "balance": {k: _money(v) for k, v in balance.items()}}


def reconcile_entry(db: Session, entry_id: int, provider_id: str, admin_id: int | None) -> dict:
    """Resolve a recorded operation only after verifying the Stripe object.

    An operator can supply an object found in Stripe logs when a request timed
    out before returning its ID. A missing object is *not* proof of failure and
    never releases money or permits another request.
    """
    import re
    from datetime import datetime, timedelta, timezone
    entry = db.get(ConnectLedgerEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "找不到此流水")
    _lock_shop(db, entry.shop_id)
    entry = _lock_entry(db, entry_id)
    shop_id, kind, state = entry.shop_id, entry.kind, entry.status
    if kind == "payout" and state in ("processing", "payout_unknown", "submitted", "paid_out"):
        operation = "payout"
        pattern = r"po_[A-Za-z0-9_]+"
    elif kind == "transfer" and state in ("processing", "unknown"):
        operation = "transfer"
        pattern = r"tr_[A-Za-z0-9_]+"
    elif kind == "transfer" and state in ("reversal_processing", "reversal_unknown"):
        operation = "reversal"
        pattern = r"trr_[A-Za-z0-9_]+"
    else:
        raise HTTPException(409, "流水状态不需要此对账")
    if state in ("processing", "reversal_processing"):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if not entry.last_attempt_at or now - entry.last_attempt_at < timedelta(minutes=2):
            raise HTTPException(409, "请求可能仍在处理，请稍后再对账")
    if not re.fullmatch(pattern, provider_id or ""):
        raise HTTPException(422, "Stripe 对象编号无效")
    if operation == "payout" and entry.stripe_payout_id and entry.stripe_payout_id != provider_id:
        raise HTTPException(409, "Stripe 打款编号与流水不一致")
    account_id, transfer_id, operation_id = entry.destination_account_id, entry.stripe_transfer_id, entry.operation_id
    expected_cents = connect_gateway._amount_cents(abs(Decimal(str(entry.net_amount))))
    if not account_id or (operation == "reversal" and not transfer_id):
        raise HTTPException(409, "流水缺少 Stripe 账户或原转帐编号")
    db.commit()  # read-only Stripe I/O outside all database locks
    try:
        if operation == "payout":
            obj = connect_gateway.retrieve_payout(account_id, provider_id)
        elif operation == "transfer":
            obj = connect_gateway.retrieve_transfer(provider_id)
        else:
            obj = connect_gateway.retrieve_reversal(transfer_id, provider_id)
    except Exception:
        raise HTTPException(502, "无法从 Stripe 确认该对象；余额仍被保留") from None
    expected_key = f"{operation}-{operation_id}"
    if (obj.get("id") != provider_id or obj.get("amount") != expected_cents
            or (obj.get("metadata") or {}).get("connect_operation_key") != expected_key):
        raise HTTPException(409, "Stripe 对象与流水不匹配")
    if (obj.get("currency") or "").lower() != CURRENCY.lower():
        raise HTTPException(409, "Stripe 对象币种不匹配")
    if operation == "transfer" and obj.get("destination") != account_id:
        raise HTTPException(409, "Stripe 转帐账户不匹配")
    if operation == "reversal" and obj.get("transfer") != transfer_id:
        raise HTTPException(409, "Stripe 冲回的原转帐不匹配")
    _lock_shop(db, shop_id)
    current = _lock_entry(db, entry_id)
    if current.status != state or current.destination_account_id != account_id:
        raise HTTPException(409, "流水已发生变化，请重新对账")
    if operation == "payout":
        provider_status = obj.get("status")
        if provider_status in ("pending", "in_transit"):
            if state == "paid_out":
                raise HTTPException(409, "Stripe 状态回退，请人工核查")
            current.status = "submitted"
        elif provider_status == "paid":
            current.status = "paid_out"
        elif provider_status in ("failed", "canceled"):
            current.status = "failed"  # Stripe confirmed no successful payout
        else:
            raise HTTPException(409, "Stripe 打款状态未知，余额仍被保留")
        current.stripe_payout_id = provider_id
    elif operation == "transfer":
        current.stripe_transfer_id = provider_id
        current.status = "transferred"
    else:
        current.stripe_reversal_id = provider_id
        current.status = "reversed"
        current.pending_reversal = False
    current.note = "reconciled_from_stripe"
    current.last_polled_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if current.status != state or admin_id is not None:
        db.add(ConnectReconciliationEvent(entry_id=entry_id, actor_id=admin_id,
            source="admin" if admin_id is not None else "scheduler", prior_status=state,
            new_status=current.status, provider_id=provider_id))
    if admin_id is not None:
        db.add(models.AdminAuditLog(admin_id=admin_id, action="connect.reconcile",
            target_type="connect_ledger", target_id=entry_id,
            detail=f"{operation}:{state}->{current.status}; stripe_object={provider_id}"))
    db.commit()
    return {"entry_id": entry_id, "status": current.status,
            "pending_reversal": current.pending_reversal}


def sweep_connect_transfers(db: Session, batch_size: int = 50) -> dict:
    """Dispatch committed intents, never retry uncertain or in-flight results."""
    from sqlalchemy import or_, and_
    if not connect_configured():
        return {"processed": 0, "reason": "not_configured"}
    ids = [r[0] for r in (db.query(ConnectLedgerEntry.id)
        .outerjoin(ConnectAccount, ConnectAccount.shop_id == ConnectLedgerEntry.shop_id)
        .filter(ConnectLedgerEntry.kind == "transfer")
        .filter(or_(and_(ConnectLedgerEntry.status == "pending",
                         ConnectAccount.charges_enabled.is_(True),
                         ConnectAccount.payouts_enabled.is_(True),
                         ConnectAccount.stripe_account_id != "",
                         or_(ConnectLedgerEntry.destination_account_id == "",
                             ConnectLedgerEntry.destination_account_id == ConnectAccount.stripe_account_id)),
                    and_(ConnectLedgerEntry.status == "transferred",
                         ConnectLedgerEntry.pending_reversal.is_(True),
                         ConnectLedgerEntry.stripe_transfer_id != "",
                         ConnectLedgerEntry.destination_account_id != "")))
        .order_by(ConnectLedgerEntry.id).limit(min(max(batch_size, 1), 100)).all())]
    db.rollback()  # release read transaction before the first dispatch
    processed = 0
    for entry_id in ids:
        try:
            entry = db.get(ConnectLedgerEntry, entry_id)
            if entry is not None:
                retry_entry(db, entry)
                processed += 1
        except Exception:
            db.rollback()
            logger.error("Connect dispatch failed for entry %s; manual review required", entry_id)
            # Poisoned pre-dispatch rows must not starve later merchants. A
            # row may only be retried after an operator resolves the issue.
            try:
                row = db.get(ConnectLedgerEntry, entry_id)
                if row is not None:
                    _lock_shop(db, row.shop_id)
                    row = _lock_entry(db, entry_id)
                    if row.status == "pending" or (row.status == "transferred" and row.pending_reversal):
                        row.status = "dispatch_blocked"
                        row.note = "dispatch_error_manual_review"
                        db.commit()
            except Exception:
                db.rollback()
    return {"processed": processed}


def sweep_payout_status(db: Session, batch_size: int = 50) -> dict:
    """Poll known Stripe payouts fairly, including paid payouts that may fail later."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import or_
    if not connect_configured() or not config.STRIPE_SECRET_KEY:
        return {"processed": 0, "reason": "not_configured"}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(minutes=5)
    rows = (db.query(ConnectLedgerEntry.id, ConnectLedgerEntry.stripe_payout_id)
        .filter(ConnectLedgerEntry.kind == "payout",
                ConnectLedgerEntry.status.in_(("submitted", "paid_out")),
                ConnectLedgerEntry.stripe_payout_id != "",
                or_(ConnectLedgerEntry.last_polled_at.is_(None),
                    ConnectLedgerEntry.last_polled_at <= cutoff))
        .order_by(ConnectLedgerEntry.last_polled_at.asc().nullsfirst(), ConnectLedgerEntry.id)
        .limit(min(max(batch_size, 1), 100)).all())
    db.rollback()
    processed = 0
    for entry_id, payout_id in rows:
        try:
            reconcile_entry(db, entry_id, payout_id, None)
            processed += 1
        except Exception:
            # Back off failed reads and let other merchants advance; never
            # treat a failed query as proof that money was not paid.
            db.rollback()
            try:
                entry = db.get(ConnectLedgerEntry, entry_id)
                if entry is not None:
                    _lock_shop(db, entry.shop_id)
                    entry = _lock_entry(db, entry_id)
                    if entry.status in ("submitted", "paid_out"):
                        entry.last_polled_at = datetime.now(timezone.utc).replace(tzinfo=None)
                        db.commit()
            except Exception:
                db.rollback()
    return {"processed": processed}
