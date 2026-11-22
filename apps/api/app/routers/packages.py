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

P1-A (paid coupons, route b): an Order gains a coupon type
(order_type='coupon', orders.coupon_id) instead of routing paid
coupons through Package. The purchase / confirm / webhook / retry /
cancel / refund / timeout machinery below is order-generic; only the
stock, voucher-issue and reservation helpers branch on package vs
coupon. Settlement needs no change: a wallet item that links an
order settles at order.paid_amount via settlement_basis().
"""
import json
import logging
from uuid import uuid4
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import cashback_service, config, late_payments, listing, models
from ..package_templates import current_package_coupon, sync_edited_package_template
from ..settlement_ledger import reverse_sale
from ..database import get_db
from ..deps import get_current_user, get_optional_user, require_role
from ..models_packages import LatePaymentRefund, Order, Package, Payment, Refund, REFUND_POLICIES
from ..shared import issue_wallet_item
from ..payments_gateway import (
    GatewayError, create_payment_intent, refund_payment,
    retrieve_payment_intent, retrieve_refund, verify_webhook_signature,
)
from ..serializers_packages import (
    order_out, package_out, payment_out, purchase_allowance, refund_out,
)
from .. import schemas_packages as sp

router = APIRouter()

logger = logging.getLogger(__name__)

# P1-5: gateway failures are never passed through to the user. Every
# gateway-error surface returns this one generic message; the gateway
# detail goes to the server log only (logger.exception at each site).
GATEWAY_FAILURE_MESSAGE = "支付失败，请稍后重试"

PAYMENT_METHODS = ("fps", "card")


from ..timeutil import utcnow as _now


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
    if db.get_bind().dialect.name == "sqlite":
        db.query(Package).filter_by(id=package_id).update({"stock_left": Package.stock_left}, synchronize_session=False)
    pkg = (db.query(Package).filter(Package.id == package_id)
           .with_for_update().populate_existing().first())
    if pkg is None:
        raise HTTPException(status_code=404, detail="找不到此套餐")
    return pkg


def _get_coupon_locked(db: Session, coupon_id: int) -> models.Coupon:
    """P1-A: same FIX-3 row lock as _get_package_locked, but for the
    coupon row whose `remaining` is the paid coupon's stock."""
    if db.get_bind().dialect.name == "sqlite":
        db.query(models.Coupon).filter_by(id=coupon_id).update(
            {"remaining": models.Coupon.remaining}, synchronize_session=False)
    coupon = (db.query(models.Coupon).filter(models.Coupon.id == coupon_id)
              .with_for_update().populate_existing().first())
    if coupon is None:
        raise HTTPException(status_code=404, detail="找不到此優惠券")
    return coupon


def _package_coupon(db: Session, package: Package):
    return current_package_coupon(db, package.id)


def _sync_coupon_stock(db: Session, package: Package):
    """Keep the package coupon template's remaining in step with stock."""
    coupon = _package_coupon(db, package)
    if coupon is not None:
        coupon.remaining = max(0, package.stock_left)
    if package.stock_left <= 0 and package.status == "active":
        package.status = "sold_out"
    elif package.stock_left > 0 and package.status == "sold_out":
        package.status = "active"


def _check_reservation(db, pkg, user_id, quantity):
    if pkg.stock_left < quantity:
        raise HTTPException(400, "此套餐已售罄")
    if pkg.status != "active" or not pkg.valid_from <= _now() < pkg.valid_to:
        raise HTTPException(400, "此套餐暫停發售或不在有效期內")
    _, remaining = purchase_allowance(db, pkg, user_id)
    if quantity > remaining:
        raise HTTPException(400, "超出此套餐每人限購份數")


def _check_coupon_reservation(db, coupon, quantity=1):
    """P1-A: paid-coupon reservation check. The coupon itself is the
    voucher, so its `remaining` is the stock; there is no per-user
    limit (one coupon per order, repeat purchase = a new order)."""
    if coupon.kind == "package":
        raise HTTPException(400, "套餐券請購買套餐，不可直接購買")
    if coupon.sale_price is None or float(coupon.sale_price) <= 0:
        raise HTTPException(400, "免費券請直接領取，不可購買")
    if coupon.remaining < quantity:
        raise HTTPException(400, "此優惠券已售罄")
    if coupon.status != "active" or not coupon.valid_from <= _now() < coupon.valid_to:
        raise HTTPException(400, "此優惠券暫停發售或不在有效期內")


def _sync_paid_coupon_stock(coupon):
    """Keep the sellable state in sync when paid-coupon stock moves."""
    if coupon.remaining <= 0 and coupon.status == "active":
        coupon.status = "sold_out"
    elif coupon.remaining > 0 and coupon.status == "sold_out":
        coupon.status = listing.live_status(coupon.valid_from, coupon.valid_to, coupon.remaining)


def _release_stock(db: Session, order: Order):
    if order.coupon_id is not None:
        # P1-A: return the reserved unit(s) to the coupon's remaining,
        # capped by what is not already promised to other live orders
        # (same formula as the package branch below).
        coupon = (db.query(models.Coupon)
                  .filter(models.Coupon.id == order.coupon_id)
                  .with_for_update().populate_existing().first())
        if coupon is not None:
            qty = order.quantity or 1
            reserved = db.query(func.coalesce(func.sum(Order.quantity), 0)).filter(
                Order.coupon_id == coupon.id, Order.id != order.id,
                Order.payment_status.in_(("pending", "paid")), Order.refund_status != "refunded", Order.settlement_status != "void").scalar()
            available = max(0, (coupon.total or 0) - reserved)
            coupon.remaining = min(available, (coupon.remaining or 0) + qty)
            _sync_paid_coupon_stock(coupon)
        return
    if order.package_id is None:
        return
    # FIX-3: lock the package row so a release cannot lose an update
    # against a concurrent purchase's decrement.
    pkg = (db.query(Package).filter(Package.id == order.package_id)
           .with_for_update().populate_existing().first())
    if pkg is not None:
        # Gap 7: release exactly what this order reserved (1 by default).
        qty = order.quantity or 1
        # A legacy multi-unit retry could oversell. Already promised units
        # must be covered before a cancellation can put stock back on sale.
        reserved = db.query(func.coalesce(func.sum(Order.quantity), 0)).filter(
            Order.package_id == pkg.id, Order.id != order.id,
            Order.payment_status.in_(("pending", "paid")), Order.refund_status != "refunded", Order.settlement_status != "void").scalar()
        available = max(0, pkg.stock_total - reserved)
        pkg.stock_left = min(available, pkg.stock_left + qty)
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


def _lock_order(db, order_id):
    if db.get_bind().dialect.name == "sqlite":
        db.query(Order).filter_by(id=order_id).update(
            {"payment_status": Order.payment_status}, synchronize_session=False)
    return db.query(Order).filter_by(id=order_id).with_for_update().populate_existing().first()


def _lock_order_with_account(db, order_id):
    """Background refund work uses the same account -> order order as API writes."""
    user_id = db.query(Order.user_id).filter_by(id=order_id).scalar()
    if user_id is None:
        return None
    cashback_service.lock_account(db, user_id)
    order = _lock_order(db, order_id)
    if order is not None and order.user_id != user_id:
        raise HTTPException(409, "訂單資料需要核對，請聯絡客服")
    return order


def _get_order_for_user_locked(db: Session, order_id: int,
                               user_id: int) -> Order:
    # FIX-3: lock the order row before any check-then-write on it
    # (refund status, retry state) so a concurrent request for the
    # same order blocks here instead of passing the check twice.
    order = _lock_order(db, order_id)
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
        order = _lock_order(db, order.id)
        if order.payment_status != "pending" or _latest_payment(db, order.id).id != attempt.id:
            return order
        attempt.status = "timeout"
        order.payment_status = "timeout"
        _release_stock(db, order)
        db.commit()
    return order


def _issue_voucher(db: Session, order: Order, package: Package = None):
    """Payment succeeded → voucher into Q Wallet (status 1).
    Idempotent: a second confirm of the same order returns the voucher.
    Package orders issue the package's coupon template; P1-A coupon
    orders issue the coupon itself."""
    existing = (db.query(models.WalletItem)
                .filter(models.WalletItem.order_id == order.id).first())
    if existing is not None:
        return existing
    if order.coupon_id is not None:
        coupon = db.get(models.Coupon, order.coupon_id)
        if coupon is None or coupon.kind == "package":
            raise HTTPException(status_code=409, detail="訂單優惠券需要人工核對")
        return issue_wallet_item(db, order.user_id, coupon.id, "purchase", order_id=order.id)
    if package is None:
        raise HTTPException(status_code=409, detail="訂單套餐券模板需要人工核對")
    coupon = (db.get(models.Coupon, order.package_coupon_id)
              if order.package_coupon_id else _package_coupon(db, package))
    if (coupon is None or coupon.package_id != package.id or coupon.shop_id != package.shop_id
            or coupon.kind != "package"):
        raise HTTPException(status_code=409, detail="訂單套餐券模板需要人工核對")
    return issue_wallet_item(db, order.user_id, coupon.id, "purchase", order_id=order.id)


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
        if package is not None or order.coupon_id is not None:
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


def _claim_unused_voucher(db, order, *, expired=False):
    # Same conditional wallet transition as the merchant scanner. A refund
    # must win this write before contacting the gateway or returning stock.
    q = db.query(models.WalletItem).filter_by(order_id=order.id).filter(
        models.WalletItem.used_at.is_(None),
        ~models.WalletItem.id.in_(db.query(models.Redemption.wallet_item_id).filter(
            models.Redemption.revoked_at.is_(None))))
    q = q.filter(models.WalletItem.status.in_((1, 3) if expired else (1,)))
    if q.update({"status": 3, "code": "", "qr_expires_at": None}, synchronize_session=False) != 1:
        raise HTTPException(400, "套餐券已使用或已失效，不可退款")


def _paid_attempt(db, order):
    attempt = db.query(Payment).filter_by(order_id=order.id, status="succeeded").order_by(Payment.id.desc()).first()
    if (not attempt or not attempt.gateway_ref or attempt.amount != order.paid_amount
            or (attempt.currency or "").upper() != "HKD"):
        raise HTTPException(409, "找不到相符的付款記錄，請聯絡客服核對")
    return attempt


def _resume_refund(db, order_id):
    order = _lock_order_with_account(db, order_id)
    if order is None:
        raise HTTPException(404, "找不到此訂單")
    refund = db.query(Refund).filter_by(order_id=order_id).order_by(Refund.id.desc()).first()
    if refund is None:
        raise HTTPException(409, "找不到退款記錄，請聯絡客服核對")
    if order.refund_status != "requested" or refund.status != "requested":
        return refund
    attempt = _paid_attempt(db, order)
    try:
        if (refund.gateway_ref or "").startswith("request-"):
            if _now() - refund.created_at >= timedelta(hours=23):
                raise HTTPException(409, "退款結果待財務核對，請聯絡客服")
            result = refund_payment(attempt.gateway_ref, float(refund.amount),
                                    request_key=refund.gateway_ref)
        else:
            result = retrieve_refund(refund.gateway_ref)
    except GatewayError:
        logger.warning("Refund awaiting gateway confirmation for order %s", order_id)
        raise HTTPException(502, GATEWAY_FAILURE_MESSAGE)
    if (not result.get("gateway_ref") or result.get("payment_intent") != attempt.gateway_ref
            or result.get("currency", "").upper() != "HKD"
            or result.get("amount") != float(refund.amount)):
        raise HTTPException(502, "退款資料待核對")
    refund.gateway_ref = result["gateway_ref"]
    status = result["status"]
    if status == "succeeded":
        order.refund_status = refund.status = "refunded"
        # Reconcile older revoked vouchers whose settlement or cashback
        # reversal may not have been recorded by their original app version.
        redemptions = (db.query(models.Redemption).join(models.WalletItem)
                       .filter(models.WalletItem.order_id == order.id).all())
        if any(red.revoked_at is not None for red in redemptions):
            cashback_service.lock_account(db, order.user_id)
        for red in redemptions:
            if red.revoked_at is not None:
                cashback_service.void_for_revocation(db, red)
            reverse_sale(db, red, "refunded")
        attempt.status = "refunded"
        order.settlement_status = "void"
        order.commission_amount = order.net_amount = 0
        _release_stock(db, order)
    elif status in ("failed", "canceled"):
        order.refund_status = refund.status = "rejected"
        item = db.query(models.WalletItem).filter_by(order_id=order.id).one()
        if item.coupon.valid_to > _now():
            item.status = 1
    db.commit()
    return refund


def _process_refund(db, order, reason, *, expired=False):
    _paid_attempt(db, order)
    _claim_unused_voucher(db, order, expired=expired)
    previous = db.query(Refund).filter_by(order_id=order.id).count()
    if previous:
        reason = f"{reason[:225]}（重試 {previous}）"
    refund = Refund(order_id=order.id, user_id=order.user_id, amount=order.paid_amount,
                    reason=reason, status="requested", gateway_ref="request-" + uuid4().hex)
    order.refund_status = "requested"
    db.add(refund)
    db.commit()  # Durable hold and idempotency key before the external operation.
    return _resume_refund(db, order.id)


def _settle_expired(db, order_id):
    order = _lock_order_with_account(db, order_id)
    if order is None:
        return None
    if order.refund_status == "requested":
        return _resume_refund(db, order_id).status
    if order.payment_status != "paid" or order.refund_status not in ("none", "rejected") or order.settlement_status == "void":
        return None
    item = db.query(models.WalletItem).filter_by(order_id=order_id).populate_existing().first()
    if not item or item.status not in (1, 3) or item.coupon.valid_to > _now():
        return None
    if order.refund_status == "rejected":
        last = db.query(Refund).filter_by(order_id=order_id).order_by(Refund.id.desc()).first()
        if last and last.created_at > _now() - timedelta(days=1):
            return None
    if item.coupon.refund_policy == "non_refundable":
        _claim_unused_voucher(db, order, expired=True)
        order.settlement_status = "void"
        order.net_amount = 0
        _release_stock(db, order)
        db.commit()
        return "void"
    return _process_refund(db, order, "過期自動退", expired=True).status


def sweep_expired_packages(db: Session, user_id=None) -> dict:
    # Older versions committed a successful payment separately from its voucher.
    # P1-A: the same sweep covers paid-coupon orders (order_type='coupon').
    missing = (db.query(Order.id).outerjoin(models.WalletItem, models.WalletItem.order_id == Order.id)
               .filter(Order.order_type.in_(("package", "coupon")),
                       (Order.package_id.isnot(None)) | (Order.coupon_id.isnot(None)),
                       Order.payment_status == "paid", Order.refund_status == "none",
                       Order.settlement_status != "void", models.WalletItem.id.is_(None)))
    if user_id is not None:
        missing = missing.filter(Order.user_id == user_id)
    recovery_failures = 0
    for (oid,) in missing.all():
        try:
            order = _lock_order(db, oid)
            if order.payment_status == "paid" and order.refund_status == "none" and order.settlement_status != "void":
                _paid_attempt(db, order)
                _issue_voucher(db, order,
                               _get_package(db, order.package_id) if order.package_id else None)
            db.commit()
        except Exception:
            db.rollback()
            recovery_failures += 1
            logger.exception("Could not reconcile missing voucher for order %s", oid)
    # Include legacy expired vouchers marked status 3 by an earlier wallet read.
    q = (db.query(Order.id).join(models.WalletItem, models.WalletItem.order_id == Order.id)
         .join(models.Coupon, models.Coupon.id == models.WalletItem.coupon_id)
         .filter(Order.order_type.in_(("package", "coupon")), Order.payment_status == "paid",
                 (Order.refund_status == "requested") | (
                     (Order.refund_status.in_(("none", "rejected"))) & (Order.settlement_status != "void") &
                     models.WalletItem.status.in_((1, 3)) & (models.Coupon.valid_to <= _now()))))
    if user_id is not None:
        q = q.filter(Order.user_id == user_id)
    stats = {"refunded": 0, "voided": 0, "failed": recovery_failures, "skipped": 0}
    for (oid,) in q.all():
        try:
            status = _settle_expired(db, oid)
            db.commit()
            key = {"refunded": "refunded", "void": "voided"}.get(status, "skipped")
            stats[key] += 1
        except Exception:
            db.rollback()
            stats["failed"] += 1
            logger.exception("Could not settle expired voucher or pending refund for order %s", oid)
    return stats


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
    return sweep_expired_packages(db, user_id=user_id)


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
    pkg, coupon = store_package(db, shop, body)
    db.commit()
    db.refresh(pkg)
    return {"package": package_out(db, pkg),
            "coupon_id": coupon.id, "message": "套餐已建立"}


def store_package(db: Session, shop: models.Shop, body: sp.PackageCreateIn):
    # MVP defaults to automatic approval; deployments can enable manual
    # review without bypassing date or stock rules.
    status = ("pending_review" if config.PACKAGE_REVIEW_REQUIRED else
              listing.live_status(body.valid_from, body.valid_to, body.stock_total))
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
        refund_policy=body.refund_policy, status=status)
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
        status=status,
        additional_terms="套餐券：到店出示 QR 碼核銷；整張一次核銷，不可拆分。",
        kind="package", package_id=pkg.id, is_package_template=True,
        person_count=body.person_count,
        usage_rules=json.dumps(body.usage_rules.model_dump(),
                               ensure_ascii=False),
        refund_policy=body.refund_policy)
    db.add(coupon)
    db.flush()
    return pkg, coupon


def _get_vendor_package(db: Session, package_id: int,
                        vendor: models.User, *, lock=False) -> Package:
    """The merchant's own package (admin may act on any)."""
    pkg = _get_package_locked(db, package_id) if lock else _get_package(db, package_id)
    if vendor.role != "admin":
        shop = db.query(models.Shop).filter(models.Shop.id == pkg.shop_id).first()
        if shop is None or shop.vendor_id != vendor.id:
            raise HTTPException(status_code=403, detail="沒有權限")
    return pkg


@router.get("/admin/packages")
def admin_packages(status: str = Query(None), shop_id: int = Query(None),
                   admin: models.User = Depends(require_role("admin")),
                   db: Session = Depends(get_db)):
    """§3.2 review queue: admin lists packages by status
    (pending_review by default for the review desk)."""
    query = db.query(Package)
    if shop_id is not None:
        query = query.filter(Package.shop_id == shop_id)
    if status:
        query = query.filter(Package.status == status)
    packages = query.order_by(Package.id.desc()).all()
    return {"packages": [package_out(db, p) for p in packages]}


@router.post("/admin/packages/{package_id}/approve")
def approve_package(package_id: int, approve: bool = True, reason: str = "",
                    admin: models.User = Depends(require_role("admin")),
                    db: Session = Depends(get_db)):
    """§3.2: admin reviews a package — the same flow as coupons.
    Approval lands on active / scheduled (valid_from in the future)
    / sold_out (no stock); the template coupon mirrors the package.
    Rejection records the reason. Only pending_review can be decided."""
    pkg = _get_package_locked(db, package_id)
    if pkg.status != "pending_review":
        raise HTTPException(status_code=400, detail="只有待審核的套餐可以審批")
    if approve:
        if pkg.valid_to <= _now():
            raise HTTPException(status_code=400, detail="套餐已過期，無法上架")
        pkg.status = listing.approve_status(
            pkg.valid_from, pkg.valid_to, pkg.stock_left)
        listing.sync_package_coupon_status(db, pkg, pkg.status)
        new_status = pkg.status
    else:
        pkg.status = "rejected"
        listing.sync_package_coupon_status(db, pkg, "rejected")
        new_status = "rejected"
    db.commit()
    listing.audit(db, admin.id,
                  "package.approve" if approve else "package.reject",
                  "package", pkg.id,
                  f"approved -> {new_status}" if approve else (reason or ""))
    coupon = _package_coupon(db, pkg)
    return {"package_id": pkg.id, "status": pkg.status,
            "coupon_status": coupon.status if coupon else None,
            "reason": reason if not approve else ""}


@router.patch("/vendor/packages/{package_id}")
def edit_package(package_id: int, body: sp.PackageUpdateIn,
                 vendor: models.User = Depends(require_role("vendor", "admin")),
                 db: Session = Depends(get_db)):
    """§3.2: merchant edits a package. Same state machine as coupon
    edits: active / paused / scheduled -> pending_review (re-review);
    draft / rejected -> draft; pending_review stays pending_review.
    Expired / sold_out packages cannot be edited. stock_total may not
    drop below the units already sold; the template coupon mirrors
    the package status."""
    # Purchase and edits serialize on the package row. Pending payments must
    # retain their original template until the payment result is known.
    pkg = _get_vendor_package(db, package_id, vendor, lock=True)
    pending = (db.query(Order.id).filter(Order.package_id == pkg.id,
        Order.payment_status.in_(("pending", "failed", "timeout")),
        Order.package_coupon_id.is_(None)).first())
    missing = (db.query(Order.id).outerjoin(models.WalletItem, models.WalletItem.order_id == Order.id)
               .filter(Order.package_id == pkg.id, Order.payment_status == "paid",
                       Order.package_coupon_id.is_(None), Order.refund_status != "refunded",
                       models.WalletItem.id.is_(None)).first())
    if pending or missing:
        raise HTTPException(409, "套餐有待付款或待核對的訂單，請稍後再編輯")
    if pkg.status in ("expired", "sold_out"):
        raise HTTPException(status_code=400, detail="已過期或已售罄的套餐不可編輯，請重新建立")
    if pkg.status not in listing.EDITABLE_STATES:
        raise HTTPException(status_code=400, detail="此狀態的套餐不可編輯")
    valid_from = body.valid_from if body.valid_from is not None else pkg.valid_from
    valid_to = body.valid_to if body.valid_to is not None else pkg.valid_to
    if valid_to <= valid_from:
        raise HTTPException(status_code=400, detail="有效期不正確")
    original = body.original_price if body.original_price is not None else float(pkg.original_price)
    price = body.package_price if body.package_price is not None else float(pkg.package_price)
    if price > original:
        raise HTTPException(status_code=400, detail="套餐價不可高於原價")
    if body.refund_policy is not None and body.refund_policy not in listing.REFUND_POLICIES:
        raise HTTPException(status_code=400, detail="refund_policy 不正確")
    sold = (pkg.stock_total or 0) - (pkg.stock_left or 0)
    if body.stock_total is not None and body.stock_total < sold:
        raise HTTPException(status_code=400, detail="庫存不可少於已售份數")
    if body.name is not None:
        pkg.name = body.name
    if body.name_en is not None:
        pkg.name_en = body.name_en
    if body.items is not None:
        pkg.items = json.dumps([i.model_dump() for i in body.items],
                               ensure_ascii=False)
    if body.person_count is not None:
        pkg.person_count = body.person_count
    pkg.original_price = original
    pkg.package_price = price
    pkg.valid_from = valid_from
    pkg.valid_to = valid_to
    if body.usage_rules is not None:
        pkg.usage_rules = json.dumps(body.usage_rules.model_dump(),
                                     ensure_ascii=False)
    if body.stock_total is not None:
        pkg.stock_left = max(0, (pkg.stock_left or 0)
                             + (body.stock_total - (pkg.stock_total or 0)))
        pkg.stock_total = body.stock_total
    if body.refund_policy is not None:
        pkg.refund_policy = body.refund_policy
    if pkg.status in listing.RE_REVIEW_STATES:
        if config.PACKAGE_REVIEW_REQUIRED:
            pkg.status = "pending_review"
        elif pkg.status != "paused":
            pkg.status = listing.live_status(pkg.valid_from, pkg.valid_to, pkg.stock_left)
    elif pkg.status == "rejected":
        pkg.status = "draft"
    sync_edited_package_template(db, pkg)
    db.commit()
    db.refresh(pkg)
    return {"message": "已儲存，內容將重新提交審核。" if pkg.status == "pending_review" else "已儲存。",
            "package": package_out(db, pkg)}


@router.post("/vendor/packages/{package_id}/pause")
def pause_package(package_id: int,
                  vendor: models.User = Depends(require_role("vendor", "admin")),
                  db: Session = Depends(get_db)):
    pkg = _get_vendor_package(db, package_id, vendor, lock=True)
    if pkg.status != "active":
        raise HTTPException(status_code=400, detail="只有上架中的套餐可以暫停")
    pkg.status = "paused"
    listing.sync_package_coupon_status(db, pkg, "paused")
    db.commit()
    return {"package_id": pkg.id, "status": pkg.status}


@router.post("/vendor/packages/{package_id}/resume")
def resume_package(package_id: int,
                   vendor: models.User = Depends(require_role("vendor", "admin")),
                   db: Session = Depends(get_db)):
    pkg = _get_vendor_package(db, package_id, vendor, lock=True)
    if pkg.status != "paused":
        raise HTTPException(status_code=400, detail="只有已暫停的套餐可以恢復上架")
    target = listing.live_status(pkg.valid_from, pkg.valid_to, pkg.stock_left)
    if target == "expired":
        raise HTTPException(status_code=400, detail="套餐已過期，無法重新上架")
    if target == "sold_out":
        raise HTTPException(status_code=400, detail="套餐已售罄，無法重新上架")
    pkg.status = target
    listing.sync_package_coupon_status(db, pkg, target)
    db.commit()
    return {"package_id": pkg.id, "status": pkg.status}


@router.post("/vendor/packages/{package_id}/unlist")
def unlist_package(package_id: int,
                   vendor: models.User = Depends(require_role("vendor", "admin")),
                   db: Session = Depends(get_db)):
    """§3.2: take a package off the shelf back to draft; the
    template coupon mirrors it. Expired / sold_out are already off
    the shelf."""
    pkg = _get_vendor_package(db, package_id, vendor, lock=True)
    if pkg.status in ("expired", "sold_out"):
        raise HTTPException(status_code=400, detail="已過期或已售罄的套餐不可下架")
    if pkg.status == "draft":
        raise HTTPException(status_code=400, detail="草稿本來就未上架")
    pkg.status = "draft"
    listing.sync_package_coupon_status(db, pkg, "draft")
    db.commit()
    return {"package_id": pkg.id, "status": pkg.status}


@router.post("/vendor/packages/{package_id}/submit")
def submit_package(package_id: int,
                   vendor: models.User = Depends(require_role("vendor", "admin")),
                   db: Session = Depends(get_db)):
    pkg = _get_vendor_package(db, package_id, vendor, lock=True)
    if pkg.status not in ("draft", "rejected"):
        raise HTTPException(status_code=400, detail="只有草稿或被駁回的套餐可以提交審核")
    pkg.status = ("pending_review" if config.PACKAGE_REVIEW_REQUIRED else
                  listing.live_status(pkg.valid_from, pkg.valid_to, pkg.stock_left))
    listing.sync_package_coupon_status(db, pkg, pkg.status)
    db.commit()
    return {"package_id": pkg.id, "status": pkg.status}


@router.post("/admin/listing-sweep")
def admin_listing_sweep(admin: models.User = Depends(require_role("admin")),
                        db: Session = Depends(get_db)):
    """Manual backfill for the listing-schedule job (same sweep the
    scheduler runs): activate due scheduled items, expire past
    valid_to, mark sold out. Idempotent."""
    return listing.sweep_listing_schedule(db)


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
    # Idempotent replay: same client_ref returns the original order.
    if body.client_ref:
        existing = (db.query(Order)
                    .filter(Order.user_id == user.id,
                            Order.package_id == pkg.id,
                            Order.client_ref == body.client_ref,
                            Order.payment_status != "cancelled")
                    .order_by(Order.id.desc()).first())
        if existing is not None:
            if (existing.quantity or 1) != body.quantity or existing.payment_method != body.payment_method:
                raise HTTPException(409, "訂單份數或付款方式與原訂單不同")
            # Timeout and retry both acquire order before package. Release the
            # read-only reservation lock before following that order here.
            db.rollback()
            _refresh_payment_timeout(db, existing)
            attempt = _latest_payment(db, existing.id)
            return _purchase_response(
                db, existing, attempt,
                attempt.client_secret if attempt else "",
                "訂單已存在，返回原訂單")
    _check_reservation(db, pkg, user.id, body.quantity)
    # Gap 7: quantity scales the total price and the stock reservation.
    total = pkg.package_price * body.quantity
    try:
        template = _package_coupon(db, pkg)
        if template is None:
            raise HTTPException(409, "套餐券模板缺失，請聯絡客服")
        order = Order(
            user_id=user.id, order_type="package", package_id=pkg.id,
            package_coupon_id=template.id, quantity=body.quantity,
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


# ---------- paid coupons (P1-A) ----------
@router.post("/coupons/{coupon_id}/purchase")
def purchase_coupon(coupon_id: int, body: sp.PurchaseIn,
                    user: models.User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Buy a paid coupon (order_type='coupon'). Reuses the package
    order / payment chain: reserve 1 unit of the coupon's `remaining`,
    create the order + payment attempt; confirm via
    /orders/{id}/confirm-payment or the Stripe webhook and the voucher
    lands in Q Wallet on success. One coupon per order (quantity > 1
    → 400); buying again = a new order. Free coupons are rejected
    here — claim them instead."""
    if body.payment_method not in PAYMENT_METHODS:
        raise HTTPException(status_code=400, detail="支付方式須為 fps 或 card")
    if body.quantity != 1:
        raise HTTPException(status_code=400, detail="付費券每次限購一張")
    # FIX-3: lock the coupon row; the stock check below then reads
    # the locked row, and a concurrent purchase blocks until commit.
    coupon = _get_coupon_locked(db, coupon_id)
    # Idempotent replay: same client_ref returns the original order.
    if body.client_ref:
        existing = (db.query(Order)
                    .filter(Order.user_id == user.id,
                            Order.coupon_id == coupon.id,
                            Order.order_type == "coupon",
                            Order.client_ref == body.client_ref,
                            Order.payment_status != "cancelled")
                    .order_by(Order.id.desc()).first())
        if existing is not None:
            if existing.payment_method != body.payment_method:
                raise HTTPException(409, "付款方式與原訂單不同")
            # Timeout and retry both acquire order before coupon. Release the
            # read-only reservation lock before following that order here.
            db.rollback()
            _refresh_payment_timeout(db, existing)
            attempt = _latest_payment(db, existing.id)
            return _purchase_response(
                db, existing, attempt,
                attempt.client_secret if attempt else "",
                "訂單已存在，返回原訂單")
    _check_coupon_reservation(db, coupon, 1)
    total = coupon.sale_price
    try:
        order = Order(
            user_id=user.id, order_type="coupon", coupon_id=coupon.id,
            quantity=1,
            amount=total, paid_amount=0,
            payment_method=body.payment_method, payment_status="pending",
            refund_status="none", settlement_status="pending",
            commission_amount=0, net_amount=0, client_ref=body.client_ref or "")
        db.add(order)
        # Reserve 1 unit until the payment settles / fails / times out.
        coupon.remaining -= 1
        _sync_paid_coupon_stock(coupon)
        db.flush()
        try:
            intent = create_payment_intent(order.id, float(total),
                                           body.payment_method)
        except GatewayError as exc:
            # Gateway unreachable before any attempt existed: drop the
            # half-made order and release the reservation so a retry is clean.
            db.delete(order)
            coupon.remaining += 1
            _sync_paid_coupon_stock(coupon)
            db.commit()
            logger.exception("coupon purchase gateway failed coupon_id=%s: %s",
                             coupon_id, exc)
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
        # FIX-3 backstop: the CHECK (remaining >= 0) fired — the row
        # lock did not serialise (SQLite ignores FOR UPDATE). The
        # reservation never landed; report sold out instead of a 500.
        db.rollback()
        raise HTTPException(status_code=400, detail="此優惠券已售罄")
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


@router.post("/orders/{order_id}/demo-payment")
def demo_payment(order_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if config.APP_ENV != "dev" or config.STRIPE_SECRET_KEY:
        raise HTTPException(404, "模擬支付未啟用")
    order = _get_order_for_user_locked(db, order_id, user.id)
    _refresh_payment_timeout(db, order)
    attempt = _latest_payment(db, order.id)
    if not attempt or not (attempt.gateway_ref or "").startswith("pi_test_"):
        raise HTTPException(400, "此訂單不是本機模擬付款")
    if order.payment_status != "paid":
        _apply_payment_result(db, order, attempt, "succeeded")
    return {"order": order_out(db, order)}


@router.post("/orders/{order_id}/confirm-payment")
def confirm_payment(order_id: int, body: sp.ConfirmPaymentIn,
                    user: models.User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    order = _get_order_for_user_locked(db, order_id, user.id)
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
    # P1-A: coupon orders name the voucher 優惠券, package orders keep 套餐券.
    voucher = "優惠券" if order.coupon_id is not None else "套餐券"
    messages = {
        "succeeded": f"支付成功，{voucher}已存入 Q Wallet",
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
    # the package / coupon row (stock decrement below) before re-checking.
    order = _get_order_for_user_locked(db, order_id, user.id)
    if order.payment_status not in ("failed", "timeout"):
        raise HTTPException(status_code=400, detail="只有支付失敗或逾時的訂單可以重試")
    method = body.payment_method or order.payment_method
    if method not in PAYMENT_METHODS:
        raise HTTPException(status_code=400, detail="支付方式須為 fps 或 card")
    # P1-A: coupon orders re-reserve the coupon's remaining instead.
    pkg = coupon = None
    if order.package_id:
        pkg = _get_package_locked(db, order.package_id)
    elif order.coupon_id is not None:
        coupon = _get_coupon_locked(db, order.coupon_id)
    else:
        raise HTTPException(status_code=400, detail="此訂單無法重試")
    sold_out_msg = "此套餐已售罄，無法重試" if pkg is not None else "此優惠券已售罄，無法重試"
    quantity = order.quantity or 1
    if pkg is not None:
        _check_reservation(db, pkg, user.id, quantity)
    else:
        _check_coupon_reservation(db, coupon, quantity)
    order.payment_status = "pending"
    order.payment_method = method
    if pkg is not None:
        pkg.stock_left -= quantity
        _sync_coupon_stock(db, pkg)
    else:
        coupon.remaining -= quantity
        _sync_paid_coupon_stock(coupon)
    try:
        db.flush()
        try:
            intent = create_payment_intent(order.id, float(order.amount), method)
        except GatewayError as exc:
            # Restore the pre-retry state and release the reservation.
            db.rollback()
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
        raise HTTPException(status_code=400, detail=sold_out_msg)
    db.refresh(order)
    db.refresh(attempt)
    return _purchase_response(db, order, attempt,
                              intent.get("client_secret", ""),
                              "已重新發起支付，請完成支付")


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, user: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    order = _get_order_for_user_locked(db, order_id, user.id)
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
    # P1-A: the same gate serves package and paid-coupon orders; only
    # the voucher noun in the messages differs.
    voucher = "優惠券" if order.coupon_id is not None else "套餐券"
    if order.payment_status != "paid":
        raise HTTPException(status_code=400, detail="只有已支付的訂單可以退款")
    if order.refund_status not in ("none", "rejected"):
        raise HTTPException(status_code=400, detail="此訂單已有退款記錄")
    item = (db.query(models.WalletItem)
            .filter(models.WalletItem.order_id == order.id).first())
    if item is None:
        raise HTTPException(status_code=400, detail=f"找不到此訂單的{voucher}")
    if item.status == 2:
        raise HTTPException(status_code=400, detail=f"已核銷的{voucher}不可退款")
    if item.status == 3:
        raise HTTPException(status_code=400, detail=f"{voucher}已失效，不可退款")
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
            "message": {"refunded": f"退款成功，{voucher}已作廢", "requested": f"退款處理中，{voucher}已暫停使用",
                        "rejected": "退款未成功，請查看訂單"}[refund.status]}


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
    order = (db.query(Order).join(models.WalletItem, models.WalletItem.order_id == Order.id)
             .filter(models.WalletItem.status == 1, models.WalletItem.used_at.is_(None),
                     Order.user_id == user.id, Order.package_id == pkg.id,
                     Order.payment_status == "paid",
                     Order.refund_status.in_(("none", "rejected")))
             .order_by(Order.id.desc())
             .with_for_update().first())
    if order is None:
        raise HTTPException(status_code=404, detail="沒有可退款的已支付訂單")
    return _refund_order_checked(db, _lock_order(db, order.id), body.reason)


@router.post("/coupons/{coupon_id}/refund")
def refund_coupon(coupon_id: int, body: sp.RefundIn,
                  user: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """P1-A: refund the newest refundable paid-coupon order of this
    coupon — the coupon-side mirror of /packages/{id}/refund. Same
    gate as /orders/{id}/refund (paid + unused + policy)."""
    coupon = db.query(models.Coupon).filter(models.Coupon.id == coupon_id).first()
    if coupon is None:
        raise HTTPException(status_code=404, detail="找不到此優惠券")
    # FIX-3: lock the newest paid, unrefunded order of this coupon so
    # a concurrent refund of the same order blocks here.
    order = (db.query(Order).join(models.WalletItem, models.WalletItem.order_id == Order.id)
             .filter(models.WalletItem.status == 1, models.WalletItem.used_at.is_(None),
                     Order.user_id == user.id, Order.coupon_id == coupon.id,
                     Order.order_type == "coupon",
                     Order.payment_status == "paid",
                     Order.refund_status.in_(("none", "rejected")))
             .order_by(Order.id.desc())
             .with_for_update().first())
    if order is None:
        raise HTTPException(status_code=404, detail="沒有可退款的已支付訂單")
    return _refund_order_checked(db, _lock_order(db, order.id), body.reason)


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
    matches = db.query(Payment).filter(Payment.gateway_ref == gateway_ref).limit(2).all()
    if not matches:
        raise HTTPException(status_code=404, detail="找不到此筆支付")
    if len(matches) != 1:
        raise HTTPException(status_code=409, detail="支付記錄需要人工核對")
    attempt = matches[0]
    if body.type == "payment_intent.succeeded":
        outcome = "succeeded"
    elif body.type == "payment_intent.payment_failed":
        outcome = "failed"
    else:
        return {"received": True, "ignored": body.type}
    order = _lock_order_with_account(db, attempt.order_id)
    db.refresh(attempt)
    if order is None:
        raise HTTPException(status_code=404, detail="找不到此訂單")
    _refresh_payment_timeout(db, order)
    # Timeout handling can commit. Reacquire the account and order locks
    # before comparing this attempt with a retry or a cancellation.
    order = _lock_order_with_account(db, attempt.order_id)
    db.refresh(attempt)
    latest = _latest_payment(db, order.id)
    current = (order.payment_status == "pending" and latest is not None and
               latest.id == attempt.id and attempt.status in ("requires_payment", "processing"))
    if outcome == "succeeded" and not current:
        if attempt.status in ("succeeded", "refunded"):
            return {"received": True, "ignored": "order already settled"}
        entry = late_payments.record_late_success(db, order, attempt, obj)
        entry = late_payments.dispatch(db, entry.id)
        return {"received": True, "order_id": order.id,
                "late_payment_refund": {"id": entry.id, "status": entry.status}}
    if not current:
        return {"received": True, "ignored": "payment already settled"}
    _apply_payment_result(db, order, attempt, outcome,
                          obj.get("last_payment_error", {}).get("message", "")
                          if isinstance(obj.get("last_payment_error"), dict) else "")
    return {"received": True, "order_id": order.id, "outcome": outcome}


@router.get("/admin/late-payment-refunds")
def list_late_payment_refunds(
        status: str | None = None, actor: models.User = Depends(require_role("admin", "finance")),
        db: Session = Depends(get_db)):
    query = db.query(LatePaymentRefund)
    if status:
        query = query.filter_by(status=status)
    entries = query.order_by(LatePaymentRefund.id.desc()).limit(100).all()
    return {"items": [{"id": e.id, "order_id": e.order_id, "payment_id": e.payment_id,
                        "amount": float(e.amount), "currency": e.currency,
                        "status": e.status, "last_error": e.last_error,
                        "payment_gateway_ref": getattr(db.get(Payment, e.payment_id), "gateway_ref", None),
                        "refund_gateway_ref": e.gateway_ref,
                        "created_at": e.created_at} for e in entries]}


@router.post("/admin/late-payment-refunds/{entry_id}/retry")
def retry_late_payment_refund(entry_id: int,
        actor: models.User = Depends(require_role("admin", "finance")),
        db: Session = Depends(get_db)):
    entry = late_payments.dispatch(db, entry_id)
    if entry is None:
        raise HTTPException(404, "找不到此筆補償退款")
    return {"id": entry.id, "status": entry.status, "last_error": entry.last_error}


@router.post("/admin/late-payment-refunds/{entry_id}/verify")
def verify_late_payment_refund(entry_id: int, gateway_ref: str = Query(min_length=1, max_length=80),
        actor: models.User = Depends(require_role("admin", "finance")),
        db: Session = Depends(get_db)):
    try:
        entry = late_payments.confirm_existing(db, entry_id, gateway_ref)
    except GatewayError:
        raise HTTPException(502, GATEWAY_FAILURE_MESSAGE)
    except ValueError:
        raise HTTPException(409, "退款資料不相符，請繼續核對")
    if entry is None:
        raise HTTPException(404, "找不到此筆補償退款")
    return {"id": entry.id, "status": entry.status}
