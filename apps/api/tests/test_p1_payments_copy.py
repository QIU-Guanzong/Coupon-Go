"""P1-5 tests: user-visible copy cleanup (payments side).

* The package purchase response carries no test_mode field.
* Gateway errors are never passed through to the user: purchase /
  confirm / refund all return the single generic message
  "支付失败，请稍后重试" while the gateway detail stays out of the
  response body.
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import config  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.payments_gateway import GatewayError  # noqa: E402
from app.routers import packages as packages_router  # noqa: E402
from app.seed import seed  # noqa: E402

GENERIC = "支付失败，请稍后重试"

engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine)


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(bind=engine)
    db = TestingSession()
    seed(db)
    db.close()

    def _get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _register_and_login(client, email):
    assert client.post("/api/v3/new-register", json={
        "email": email, "password": "Demo123!", "name": "Test",
        "role": "customer"}).status_code == 200
    code = client.get("/api/v3/_dev/otp", params={"email": email}).json()["code"]
    r = client.post("/api/v3/new-verify-otp", json={
        "email": email, "code": code, "purpose": "register"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _vendor_headers(client):
    r = client.post("/api/v3/login", json={
        "email": "vendor@coupon-go.example", "password": "Demo123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _create_package(client, name="P1 套餐", stock=5, price=150, original=200):
    vh = _vendor_headers(client)
    body = {
        "shop_id": 1, "name": name,
        "items": [{"name": "主菜一份", "qty": 1}],
        "person_count": 2, "original_price": original, "package_price": price,
        "valid_from": "2026-11-01T00:00:00",
        "valid_to": "2026-12-31T00:00:00",
        "usage_rules": {"need_booking": True},
        "stock_total": stock, "refund_policy": "anytime",
    }
    r = client.post("/api/v3/packages", json=body, headers=vh)
    assert r.status_code == 201, r.text
    return r.json()["package"]


def _purchase(client, headers, package_id, method="fps", client_ref=""):
    return client.post(f"/api/v3/packages/{package_id}/purchase",
                       json={"payment_method": method, "client_ref": client_ref},
                       headers=headers)


def test_purchase_response_has_no_test_mode(client):
    h = _register_and_login(client, "p1-copy-buyer@example.com")
    pkg = _create_package(client, name="P1 無 test_mode 套餐")
    r = _purchase(client, h, pkg["id"], client_ref="p1-no-test-mode")
    assert r.status_code == 200, r.text
    assert "test_mode" not in r.json()
    assert r.json()["order"]["payment_status"] == "pending"


def test_gateway_error_not_leaked_on_purchase(client, monkeypatch):
    h = _register_and_login(client, "p1-copy-buyer2@example.com")
    pkg = _create_package(client, name="P1 網關錯套餐")

    def _boom(order_id, amount, method, currency="HKD"):
        raise GatewayError("Stripe 测试网关拒绝：{secret-stripe-detail}")

    monkeypatch.setattr(packages_router, "create_payment_intent", _boom)
    r = _purchase(client, h, pkg["id"])
    assert r.status_code == 502, r.text
    assert r.json()["detail"] == GENERIC
    assert "secret-stripe-detail" not in r.text
    assert "Stripe" not in r.json()["detail"]


def test_gateway_error_not_leaked_on_confirm(client, monkeypatch):
    h = _register_and_login(client, "p1-copy-buyer3@example.com")
    pkg = _create_package(client, name="P1 確認網關錯套餐")
    bought = _purchase(client, h, pkg["id"], client_ref="p1-confirm-leak")
    assert bought.status_code == 200, bought.text
    order_id = bought.json()["order"]["id"]

    def _boom(ref):
        raise GatewayError("Stripe 测试网关拒绝：{secret-stripe-detail}")

    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setattr(packages_router, "retrieve_payment_intent", _boom)
    r = client.post(f"/api/v3/orders/{order_id}/confirm-payment",
                    json={"outcome": "succeeded"}, headers=h)
    assert r.status_code == 502, r.text
    assert r.json()["detail"] == GENERIC
    assert "secret-stripe-detail" not in r.text


def test_gateway_error_not_leaked_on_refund(client, monkeypatch):
    h = _register_and_login(client, "p1-copy-buyer4@example.com")
    pkg = _create_package(client, name="P1 退款網關錯套餐")
    bought = _purchase(client, h, pkg["id"], client_ref="p1-refund-leak")
    assert bought.status_code == 200, bought.text
    order_id = bought.json()["order"]["id"]
    r = client.post(f"/api/v3/orders/{order_id}/confirm-payment",
                    json={"outcome": "succeeded"}, headers=h)
    assert r.status_code == 200, r.text

    def _boom(ref, amount, currency="HKD", **kwargs):
        raise GatewayError("Stripe 测试网关拒绝：{secret-stripe-detail}")

    monkeypatch.setattr(packages_router, "refund_payment", _boom)
    r = client.post(f"/api/v3/orders/{order_id}/refund",
                    json={"reason": "用戶申請退款"}, headers=h)
    assert r.status_code == 502, r.text
    assert r.json()["detail"] == GENERIC
    assert "secret-stripe-detail" not in r.text
