"""Line C (套餐 2A): packages / orders / payments / refunds.

Paths follow docs/FEATURE_RESEARCH_20261116.md §12 （套餐）:
  GET  /packages, GET /package-details, POST /packages/{id}/purchase,
  POST /packages/{id}/refund, GET /orders, GET /orders/{id}
plus the payment lifecycle the report's F1-2/F1-3 requires:
  POST /orders/{id}/confirm-payment (FIX-1: gateway-verified result;
  client-supplied outcome accepted only in dev without a key),
  POST /orders/{id}/retry-payment, POST /orders/{id}/cancel,
  POST /orders/{id}/refund, POST /orders/process-expired-refunds,
  POST /payments/webhook (Stripe test-mode events, FIX-1:
  Stripe-Signature verified whenever a key/secret is configured).

Flow: purchase (reserve stock, create order + payment attempt) →
confirm succeeded → voucher (kind='package' coupon) into Q Wallet
(wallet status 1/2/3 reused; source='purchase', order_id linked) →
vendor scan via the existing /new-scan-coupon path → refund voids the
voucher (status 3) so it can never be redeemed afterwards.
Free-coupon claim is untouched; package coupons cannot be claimed
(the guard lives in routers/coupons.py, 3 lines).

Settlement: on payment success the order books gross / commission /
net (commission rate is unset — report §8: fee is negotiated per
merchant, default 0) with settlement_status='pending'. Reconciliation
is line F; this line only records the ledger fields. A refund voids
the settlement (settlement_status='void', net 0).
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import config, models
from ..database import get_db
from ..deps import get_current_user, get_optional_user, require_role
from ..models_packages import Order, Package, Payment, Refund
from ..payments_gateway import (
    GatewayError, create_payment_intent, refund_payment,
    retrieve_payment_intent, verify_webhook_signature,
)
from ..serializers_packages import (
    order_out, package_out, payment_out, refund_out,
)
from .. import schemas_packages as sp

router = APIRouter()

logger = logging.getLogger(__name__)

# P1-5: gateway failures are never passed through to the user. Every
# gateway-error surface returns this one generic message; the gateway
# detail goes to the server log only (logger.exception at each site).
GATEWAY_FAILURE_MESSAGE = "支付失败，请稍后重试"

PAYMENT_METHODS = ("fps", "card")
REFUND_POLICIES = ("anytime", "expiry_only", "non_refundable")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------- helpers ----------
def _get_package(db: Session, package_id: int) -> Package:
    pkg = db.query(Package).filter(Package.id == package_id).first()
    if pkg is None:
        raise HTTPException(status_code=404, detail="找不到此套餐")
    return pkg


def _get_package_locked(db: Session, package_id: int) -> Package:
    # FIX-3: lock the package row before the stock check so a second
    # concurrent purchase/retry blocks here and re-reads stock_left
    # only after this transaction commits.
    pkg = (db.query(Package).filter(Package.id == package_id)
           .with_for_update().populate_existing().first())
    if pkg is None:
        raise HTTPException(status_code=404, detail="找不到此套餐")
    return pkg


def _package_coupon(db: Session, package: Package):
    return (db.query(models.Coupon)
            .filter(models.Coupon.package_id == package.id,
                    models.Coupon.kind == "package")
            .first())


def _sync_coupon_stock(db: Session, package: Package):
    """Keep the package coupon template's remaining in step with stock."""
    coupon = _package_coupon(db, package)
    if coupon is not None:
        coupon.remaining = max(0, package.stock_left)
    if package.stock_left <= 0 and package.status == "active":
        package.status = "sold_out"
    elif package.stock_left > 0 and package.status == "sold_out":
        package.status = "active"


def _release_stock(db: Session, order: Order):
    if order.package_id is None:
        return
    # FIX-3: lock the package row so a release cannot lose an update
    # against a concurrent purchase's decrement.
    pkg = (db.query(Package).filter(Package.id == order.package_id)
           .with_for_update().populate_existing().first())
    if pkg is not None:
        # Gap 7: release exactly what this order reserved (1 by default).
        qty = order.quantity or 1
        pkg.stock_left = min(pkg.stock_total, pkg.stock_left + qty)
        _sync_coupon_stock(db, pkg)


def _latest_payment(db: Session, order_id: int):
    return (db.query(Payment).filter(Payment.order_id == order_id)
            .order_by(Payment.id.desc()).first())


def _get_order_for_user(db: Session, order_id: int, user_id: int) -> Order:
    order = db.query(Order).filter(Order.id == order_id).first()
    if order is None:
        raise HTTPException(status_code=404, detail="找不到此訂單")
    if order.user_id != user_id:
        raise HTTPException(status_code=403, detail="沒有權限")
    return order


def _get_order_for_user_locked(db: Session, order_id: int,
                               user_id: int) -> Order:
    # FIX-3: lock the order row before any check-then-write on it
    # (refund status, retry state) so a concurrent request for the
    # same order blocks here instead of passing the check twice.
    order = (db.query(Order).filter(Order.id == order_id)
             .with_for_update().populate_existing().first())
    if order is None:
        raise HTTPException(status_code=404, detail="找不到此訂單")
    if order.user_id != user_id:
        raise HTTPException(status_code=403, detail="沒有權限")
    return order


def _refresh_payment_timeout(db: Session, order: Order):
    """Lazy timeout: a pending order whose open attempt is older than
    PAYMENT_TIMEOUT_MINUTES becomes timeout and releases its stock."""
    if order.payment_status != "pending":
        return order
    attempt = _latest_payment(db, order.id)
    if attempt is None or attempt.status not in ("requires_payment", "processing"):
        return order
    age = _now() - (attempt.created_at or _now())
    if age > timedelta(minutes=config.PAYMENT_TIMEOUT_MINUTES):
        attempt.status = "timeout"
        order.payment_status = "timeout"
        _release_stock(db, order)
        db.commit()
    return order


def _issue_voucher(db: Session, order: Order, package: Package):
    """Payment succeeded → package voucher into Q Wallet (status 1).
    Idempotent: a second confirm of the same order returns the voucher."""
    existing = (db.query(models.WalletItem)
                .filter(models.WalletItem.order_id == order.id).first())
    if existing is not None:
        return existing
    coupon = _package_coupon(db, package)
    if coupon is None:
        raise HTTPException(status_code=500, detail="套餐券模板缺失，請聯繫客服")
    item = models.WalletItem(
        user_id=order.user_id, coupon_id=coupon.id, status=1,
        source="purchase", order_id=order.id)
    db.add(item)
    coupon.used_count += 1
    db.flush()
    return item


def _apply_payment_result(db: Session, order: Order, attempt: Payment,
                          outcome: str, failure_message: str = ""):
    """Shared by confirm-payment and the test-mode webhook."""
    if order.payment_status != "pending":
        raise HTTPException(status_code=400, detail="訂單已不在待支付狀態")
    if attempt.status not in ("requires_payment", "processing"):
        raise HTTPException(status_code=400, detail="此筆支付已處理")
    package = None
    if order.package_id:
        package = db.query(Package).filter(Package.id == order.package_id).first()
    if outcome == "succeeded":
        attempt.status = "succeeded"
        order.payment_status = "paid"
        order.paid_amount = order.amount
        order.paid_at = _now()
        # Settlement ledger (line F reconciles; default commission 0).
        order.commission_amount = 0
        order.net_amount = order.paid_amount
        order.settlement_status = "pending"
        db.commit()
        if package is not None:
            _issue_voucher(db, order, package)
        db.commit()
    elif outcome == "failed":
        attempt.status = "failed"
        attempt.failure_message = failure_message or "支付失敗"
        order.payment_status = "failed"
        _release_stock(db, order)
        db.commit()
    elif outcome == "timeout":
        attempt.status = "timeout"
        attempt.failure_message = failure_message or "支付逾時"
        order.payment_status = "timeout"
        _release_stock(db, order)
        db.commit()
    else:
        raise HTTPException(status_code=400, detail="outcome 須為 succeeded / failed / timeout")
    return order


def _process_refund(db: Session, order: Order, reason: str) -> Refund:
    """Execute a refund: gateway refund (原路返回） + void the voucher +
    void the settlement. Caller validates policy and voucher state.

    FIX-3: the Refund row is claimed (inserted + flushed) BEFORE the
    gateway call, while the caller holds the order row lock. The
    (order_id, reason) UNIQUE then fires before any money moves if a
    second refund slipped past the check (e.g. where FOR UPDATE is
    ignored); on a gateway failure the whole transaction — claim row
    included — rolls back.
    """
    item = (db.query(models.WalletItem)
            .filter(models.WalletItem.order_id == order.id).first())
    if item is None or item.status != 1:
        raise HTTPException(status_code=400, detail="此訂單的套餐券已使用或已失效，不可退款")
    attempt = (db.query(Payment)
               .filter(Payment.order_id == order.id,
                       Payment.status == "succeeded")
               .order_by(Payment.id.desc()).first())
    refund = Refund(order_id=order.id, user_id=order.user_id,
                    amount=order.paid_amount, reason=reason,
                    status="refunded", gateway_ref="")
    db.add(refund)
    order.refund_status = "refunded"
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="此訂單已有退款記錄")
    gateway_ref = ""
    if attempt is not None and attempt.gateway_ref:
        try:
            result = refund_payment(attempt.gateway_ref, float(order.paid_amount))
            gateway_ref = result.get("gateway_ref", "")
            attempt.status = "refunded"
        except GatewayError as exc:
            # P1-5: log the gateway detail, return only the generic
            # message — the raw gateway error never reaches the user.
            logger.exception("package refund gateway failed order_id=%s: %s",
                             order.id, exc)
            raise HTTPException(status_code=502,
                                detail=GATEWAY_FAILURE_MESSAGE)
    refund.gateway_ref = gateway_ref
    order.settlement_status = "void"
    order.net_amount = 0
    item.status = 3  # 退款后券作废，不可再核销
    _release_stock(db, order)
    db.commit()
    db.refresh(refund)
    return refund


def _void_expired_voucher(db: Session, order: Order):
    """Non-refundable expired voucher: void it without a refund."""
    item = (db.query(models.WalletItem)
            .filter(models.WalletItem.order_id == order.id).first())
    if item is not None and item.status == 1:
        item.status = 3
    order.settlement_status = "void"
    order.net_amount = 0
    _release_stock(db, order)
    db.commit()


def sweep_expired_packages(db: Session) -> dict:
    """M1 shared sweep: expired paid package vouchers → auto-refund
    (or void if non-refundable). Used by BOTH the admin manual
    endpoint and the background scheduler — one logic, two triggers.

    Idempotent: a refunded order has refund_status='refunded' and is
    never selected again; a concurrent second refund for the same
    order hits the (order_id, reason) UNIQUE and is counted skipped.
    One bad order never blocks the rest: per-order failure is logged,
    rolled back, counted, and retried on the next run."""
    refunded, voided, failed, skipped = 0, 0, 0, 0
    orders = (db.query(Order)
              .filter(Order.order_type == "package",
                      Order.payment_status == "paid",
                      Order.refund_status == "none").all())
    for order in orders:
        try:
            item = (db.query(models.WalletItem)
                    .filter(models.WalletItem.order_id == order.id).first())
            if item is None or item.status != 1:
                continue
            coupon = db.query(models.Coupon).filter(
                models.Coupon.id == item.coupon_id).first()
            if coupon is None or coupon.valid_to >= _now():
                continue
            policy = coupon.refund_policy or "anytime"
            if policy == "non_refundable":
                _void_expired_voucher(db, order)
                voided += 1
            else:
                _process_refund(db, order, "過期自動退")
                refunded += 1
        except HTTPException as exc:
            db.rollback()
            if exc.status_code == 409:
                skipped += 1
                logger.warning("expired-package sweep skipped order_id=%s: %s",
                               order.id, exc.detail)
            else:
                failed += 1
                logger.exception("expired-package sweep failed order_id=%s",
                                 order.id)
        except Exception:
            db.rollback()
            failed += 1
            logger.exception("expired-package sweep failed order_id=%s",
                             order.id)
    return {"refunded": refunded, "voided": voided,
            "failed": failed, "skipped": skipped}


def sweep_payment_timeouts(db: Session) -> dict:
    """M1 shared sweep: pending orders whose open payment attempt is
    older than PAYMENT_TIMEOUT_MINUTES → timeout + release stock.
    Same per-order logic as the lazy _refresh_payment_timeout that
    runs on order reads, but for ALL pending orders at once.
    Idempotent: a timed-out order is no longer pending and is never
    selected again."""
    closed, failed = 0, 0
    orders = (db.query(Order)
              .filter(Order.payment_status == "pending").all())
    for order in orders:
        try:
            _refresh_payment_timeout(db, order)
            if order.payment_status == "timeout":
                closed += 1
        except Exception:
            db.rollback()
            failed += 1
            logger.exception("payment-timeout sweep failed order_id=%s",
                             order.id)
    return {"closed": closed, "failed": failed}


def _process_expired_for_user(db: Session, user_id: int) -> dict:
    """Expired paid package vouchers → auto-refund (unless the template
    says non-refundable, then just void). Returns counts."""
    refunded, voided = 0, 0
    orders = (db.query(Order)
              .filter(Order.user_id == user_id,
                      Order.order_type == "package",
                      Order.payment_status == "paid",
                      Order.refund_status == "none").all())
    for order in orders:
        item = (db.query(models.WalletItem)
                .filter(models.WalletItem.order_id == order.id).first())
        if item is None or item.status != 1:
            continue
        coupon = db.query(models.Coupon).filter(
            models.Coupon.id == item.coupon_id).first()
        if coupon is None or coupon.valid_to >= _now():
            continue
        policy = coupon.refund_policy or "anytime"
        if policy == "non_refundable":
            _void_expired_voucher(db, order)
            voided += 1
        else:
            _process_refund(db, order, "過期自動退")
            refunded += 1
    return {"refunded": refunded, "voided": voided}


def _purchase_response(db: Session, order: Order, attempt: Payment,
                       client_secret: str, message: str) -> dict:
    return {
        "order": order_out(db, order),
        "payment": payment_out(attempt),
        "client_secret": client_secret,
        "gateway": "stripe",
        "message": message,
    }


# ---------- packages: list / detail / create ----------
@router.get("/packages")
def list_packages(shop_id: int = Query(None),
                  status: str = Query("active"),
                  db: Session = Depends(get_db)):
    query = db.query(Package)
    if shop_id is not None:
        query = query.filter(Package.shop_id == shop_id)
    if status:
        query = query.filter(Package.status == status)
    packages = query.order_by(Package.id).all()
    return {"packages": [package_out(db, p) for p in packages]}


@router.get("/package-details")
def package_details(package_id: int, db: Session = Depends(get_db),
                    user: models.User | None = Depends(get_optional_user)):
    pkg = _get_package(db, package_id)
    # Gap 10: a logged-in caller gets the per-user limit_remaining;
    # guests get the cap (see serializers_packages.package_out).
    return {"package": package_out(db, pkg, user_id=user.id if user else None)}


@router.post("/packages", status_code=201)
def create_package(body: sp.PackageCreateIn,
                   vendor: models.User = Depends(require_role("vendor", "admin")),
                   db: Session = Depends(get_db)):
    if body.refund_policy not in REFUND_POLICIES:
        raise HTTPException(status_code=400, detail="refund_policy 不正確")
    if body.package_price > body.original_price:
        raise HTTPException(status_code=400, detail="套餐價不可高於原價")
    if body.valid_to <= body.valid_from:
        raise HTTPException(status_code=400, detail="有效期不正確")
    shop = db.query(models.Shop).filter(models.Shop.id == body.shop_id).first()
    if shop is None:
        raise HTTPException(status_code=404, detail="找不到此商店")
    if vendor.role == "vendor" and shop.vendor_id != vendor.id:
        raise HTTPException(status_code=403, detail="只能為自己的商店建立套餐")
    pkg = Package(
        shop_id=body.shop_id, name=body.name, name_en=body.name_en,
        items=json.dumps([i.model_dump() for i in body.items],
                         ensure_ascii=False),
        person_count=body.person_count,
        original_price=body.original_price,
        package_price=body.package_price,
        valid_from=body.valid_from, valid_to=body.valid_to,
        usage_rules=json.dumps(body.usage_rules.model_dump(),
                               ensure_ascii=False),
        stock_total=body.stock_total, stock_left=body.stock_total,
        refund_policy=body.refund_policy, status="active")
    db.add(pkg)
    db.flush()
    # Coupon template for the package voucher (kind='package').
    coupon = models.Coupon(
        shop_id=body.shop_id, vendor_id=shop.vendor_id,
        coupon_type="Price", coupon_name=body.name,
        discount_price=body.package_price,
        regular_price=body.original_price,
        valid_from=body.valid_from, valid_to=body.valid_to,
        total=body.stock_total, remaining=body.stock_total,
        status="active",
        additional_terms="套餐券：到店出示 QR 碼核銷；整張一次核銷，不可拆分。",
        kind="package", package_id=pkg.id,
        person_count=body.person_count,
        usage_rules=json.dumps(body.usage_rules.model_dump(),
                               ensure_ascii=False),
        refund_policy=body.refund_policy)
    db.add(coupon)
    db.commit()
    db.refresh(pkg)
    return {"package": package_out(db, pkg),
            "coupon_id": coupon.id, "message": "套餐已建立"}


# ---------- purchase ----------
@router.post("/packages/{package_id}/purchase")
def purchase_package(package_id: int, body: sp.PurchaseIn,
                     user: models.User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    if body.payment_method not in PAYMENT_METHODS:
        raise HTTPException(status_code=400, detail="支付方式須為 fps 或 card")
    # FIX-3: lock the package row; the stock check below then reads
    # the locked row, and a concurrent purchase blocks until commit.
    pkg = _get_package_locked(db, package_id)
    if pkg.status != "active":
        raise HTTPException(status_code=400, detail="此套餐暫停發售")
    if pkg.valid_from > _now() or pkg.valid_to < _now():
        raise HTTPException(status_code=400, detail="此套餐不在有效期內")
    # Idempotent replay: same client_ref returns the original order.
    if body.client_ref:
        existing = (db.query(Order)
                    .filter(Order.user_id == user.id,
                            Order.package_id == pkg.id,
                            Order.client_ref == body.client_ref,
                            Order.payment_status != "cancelled")
                    .order_by(Order.id.desc()).first())
        if existing is not None:
            _refresh_payment_timeout(db, existing)
            attempt = _latest_payment(db, existing.id)
            return _purchase_response(
                db, existing, attempt,
                attempt.client_secret if attempt else "",
                "訂單已存在，返回原訂單")
    if pkg.stock_left < body.quantity:
        raise HTTPException(status_code=400, detail="此套餐已售罄")
    # Gap 7: quantity scales the total price and the stock reservation.
    total = pkg.package_price * body.quantity
    try:
        order = Order(
            user_id=user.id, order_type="package", package_id=pkg.id,
            quantity=body.quantity,
            amount=total, paid_amount=0,
            payment_method=body.payment_method, payment_status="pending",
            refund_status="none", settlement_status="pending",
            commission_amount=0, net_amount=0, client_ref=body.client_ref or "")
        db.add(order)
        # Reserve `quantity` units until the payment settles / fails /
        # times out.
        pkg.stock_left -= body.quantity
        _sync_coupon_stock(db, pkg)
        db.flush()
        try:
            intent = create_payment_intent(order.id, float(total),
                                           body.payment_method)
        except GatewayError as exc:
            # Gateway unreachable before any attempt existed: drop the
            # half-made order and release the reservation so a retry is clean.
            db.delete(order)
            pkg.stock_left += body.quantity
            _sync_coupon_stock(db, pkg)
            db.commit()
            logger.exception("package purchase gateway failed package_id=%s: %s",
                             package_id, exc)
            raise HTTPException(status_code=502,
                                detail=GATEWAY_FAILURE_MESSAGE)
        attempt = Payment(
            order_id=order.id, gateway="stripe",
            gateway_ref=intent.get("gateway_ref", ""),
            client_secret=intent.get("client_secret", ""),
            method=body.payment_method, amount=total,
            currency=intent.get("currency", "HKD"), status="requires_payment")
        db.add(attempt)
        db.commit()
    except IntegrityError:
        # FIX-3 backstop: the CHECK (stock_left >= 0) fired — the row
        # lock did not serialise (SQLite ignores FOR UPDATE). The
        # reservation never landed; report sold out instead of a 500.
        db.rollback()
        raise HTTPException(status_code=400, detail="此套餐已售罄")
    db.refresh(order)
    db.refresh(attempt)
    return _purchase_response(
        db, order, attempt, intent.get("client_secret", ""),
        "訂單已建立，請完成支付")


# ---------- orders ----------
@router.get("/orders")
def list_orders(user: models.User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    _process_expired_for_user(db, user.id)
    orders = (db.query(Order).filter(Order.user_id == user.id)
              .order_by(Order.id.desc()).all())
    for order in orders:
        _refresh_payment_timeout(db, order)
    return {"orders": [order_out(db, o) for o in orders]}


@router.get("/orders/{order_id}")
def order_detail(order_id: int, user: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    order = _get_order_for_user(db, order_id, user.id)
    _process_expired_for_user(db, user.id)
    _refresh_payment_timeout(db, order)
    db.refresh(order)
    return {"order": order_out(db, order)}


@router.post("/orders/{order_id}/confirm-payment")
def confirm_payment(order_id: int, body: sp.ConfirmPaymentIn,
                    user: models.User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    order = _get_order_for_user(db, order_id, user.id)
    _refresh_payment_timeout(db, order)
    if order.payment_status != "pending":
        raise HTTPException(status_code=400, detail="訂單已不在待支付狀態")
    if body.payment_id is not None:
        attempt = (db.query(Payment)
                   .filter(Payment.id == body.payment_id,
                           Payment.order_id == order.id).first())
        if attempt is None:
            raise HTTPException(status_code=404, detail="找不到此筆支付")
    else:
        attempt = _latest_payment(db, order.id)
    if attempt is None:
        raise HTTPException(status_code=400, detail="訂單沒有待處理的支付")
    # FIX-1: the client-supplied outcome is never trusted on its own.
    # - Key configured: settle only after querying the gateway for the
    #   payment_intent status; body.outcome is ignored entirely.
    # - No key (local simulator): outcome is accepted only in dev;
    #   any other env gets 403 (a buyer must not self-confirm payment).
    if config.STRIPE_SECRET_KEY:
        try:
            intent = retrieve_payment_intent(attempt.gateway_ref)
        except GatewayError as exc:
            logger.exception("confirm-payment gateway failed order_id=%s: %s",
                             order.id, exc)
            raise HTTPException(
                status_code=502,
                detail=GATEWAY_FAILURE_MESSAGE)
        status = intent.get("status", "")
        if status == "succeeded":
            outcome = "succeeded"
        elif status == "canceled":
            outcome = "failed"
        else:
            raise HTTPException(
                status_code=400, detail="支付尚未成功，訂單仍為待支付")
    elif config.APP_ENV != "dev":
        raise HTTPException(
            status_code=403,
            detail="非開發環境不接受客戶端傳入的支付結果，請以網關通知為準")
    else:
        outcome = body.outcome
    _apply_payment_result(db, order, attempt, outcome,
                          body.failure_message)
    db.refresh(order)
    messages = {
        "succeeded": "支付成功，套餐券已存入 Q Wallet",
        "failed": "支付失敗，可重試或取消訂單",
        "timeout": "支付逾時，可重試或取消訂單",
    }
    return {"order": order_out(db, order),
            "message": messages.get(outcome, "")}


@router.post("/orders/{order_id}/retry-payment")
def retry_payment(order_id: int, body: sp.RetryPaymentIn,
                  user: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    # FIX-3: lock the order row (one retry at a time per order) and
    # the package row (stock decrement below) before re-checking.
    order = _get_order_for_user_locked(db, order_id, user.id)
    if order.payment_status not in ("failed", "timeout"):
        raise HTTPException(status_code=400, detail="只有支付失敗或逾時的訂單可以重試")
    method = body.payment_method or order.payment_method
    if method not in PAYMENT_METHODS:
        raise HTTPException(status_code=400, detail="支付方式須為 fps 或 card")
    pkg = _get_package_locked(db, order.package_id) if order.package_id else None
    if pkg is None or pkg.stock_left <= 0:
        raise HTTPException(status_code=400, detail="此套餐已售罄，無法重試")
    previous_status = order.payment_status
    order.payment_status = "pending"
    order.payment_method = method
    pkg.stock_left -= 1
    _sync_coupon_stock(db, pkg)
    try:
        db.flush()
        try:
            intent = create_payment_intent(order.id, float(order.amount), method)
        except GatewayError as exc:
            # Restore the pre-retry state and release the reservation.
            order.payment_status = previous_status
            pkg.stock_left += 1
            _sync_coupon_stock(db, pkg)
            db.commit()
            logger.exception("retry-payment gateway failed order_id=%s: %s",
                             order.id, exc)
            raise HTTPException(status_code=502,
                                detail=GATEWAY_FAILURE_MESSAGE)
        attempt = Payment(
            order_id=order.id, gateway="stripe",
            gateway_ref=intent.get("gateway_ref", ""),
            client_secret=intent.get("client_secret", ""),
            method=method, amount=order.amount,
            currency=intent.get("currency", "HKD"), status="requires_payment")
        db.add(attempt)
        db.commit()
    except IntegrityError:
        # FIX-3 backstop: the CHECK (stock_left >= 0) fired — the row
        # lock did not serialise (SQLite ignores FOR UPDATE).
        db.rollback()
        raise HTTPException(status_code=400, detail="此套餐已售罄，無法重試")
    db.refresh(order)
    db.refresh(attempt)
    return _purchase_response(db, order, attempt,
                              intent.get("client_secret", ""),
                              "已重新發起支付，請完成支付")


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, user: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    order = _get_order_for_user(db, order_id, user.id)
    _refresh_payment_timeout(db, order)
    if order.payment_status != "pending":
        raise HTTPException(status_code=400, detail="只有待支付的訂單可以取消")
    attempt = _latest_payment(db, order.id)
    if attempt is not None and attempt.status in ("requires_payment", "processing"):
        attempt.status = "cancelled"
    order.payment_status = "cancelled"
    _release_stock(db, order)
    db.commit()
    return {"order": order_out(db, order), "message": "訂單已取消"}


# ---------- refunds ----------
def _refund_order_checked(db: Session, order: Order, reason: str) -> dict:
    if order.payment_status != "paid":
        raise HTTPException(status_code=400, detail="只有已支付的訂單可以退款")
    if order.refund_status != "none":
        raise HTTPException(status_code=400, detail="此訂單已有退款記錄")
    item = (db.query(models.WalletItem)
            .filter(models.WalletItem.order_id == order.id).first())
    if item is None:
        raise HTTPException(status_code=400, detail="找不到此訂單的套餐券")
    if item.status == 2:
        raise HTTPException(status_code=400, detail="已核銷的套餐券不可退款")
    if item.status == 3:
        raise HTTPException(status_code=400, detail="套餐券已失效，不可退款")
    coupon = db.query(models.Coupon).filter(
        models.Coupon.id == item.coupon_id).first()
    policy = (coupon.refund_policy if coupon else None) or "anytime"
    if policy == "non_refundable":
        raise HTTPException(status_code=400, detail="此為特價券，不設退款")
    if policy == "expiry_only":
        raise HTTPException(status_code=400,
                            detail="此券僅支持過期自動退，過期前不設手動退款")
    refund = _process_refund(db, order, reason or "用戶申請退款")
    return {"refund": refund_out(refund),
            "order": order_out(db, order),
            "message": "退款成功，套餐券已作廢；款項原路返回，預計 1–3 個工作日到賬"}


@router.post("/orders/{order_id}/refund")
def refund_order(order_id: int, body: sp.RefundIn,
                 user: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    # FIX-3: lock the order row; _refund_order_checked re-checks
    # refund_status on the locked row before _process_refund calls
    # the gateway, so a concurrent refund blocks here.
    order = _get_order_for_user_locked(db, order_id, user.id)
    return _refund_order_checked(db, order, body.reason)


@router.post("/packages/{package_id}/refund")
def refund_package(package_id: int, body: sp.RefundIn,
                   user: models.User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    pkg = _get_package(db, package_id)
    # FIX-3: lock the newest paid, unrefunded order of this package so
    # a concurrent refund of the same order blocks here.
    order = (db.query(Order)
             .filter(Order.user_id == user.id,
                     Order.package_id == pkg.id,
                     Order.payment_status == "paid",
                     Order.refund_status == "none")
             .order_by(Order.id.desc())
             .with_for_update().first())
    if order is None:
        raise HTTPException(status_code=404, detail="沒有可退款的已支付訂單")
    return _refund_order_checked(db, order, body.reason)


@router.post("/orders/process-expired-refunds")
def process_expired_refunds(
        admin: models.User = Depends(require_role("admin")),
        db: Session = Depends(get_db)):
    """Sweep: expired paid package vouchers → auto-refund (or void if
    non-refundable). Idempotent; safe to run on a schedule. M1: this
    manual endpoint now calls the same sweep_expired_packages() the
    background scheduler runs — it stays as the admin backfill path."""
    result = sweep_expired_packages(db)
    return {**result,
            "message": f"過期自動退 {result['refunded']} 筆，"
                       f"作廢不退 {result['voided']} 筆"}


# ---------- Stripe test-mode webhook ----------
@router.post("/payments/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Stripe webhook (FIX-1: signature-verified, gateway-sourced).

    Verification runs on the RAW body before anything in it is trusted:
    - STRIPE_SECRET_KEY and/or STRIPE_WEBHOOK_SECRET configured:
      Stripe-Signature is mandatory and must verify (timestamp
      tolerance); missing / wrong / stale signature -> 400.
    - Neither configured: accepted unsigned only when APP_ENV=dev
      (local simulator flow); any other env -> 400 outright.
    Maps payment_intent.succeeded / payment_intent.payment_failed onto
    the same apply function as confirm-payment. A replayed event for
    an already-settled order returns 200 + ignored (idempotent; the
    voucher issue path itself is also idempotent)."""
    raw = await request.body()
    signature = request.headers.get("Stripe-Signature", "")
    if config.STRIPE_SECRET_KEY or config.STRIPE_WEBHOOK_SECRET:
        if not verify_webhook_signature(raw, signature):
            raise HTTPException(status_code=400, detail="webhook 簽名校驗失敗")
    elif config.APP_ENV != "dev":
        raise HTTPException(
            status_code=400,
            detail="非開發環境 webhook 須配置簽名密鑰")
    try:
        parsed = json.loads(raw) if raw else {}
        body = sp.WebhookIn.model_validate(parsed)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="webhook 內容不正確")
    data = body.data or {}
    obj = data.get("object") or {}
    gateway_ref = obj.get("id", "")
    if not gateway_ref:
        raise HTTPException(status_code=400, detail="缺少 payment_intent id")
    attempt = db.query(Payment).filter(
        Payment.gateway_ref == gateway_ref).first()
    if attempt is None:
        raise HTTPException(status_code=404, detail="找不到此筆支付")
    if body.type == "payment_intent.succeeded":
        outcome = "succeeded"
    elif body.type == "payment_intent.payment_failed":
        outcome = "failed"
    else:
        return {"received": True, "ignored": body.type}
    order = db.query(Order).filter(Order.id == attempt.order_id).first()
    if order is None:
        raise HTTPException(status_code=404, detail="找不到此訂單")
    _refresh_payment_timeout(db, order)
    if order.payment_status != "pending":
        return {"received": True, "ignored": "order already settled"}
    _apply_payment_result(db, order, attempt, outcome,
                          obj.get("last_payment_error", {}).get("message", "")
                          if isinstance(obj.get("last_payment_error"), dict) else "")
    return {"received": True, "order_id": order.id, "outcome": outcome}
