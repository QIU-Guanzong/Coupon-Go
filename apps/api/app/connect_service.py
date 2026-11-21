"""P1-B service: post-redemption transfers, reversals, balances, payouts.

Rules (FEATURE_GAP_20261120 §3 decision 3; docs/P1B_API.md):
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
- The scan hook (record_redemption) must never fail a redemption:
  gateway errors leave a pending entry for the retry endpoint.
- Reversal is idempotent: an already-reversed entry is a no-op.
"""
import logging
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import config, connect_gateway, models
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
                   if e.kind == "transfer" and e.status == "pending"),
                  Decimal("0"))
    transferred = sum((Decimal(str(e.net_amount)) for e in entries
                       if e.kind == "transfer" and e.status == "transferred"),
                      Decimal("0"))
    paid_out = sum((-Decimal(str(e.net_amount)) for e in entries
                    if e.kind == "payout" and e.status == "paid_out"),
                   Decimal("0"))
    return {"pending": pending, "available": transferred - paid_out,
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
    """Post-redemption hook (called from scan_coupon, same transaction).

    Writes the pending ledger entry and, when Connect is configured
    and the merchant account is ready, creates the Stripe transfer
    immediately. Never raises: a gateway failure leaves the entry
    pending for the retry endpoint, and the redemption itself must
    never fail because money movement failed.
    """
    coupon = db.query(models.Coupon).filter(
        models.Coupon.id == red.coupon_id).first()
    basis = settlement_basis(db, coupon, redemption=red)
    if basis is None or basis <= 0:
        return {"transfer": "skipped_zero", "net_amount": 0.0,
                "gross_amount": 0.0, "commission": 0.0}
    rate = effective_commission_rate(db, red.shop_id)
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
    try:
        transfer = connect_gateway.create_transfer(
            account.stripe_account_id, float(net),
            transfer_group=f"coupon-go-redemption-{red.id}")
    except Exception as exc:  # noqa: BLE001 — redemption must not fail here
        entry.note = f"failed: {exc}"[:255]
        logger.warning("Connect transfer failed for redemption %s: %s",
                       red.id, exc)
        result["reason"] = "failed"
        return result
    entry.status = "transferred"
    entry.stripe_transfer_id = transfer.get("transfer_id", "")
    entry.note = ""
    result["transfer"] = "transferred"
    result["stripe_transfer_id"] = entry.stripe_transfer_id
    return result


def _attempt_reversal(db: Session, entry: ConnectLedgerEntry) -> dict:
    """Transfer Reversal for an already-transferred entry. Returns
    {"reversed": bool, "reason": str}. Never raises: on failure the
    entry keeps its transferred status and is flagged pending_reversal
    for the retry endpoint."""
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
            entry.stripe_transfer_id, float(entry.net_amount))
    except Exception as exc:  # noqa: BLE001 — caller decides, never crash
        entry.pending_reversal = True
        entry.note = f"reversal_pending: failed: {exc}"[:255]
        logger.warning("Connect reversal failed for entry %s: %s",
                       entry.id, exc)
        return {"reversed": False, "reason": "failed"}
    entry.status = "reversed"
    entry.stripe_reversal_id = reversal.get("reversal_id", "")
    entry.pending_reversal = False
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


def retry_entry(db: Session, entry: ConnectLedgerEntry) -> dict:
    """Retry a pending transfer, or a pending reversal. Raises 409
    when there is nothing to retry, 503 when Connect is not
    configured, 502 when the gateway fails (state unchanged)."""
    if entry.kind != "transfer":
        raise HTTPException(status_code=409, detail="提款流水无需重试")
    if entry.status == "reversed":
        raise HTTPException(status_code=409, detail="此笔已冲回，无需重试")
    if entry.pending_reversal:
        out = _attempt_reversal(db, entry)
        if not out["reversed"]:
            raise HTTPException(status_code=502,
                                detail=f"冲回仍未成功（{out['reason']}），请稍后重试")
        return {"entry_id": entry.id, "status": "reversed",
                "stripe_transfer_id": entry.stripe_transfer_id or None,
                "stripe_reversal_id": entry.stripe_reversal_id or None,
                "reversed": True}
    if entry.status == "transferred":
        raise HTTPException(status_code=409, detail="此笔已转帐，无需重试")
    if not connect_configured():
        raise HTTPException(
            status_code=503,
            detail="Stripe Connect 未配置：请先在部署环境开启 "
                   "STRIPE_CONNECT_ENABLED")
    account = get_account(db, entry.shop_id)
    if account is None:
        entry.note = "no_account"
        return {"entry_id": entry.id, "status": "pending",
                "stripe_transfer_id": None, "reversed": False,
                "reason": "no_account"}
    if not account_ready(account):
        entry.note = "account_not_ready"
        return {"entry_id": entry.id, "status": "pending",
                "stripe_transfer_id": None, "reversed": False,
                "reason": "account_not_ready"}
    try:
        transfer = connect_gateway.create_transfer(
            account.stripe_account_id, float(entry.net_amount),
            transfer_group=f"coupon-go-redemption-{entry.redemption_id}")
    except GatewayError as exc:
        entry.note = f"failed: {exc}"[:255]
        raise HTTPException(status_code=502, detail=f"Stripe 转帐失败：{exc}")
    entry.status = "transferred"
    entry.stripe_transfer_id = transfer.get("transfer_id", "")
    entry.note = ""
    return {"entry_id": entry.id, "status": "transferred",
            "stripe_transfer_id": entry.stripe_transfer_id,
            "reversed": False}


def request_payout(db: Session, shop: models.Shop, amount) -> dict:
    """Merchant-initiated withdrawal. Raises 503 (not configured),
    409 (account not ready), 422 (bad amount / exceeds available),
    502 (gateway failure — no ledger row is written then)."""
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
    account = get_account(db, shop.id)
    if account is None or not account.payouts_enabled:
        raise HTTPException(status_code=409, detail="商户 Stripe 帐户未就绪，暂不能提款")
    balance = merchant_balance(db, shop.id)
    if amount_dec > balance["available"]:
        raise HTTPException(status_code=422, detail="提款金额超过可提取余额")
    try:
        payout = connect_gateway.create_payout(
            account.stripe_account_id, float(amount_dec))
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=f"Stripe 提款失败：{exc}")
    entry = ConnectLedgerEntry(
        shop_id=shop.id, redemption_id=None, kind="payout",
        status="paid_out", gross_amount=Decimal("0"),
        commission=Decimal("0"), net_amount=-amount_dec,
        stripe_payout_id=payout.get("payout_id", ""))
    db.add(entry)
    db.flush()
    balance = merchant_balance(db, shop.id)
    return {"shop_id": shop.id, "amount": _money(amount_dec),
            "currency": CURRENCY,
            "stripe_payout_id": entry.stripe_payout_id,
            "status": "paid_out",
            "balance": {"pending": _money(balance["pending"]),
                        "available": _money(balance["available"]),
                        "paid_out": _money(balance["paid_out"])}}
