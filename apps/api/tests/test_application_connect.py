"""Application-to-shop Connect flow; every provider call is a stub."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from tests import test_p1d_merchant_onboarding as base
from app import application_connect, config, connect_gateway, models
from app.models_connect import ApplicationConnectAccount, ConnectAccount, ShopConnectOperation
from app.models_line_b import MerchantApplication, MerchantContractSignature, MerchantContractVersion
from app.payments_gateway import GatewayError
from app.timeutil import utcnow

client = base.client


@pytest.fixture
def provider(monkeypatch):
    created = {}
    calls = []

    def create(email, country="HK", *, idempotency_key):
        calls.append(idempotency_key)
        created.setdefault(idempotency_key, {"account_id": "acct_" + idempotency_key,
                                              "simulated": False})
        return created[idempotency_key]

    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED_RAW", "1")
    monkeypatch.setattr(connect_gateway, "create_account", create)
    monkeypatch.setattr(connect_gateway, "create_account_link",
                        lambda account, refresh, ret: {"url": "https://example.test/onboard"})
    monkeypatch.setattr(connect_gateway, "retrieve_account",
                        lambda account: {"account_id": account, "charges_enabled": True, "payouts_enabled": True,
                                         "details_submitted": True})
    monkeypatch.setattr(connect_gateway, "set_manual_payouts", lambda account: {"manual": True})
    return created, calls


def applicant(client, suffix):
    h = base._register_and_login(client, f"connect-{suffix}@example.com")
    r = base._submit(client, h)
    assert r.status_code == 201, r.text
    application_id = r.json()["application"]["id"]
    db = base.TestingSession()
    app = db.get(MerchantApplication, application_id)
    contract = db.query(MerchantContractVersion).filter_by(is_current=True).one()
    db.add(MerchantContractSignature(application_id=app.id, user_id=app.user_id,
                                     contract_version_id=contract.id))
    db.commit()
    db.close()
    return h, application_id


def start(client, headers, application_id):
    return client.post(f"/api/v3/merchant-applications/{application_id}/connect/onboarding",
                       headers=headers, json={"return_url": "https://example.test/return",
                                             "refresh_url": "https://example.test/refresh"})


def test_applicant_creates_account_and_approval_binds_one_shop(client, provider):
    h, app_id = applicant(client, "complete")
    assert start(client, h, app_id).status_code == 200
    assert start(client, h, app_id).status_code == 200
    sync = client.post(f"/api/v3/merchant-applications/{app_id}/connect/sync", headers=h)
    assert sync.status_code == 200, sync.text
    approved = base._approve(client, base._admin_headers(client), app_id)
    assert approved.status_code == 200, approved.text
    db = base.TestingSession()
    app = db.get(MerchantApplication, app_id)
    row = db.query(ApplicationConnectAccount).filter_by(application_id=app_id).one()
    shop = db.get(models.Shop, app.approved_shop_id)
    account = db.query(ConnectAccount).filter_by(shop_id=shop.id).one()
    assert shop.vendor_id == app.user_id
    assert account.stripe_account_id == row.stripe_account_id
    assert db.get(models.User, app.user_id).role == "vendor"
    assert len(provider[0]) == 1
    db.close()
    assert base._approve(client, base._admin_headers(client), app_id).status_code == 400


def test_unknown_provider_result_reuses_original_operation(client, provider, monkeypatch):
    h, app_id = applicant(client, "unknown")
    real_create = connect_gateway.create_account
    def ambiguous(email, **kwargs):
        real_create(email, **kwargs)
        raise GatewayError("connection dropped")
    monkeypatch.setattr(connect_gateway, "create_account", ambiguous)
    assert start(client, h, app_id).status_code == 502
    monkeypatch.setattr(connect_gateway, "create_account", real_create)
    assert start(client, h, app_id).status_code == 200
    assert len(provider[0]) == 1 and len(set(provider[1])) == 1


def test_stale_unknown_request_and_other_user_are_blocked(client, provider):
    h, app_id = applicant(client, "stale")
    other = base._register_and_login(client, "connect-other@example.com")
    assert start(client, other, app_id).status_code == 404
    db = base.TestingSession()
    db.add(ApplicationConnectAccount(application_id=app_id, operation_key="unknown-old", contact_email="test@example.com",
                                     state="creating", started_at=utcnow() - timedelta(hours=1)))
    db.commit()
    db.close()
    assert start(client, h, app_id).status_code == 409
    assert not provider[1]


def test_existing_provider_binding_does_not_create_second_shop(client, provider):
    h, app_id = applicant(client, "conflict")
    assert start(client, h, app_id).status_code == 200
    db = base.TestingSession()
    app = db.get(MerchantApplication, app_id)
    row = db.query(ApplicationConnectAccount).filter_by(application_id=app_id).one()
    shop = models.Shop(vendor_id=app.user_id, name="Historical shop")
    db.add(shop)
    db.flush()
    db.add(ConnectAccount(shop_id=shop.id, stripe_account_id=row.stripe_account_id))
    db.commit()
    db.close()
    r = base._approve(client, base._admin_headers(client), app_id)
    assert r.status_code == 409, r.text
    db = base.TestingSession()
    current = db.get(MerchantApplication, app_id)
    assert current.approved_shop_id is None
    assert db.query(models.Shop).filter_by(vendor_id=current.user_id).count() == 1
    db.close()


def test_simulated_provider_status_cannot_become_formal_approval(client, provider, monkeypatch):
    h, app_id = applicant(client, "simulated")
    assert start(client, h, app_id).status_code == 200
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_placeholder")
    monkeypatch.setattr(connect_gateway, "retrieve_account", lambda account: {
        "account_id": account, "charges_enabled": True, "payouts_enabled": True,
        "details_submitted": True, "simulated": True})
    db = base.TestingSession()
    with pytest.raises(Exception) as exc:
        application_connect.approve_binding(db, db.get(MerchantApplication, app_id))
    assert exc.value.status_code == 409
    db.rollback()
    assert db.get(MerchantApplication, app_id).approved_shop_id is None
    db.close()


def test_existing_shop_opening_reuses_operation_after_unknown_result(client, provider, monkeypatch):
    h, app_id = applicant(client, "oldshop")
    db = base.TestingSession()
    app = db.get(MerchantApplication, app_id)
    shop = models.Shop(vendor_id=app.user_id, name="Old shop")
    db.add(shop)
    db.commit()
    shop_id = shop.id
    real_create = connect_gateway.create_account
    def ambiguous(email, **kwargs):
        real_create(email, **kwargs)
        raise GatewayError("response lost")
    monkeypatch.setattr(connect_gateway, "create_account", ambiguous)
    with pytest.raises(Exception) as exc:
        application_connect.start_existing_shop(db, shop_id, "merchant@example.com")
    assert exc.value.status_code == 502
    monkeypatch.setattr(connect_gateway, "create_account", real_create)
    account = application_connect.start_existing_shop(db, shop_id, "merchant@example.com")
    assert application_connect.start_existing_shop(db, shop_id, "merchant@example.com").id == account.id
    assert db.query(ShopConnectOperation).filter_by(shop_id=shop_id).count() == 1
    assert len(provider[0]) == 1 and len(set(provider[1])) == 1
    db.close()


@pytest.mark.skipif(not base.PG_URL, reason="requires disposable PostgreSQL")
def test_concurrent_onboarding_reuses_single_provider_operation(client, provider):
    h, app_id = applicant(client, "concurrent")
    barrier = Barrier(2)
    def request():
        barrier.wait(timeout=5)
        return start(client, h, app_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: request(), range(2)))
    assert [r.status_code for r in results] == [200, 200]
    assert len(provider[0]) == 1 and len(provider[1]) == 1
    db = base.TestingSession()
    assert db.query(ApplicationConnectAccount).filter_by(application_id=app_id).count() == 1
    db.close()
