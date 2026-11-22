"""Connect onboarding bound to an application before the shop exists."""
from datetime import timedelta
from hashlib import sha256
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from . import config, connect_gateway, models
from .models_connect import ApplicationConnectAccount, ConnectAccount, ShopConnectOperation
from .models_line_b import MerchantApplication
from .payments_gateway import GatewayError
from .timeutil import utcnow


def _application(db, application_id, user_id=None):
    from .cashback_service import lock_account
    owner_id = db.query(MerchantApplication.user_id).filter_by(id=application_id).scalar()
    if owner_id is None or (user_id is not None and owner_id != user_id):
        raise HTTPException(404, "找不到此入駐申請")
    lock_account(db, owner_id)
    if db.get_bind().dialect.name == "sqlite":
        db.query(MerchantApplication).filter_by(id=application_id).update(
            {"status": MerchantApplication.status}, synchronize_session=False)
    app = (db.query(MerchantApplication).filter_by(id=application_id)
           .with_for_update().populate_existing().first())
    if app is None or app.user_id != owner_id:
        raise HTTPException(404, "找不到此入駐申請")
    if app.status != "pending":
        raise HTTPException(409, "申請已審核")
    return app


def _matching_shops(db, app):
    shops = db.query(models.Shop).filter_by(vendor_id=app.user_id, name=app.shop_name).all()
    address = (app.address or "").strip()
    # Without an address the namesake may be the same business: do not
    # silently create a second provider account while its identity is unclear.
    return [shop for shop in shops if not address or not (shop.address or "").strip()
            or shop.address.strip() == address]


def _shop_operation_exists(db, shops):
    ids = [shop.id for shop in shops]
    if not ids:
        return False
    return (db.query(ConnectAccount.id).filter(ConnectAccount.shop_id.in_(ids)).first() is not None
            or db.query(ShopConnectOperation.id).filter(ShopConnectOperation.shop_id.in_(ids)).first() is not None)


def _application_operation_exists(db, shop):
    pending = db.query(MerchantApplication).filter_by(user_id=shop.vendor_id, status="pending", shop_name=shop.name).all()
    ids = [app.id for app in pending if any(candidate.id == shop.id for candidate in _matching_shops(db, app))]
    return bool(ids and db.query(ApplicationConnectAccount.id)
                .filter(ApplicationConnectAccount.application_id.in_(ids)).first())


def _enabled():
    from .connect_service import connect_configured
    if not connect_configured():
        raise HTTPException(503, "Stripe Connect 尚未配置")


def _lock_provider_id(db, account_id):
    # A provider ID must not be bound concurrently through an application
    # and an older shop's independent onboarding entry point.
    if db.get_bind().dialect.name == "postgresql":
        key = int.from_bytes(sha256(account_id.encode()).digest()[:8], "big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def start(db, application_id, user, return_url, refresh_url):
    _enabled()
    if not return_url or not refresh_url:
        raise HTTPException(400, "缺少開戶返回地址")
    app = _application(db, application_id, user.id)
    row = db.query(ApplicationConnectAccount).filter_by(application_id=app.id).first()
    if row is None:
        if _shop_operation_exists(db, _matching_shops(db, app)):
            raise HTTPException(409, "同名店舖已有開戶記錄，請聯絡平台核對")
        # Persist an operation identity before making any provider request.
        row = ApplicationConnectAccount(application_id=app.id,
                                        operation_key="application-" + uuid4().hex,
                                        contact_email=user.email,
                                        state="creating", started_at=utcnow())
        db.add(row)
        db.commit()
    # Serialize duplicate requests, including the remote creation attempt.
    _application(db, application_id, user.id)
    row = (db.query(ApplicationConnectAccount).filter_by(application_id=application_id)
           .with_for_update().populate_existing().one())
    if not row.stripe_account_id:
        if utcnow() - row.started_at > timedelta(minutes=10):
            raise HTTPException(409, "开户结果待核对，请联系平台处理")
        try:
            created = connect_gateway.create_account(row.contact_email, idempotency_key=row.operation_key)
        except GatewayError:
            # Do not replace the operation key when the provider result is unknown.
            db.rollback()
            raise HTTPException(502, "開戶結果未確認，請稍後重試")
        account_id = created.get("account_id")
        if not account_id:
            db.rollback()
            raise HTTPException(502, "開戶結果未確認，請稍後重試")
        _lock_provider_id(db, account_id)
        conflicting_application = (db.query(ApplicationConnectAccount.id)
                                   .filter(ApplicationConnectAccount.stripe_account_id == account_id,
                                           ApplicationConnectAccount.id != row.id).first())
        conflicting_shop = db.query(ConnectAccount.id).filter_by(stripe_account_id=account_id).first()
        if conflicting_application or conflicting_shop:
            db.rollback()
            raise HTTPException(409, "此開戶帳號已有綁定，請聯絡平台核對")
        row.stripe_account_id = account_id
        row.simulated = bool(created.get("simulated"))
        row.state = "incomplete"
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "此開戶帳號已有綁定，請聯絡平台核對")
    try:
        link = connect_gateway.create_account_link(row.stripe_account_id, refresh_url, return_url)
    except GatewayError:
        raise HTTPException(502, "無法取得開戶連結，請稍後重試")
    return {"application_id": application_id, "onboarding_url": link.get("url"),
            "simulated": bool(row.simulated or link.get("simulated"))}


def start_existing_shop(db, shop_id, email):
    """Create at most one provider account for a pre-existing shop."""
    _enabled()
    from .cashback_service import lock_account
    owner_id = db.query(models.Shop.vendor_id).filter_by(id=shop_id).scalar()
    if owner_id is None:
        raise HTTPException(404, "找不到商店")
    lock_account(db, owner_id)
    if db.get_bind().dialect.name == "sqlite":
        db.query(models.Shop).filter_by(id=shop_id).update(
            {"name": models.Shop.name}, synchronize_session=False)
    shop = (db.query(models.Shop).filter_by(id=shop_id).with_for_update()
            .populate_existing().one())
    account = db.query(ConnectAccount).filter_by(shop_id=shop.id).first()
    if account:
        return account
    op = db.query(ShopConnectOperation).filter_by(shop_id=shop.id).first()
    if op is None:
        if _application_operation_exists(db, shop):
            raise HTTPException(409, "入駐申請已有開戶記錄，請使用原開戶流程")
        op = ShopConnectOperation(shop_id=shop.id, operation_key="shop-" + uuid4().hex,
                                  contact_email=email, started_at=utcnow())
        db.add(op)
        db.commit()
    # The shop lock protects both the remote call and local account binding.
    lock_account(db, owner_id)
    if db.get_bind().dialect.name == "sqlite":
        db.query(models.Shop).filter_by(id=shop_id).update(
            {"name": models.Shop.name}, synchronize_session=False)
    db.query(models.Shop).filter_by(id=shop_id).with_for_update().populate_existing().one()
    account = db.query(ConnectAccount).filter_by(shop_id=shop_id).first()
    if account:
        return account
    op = db.query(ShopConnectOperation).filter_by(shop_id=shop_id).one()
    if utcnow() - op.started_at > timedelta(minutes=10):
        raise HTTPException(409, "开户结果待核对，请联系平台处理")
    try:
        created = connect_gateway.create_account(op.contact_email, idempotency_key=op.operation_key)
    except GatewayError:
        db.rollback()
        raise HTTPException(502, "開戶結果未確認，請稍後重試")
    account_id = created.get("account_id")
    if not account_id:
        db.rollback()
        raise HTTPException(502, "開戶結果未確認，請稍後重試")
    _lock_provider_id(db, account_id)
    if (db.query(ConnectAccount).filter_by(stripe_account_id=account_id).first()
            or db.query(ApplicationConnectAccount).filter_by(stripe_account_id=account_id).first()):
        raise HTTPException(409, "此開戶帳號已有店鋪綁定，請聯絡平台核對")
    account = ConnectAccount(shop_id=shop_id, stripe_account_id=account_id,
                             charges_enabled=bool(created.get("charges_enabled")),
                             payouts_enabled=bool(created.get("payouts_enabled")),
                             details_submitted=bool(created.get("details_submitted")),
                             onboarding_status="pending")
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def sync(db, application_id, user_id=None):
    _enabled()
    app = _application(db, application_id, user_id)
    row = (db.query(ApplicationConnectAccount).filter_by(application_id=app.id)
           .with_for_update().populate_existing().first())
    if row is None or not row.stripe_account_id:
        raise HTTPException(409, "尚未開始開戶")
    try:
        info = connect_gateway.retrieve_account(row.stripe_account_id)
        if info.get("account_id") != row.stripe_account_id:
            raise GatewayError("provider returned a different account")
        manual = row.manual_payouts
        if info.get("payouts_enabled") and not manual:
            payout_info = connect_gateway.set_manual_payouts(row.stripe_account_id)
            if not payout_info.get("manual"):
                raise GatewayError("manual payout schedule not confirmed")
            if payout_info.get("simulated"):
                row.simulated = True
            manual = True
    except GatewayError:
        raise HTTPException(502, "無法核實開戶狀態，請稍後重試")
    row.charges_enabled = bool(info.get("charges_enabled"))
    row.payouts_enabled = bool(info.get("payouts_enabled"))
    row.transfers_enabled = bool(info.get("transfers_enabled"))
    row.details_submitted = bool(info.get("details_submitted"))
    row.simulated = row.simulated or bool(info.get("simulated"))
    row.manual_payouts = manual
    row.verified_at = utcnow()
    row.state = "active" if row.charges_enabled and row.payouts_enabled and row.transfers_enabled else "incomplete"
    # The legacy application's fields are projections, never verification evidence.
    app.stripe_account_id = row.stripe_account_id
    app.stripe_charges_enabled = row.charges_enabled
    app.stripe_payouts_enabled = row.payouts_enabled
    app.stripe_onboarding_updated_at = row.verified_at
    db.flush()
    return row


def approve_binding(db, app):
    row = sync(db, app.id, app.user_id)
    if config.APP_ENV != "dev" and (row.simulated or not config.STRIPE_SECRET_KEY
                                     or config.STRIPE_SECRET_KEY.startswith("sk_test_")):
        raise HTTPException(409, "測試或模擬開戶不能用於正式入駐")
    if not (row.charges_enabled and row.payouts_enabled and row.transfers_enabled and row.manual_payouts):
        raise HTTPException(400, "尚未完成 Stripe 開戶")
    # Caller already holds user -> application locks. Use exactly the same
    # shop resolution as simulated approval and serialize against legacy
    # shop onboarding before checking or attaching the Connect account.
    from .merchant_shop import bind_approved_shop
    from .settlement_ledger import lock_shop
    shop = bind_approved_shop(db, app)
    lock_shop(db, shop.id)
    _lock_provider_id(db, row.stripe_account_id)
    matching = db.query(ConnectAccount).filter_by(stripe_account_id=row.stripe_account_id).all()
    if any(account.shop_id != shop.id for account in matching):
        raise HTTPException(409, "此開戶帳號已有其他店舖綁定，請聯絡平台核對")
    account = db.query(ConnectAccount).filter_by(shop_id=shop.id).first()
    if account and account.stripe_account_id != row.stripe_account_id:
        raise HTTPException(409, "此店舖已有其他開戶帳號，請聯絡平台核對")
    if account is None:
        account = ConnectAccount(shop_id=shop.id, stripe_account_id=row.stripe_account_id)
        db.add(account)
    account.charges_enabled = row.charges_enabled
    account.payouts_enabled = row.payouts_enabled
    account.transfers_enabled = row.transfers_enabled
    account.details_submitted = row.details_submitted
    account.manual_payouts = row.manual_payouts
    account.onboarding_status = row.state
    db.flush()
    return shop
