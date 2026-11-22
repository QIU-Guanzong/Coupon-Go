"""Connect onboarding bound to an application before the shop exists."""
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException

from . import config, connect_gateway, models
from .models_connect import ApplicationConnectAccount, ConnectAccount, ShopConnectOperation
from .models_line_b import MerchantApplication
from .payments_gateway import GatewayError
from .timeutil import utcnow


def _application(db, application_id, user_id=None):
    app = (db.query(MerchantApplication).filter_by(id=application_id)
           .with_for_update().populate_existing().first())
    if app is None or (user_id is not None and app.user_id != user_id):
        raise HTTPException(404, "找不到此入駐申請")
    if app.status != "pending":
        raise HTTPException(409, "申請已審核")
    return app


def _enabled():
    if not config.connect_enabled()["enabled"]:
        raise HTTPException(503, "Stripe Connect 尚未配置")


def start(db, application_id, user, return_url, refresh_url):
    _enabled()
    if not return_url or not refresh_url:
        raise HTTPException(400, "缺少開戶返回地址")
    app = _application(db, application_id, user.id)
    row = db.query(ApplicationConnectAccount).filter_by(application_id=app.id).first()
    if row is None:
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
        row.stripe_account_id = account_id
        row.simulated = bool(created.get("simulated"))
        row.state = "incomplete"
        db.commit()
    try:
        link = connect_gateway.create_account_link(row.stripe_account_id, refresh_url, return_url)
    except GatewayError:
        raise HTTPException(502, "無法取得開戶連結，請稍後重試")
    return {"application_id": application_id, "onboarding_url": link.get("url"),
            "simulated": bool(row.simulated or link.get("simulated"))}


def start_existing_shop(db, shop_id, email):
    """Create at most one provider account for a pre-existing shop."""
    _enabled()
    shop = (db.query(models.Shop).filter_by(id=shop_id).with_for_update()
            .populate_existing().one())
    account = db.query(ConnectAccount).filter_by(shop_id=shop.id).first()
    if account:
        return account
    op = db.query(ShopConnectOperation).filter_by(shop_id=shop.id).first()
    if op is None:
        op = ShopConnectOperation(shop_id=shop.id, operation_key="shop-" + uuid4().hex,
                                  contact_email=email, started_at=utcnow())
        db.add(op)
        db.commit()
    # The shop lock protects both the remote call and local account binding.
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
    if db.query(ConnectAccount).filter_by(stripe_account_id=account_id).first():
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
            if payout_info.get("simulated"):
                row.simulated = True
            manual = True
    except GatewayError:
        raise HTTPException(502, "無法核實開戶狀態，請稍後重試")
    row.charges_enabled = bool(info.get("charges_enabled"))
    row.payouts_enabled = bool(info.get("payouts_enabled"))
    row.details_submitted = bool(info.get("details_submitted"))
    row.simulated = row.simulated or bool(info.get("simulated"))
    row.manual_payouts = manual
    row.verified_at = utcnow()
    row.state = "active" if row.charges_enabled and row.payouts_enabled else "incomplete"
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
    if not (row.charges_enabled and row.payouts_enabled and row.manual_payouts):
        raise HTTPException(400, "尚未完成 Stripe 開戶")
    matches = (db.query(ConnectAccount, models.Shop)
               .join(models.Shop, models.Shop.id == ConnectAccount.shop_id)
               .filter(ConnectAccount.stripe_account_id == row.stripe_account_id).all())
    if matches:
        # Existing bindings must be reconciled explicitly; never guess ownership.
        raise HTTPException(409, "此開戶帳號已有店鋪綁定，請聯絡平台核對")
    if app.approved_shop_id:
        raise HTTPException(409, "申請已關聯店鋪，請聯絡平台核對")
    shop = models.Shop(vendor_id=app.user_id, name=app.shop_name,
                       address=app.address or "", phone=app.phone or "", is_approved=True)
    db.add(shop)
    db.flush()
    db.add(ConnectAccount(shop_id=shop.id, stripe_account_id=row.stripe_account_id,
                          charges_enabled=row.charges_enabled,
                          payouts_enabled=row.payouts_enabled,
                          details_submitted=row.details_submitted,
                          manual_payouts=row.manual_payouts,
                          onboarding_status=row.state))
    app.approved_shop_id = shop.id
    db.flush()
    return shop
