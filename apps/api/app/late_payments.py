"""Durable per-attempt compensation for payment success after cancellation."""
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from . import config
from .models_packages import LatePaymentRefund, Payment
from .payments_gateway import GatewayError, refund_payment, retrieve_refund
from .timeutil import utcnow

logger = logging.getLogger(__name__)


def _event_matches(attempt, obj):
    """Amounts in a Stripe event are integer cents; absent fields are dev-only."""
    received = obj.get("amount_received", obj.get("amount"))
    currency = obj.get("currency")
    if received is None or currency is None:
        return config.APP_ENV == "dev" and not config.STRIPE_WEBHOOK_SECRET and not config.STRIPE_SECRET_KEY
    try:
        return (not isinstance(received, bool) and
                Decimal(str(received)) == Decimal(str(attempt.amount)) * 100 and
                str(currency).upper() == (attempt.currency or "").upper() == "HKD")
    except (InvalidOperation, TypeError, ValueError):
        return False


def record_late_success(db, order, attempt, obj):
    """Caller holds account then order lock. Commit intent before any gateway call."""
    entry = db.query(LatePaymentRefund).filter_by(payment_id=attempt.id).first()
    if entry is not None:
        return entry
    valid = _event_matches(attempt, obj) and bool(attempt.gateway_ref)
    entry = LatePaymentRefund(payment_id=attempt.id, order_id=order.id,
        user_id=order.user_id, amount=attempt.amount, currency=attempt.currency or "",
        request_key="late-payment-" + uuid4().hex,
        status="pending" if valid else "manual_review",
        last_error="" if valid else "payment_event_mismatch")
    attempt.status = "late_succeeded"
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def dispatch(db, entry_id):
    """Serialize retries; a persisted request key survives timeouts/commit failures."""
    if db.get_bind().dialect.name == "sqlite":
        db.query(LatePaymentRefund).filter_by(id=entry_id).update(
            {"status": LatePaymentRefund.status}, synchronize_session=False)
    entry = (db.query(LatePaymentRefund).filter_by(id=entry_id)
             .with_for_update().populate_existing().first())
    if entry is None:
        return None
    if entry.status != "pending":
        return entry
    attempt = db.get(Payment, entry.payment_id)
    if (attempt is None or attempt.order_id != entry.order_id or
            attempt.amount != entry.amount or (attempt.currency or "").upper() != entry.currency.upper() or
            entry.currency.upper() != "HKD" or not attempt.gateway_ref):
        entry.status, entry.last_error = "manual_review", "payment_record_mismatch"
        db.commit()
        return entry
    if not entry.gateway_ref and utcnow() - entry.created_at >= timedelta(hours=23):
        entry.status, entry.last_error = "manual_review", "request_window_expired"
        db.commit()
        return entry
    try:
        result = (retrieve_refund(entry.gateway_ref) if entry.gateway_ref else
                  refund_payment(attempt.gateway_ref, float(entry.amount),
                                 request_key=entry.request_key))
    except GatewayError:
        entry.last_error = "gateway_unavailable"
        db.commit()
        return entry
    try:
        matches = (bool(result.get("gateway_ref")) and
                   result.get("payment_intent") == attempt.gateway_ref and
                   str(result.get("currency", "")).upper() == entry.currency.upper() and
                   Decimal(str(result.get("amount"))) == Decimal(str(entry.amount)))
    except (InvalidOperation, ValueError, TypeError):
        matches = False
    if not matches:
        entry.status, entry.last_error = "manual_review", "refund_result_mismatch"
    else:
        entry.gateway_ref = result["gateway_ref"]
        status = result.get("status")
        if status == "succeeded":
            entry.status, entry.last_error = "refunded", ""
            attempt.status = "late_refunded"
        elif status in ("pending", "requires_action"):
            entry.last_error = "gateway_pending"
        else:
            entry.status, entry.last_error = "manual_review", "gateway_refund_not_completed"
    db.commit()
    return entry


def sweep(db, limit=100):
    ids = [row[0] for row in db.query(LatePaymentRefund.id)
           .filter_by(status="pending").order_by(LatePaymentRefund.id).limit(limit).all()]
    counts = {"refunded": 0, "pending": 0, "manual_review": 0, "failed": 0}
    for entry_id in ids:
        try:
            entry = dispatch(db, entry_id)
            if entry is not None:
                counts[entry.status] += 1
        except Exception:
            db.rollback()
            counts["failed"] += 1
            logger.exception("Late payment refund retry failed id=%s", entry_id)
    return counts


def confirm_existing(db, entry_id, gateway_ref):
    """Record an operator-found refund only after verifying it with the gateway."""
    if db.get_bind().dialect.name == "sqlite":
        db.query(LatePaymentRefund).filter_by(id=entry_id).update(
            {"status": LatePaymentRefund.status}, synchronize_session=False)
    entry = (db.query(LatePaymentRefund).filter_by(id=entry_id)
             .with_for_update().populate_existing().first())
    if entry is None or entry.status == "refunded":
        return entry
    attempt = db.get(Payment, entry.payment_id)
    if (attempt is None or attempt.order_id != entry.order_id or
            attempt.amount != entry.amount or (attempt.currency or "").upper() != entry.currency.upper()):
        raise ValueError("payment_record_mismatch")
    result = retrieve_refund(gateway_ref)
    try:
        valid = (result.get("gateway_ref") == gateway_ref and
                 result.get("payment_intent") == attempt.gateway_ref and
                 str(result.get("currency", "")).upper() == entry.currency.upper() and
                 Decimal(str(result.get("amount"))) == Decimal(str(entry.amount)) and
                 result.get("status") == "succeeded")
    except (InvalidOperation, ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError("refund_result_mismatch")
    entry.gateway_ref, entry.status, entry.last_error = gateway_ref, "refunded", ""
    attempt.status = "late_refunded"
    db.commit()
    return entry
