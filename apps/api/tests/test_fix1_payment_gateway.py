"""FIX-1 (P0) tests: payment confirmation must come from the gateway.

Red phase: these tests FAIL against the pre-fix code (client-supplied
outcome is trusted, webhook is unsigned, pi_test_ ids leak to clients).
Green phase: they pass after the fix.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_fix1_payment_gateway.py -v
Uses an in-memory SQLite DB (StaticPool), same pattern as test_packages.py.
No real Stripe keys are used anywhere: the "key" values below are fake
sk_test_/whsec_ placeholders for signature/gating logic only.
"""
import hashlib
import hmac
import json
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import config, legal, models  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models_packages import Payment  # noqa: E402
from app.payments_gateway import GatewayError  # noqa: E402
from app.seed import seed  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine)

FAKE_KEY = "sk_test_fix1_placeholder_not_a_real_key"
FAKE_WEBHOOK_SECRET = "whsec_fix1_placeholder_not_a_real_secret"


# Supply approved-document metadata without publishing or visiting real URLs.
# Registration and session gating still use the real consent ledger.
LEGAL_DOCS = {
    "terms": {"version": "test-v1", "url": "https://legal.example.test/terms"},
    "privacy": {"version": "test-v1", "url": "https://legal.example.test/privacy"},
}
LEGAL_VERSIONS = {
    "accepted_terms_version": "test-v1",
    "accepted_privacy_version": "test-v1",
}


@pytest.fixture(autouse=True)
def configure_legal_documents(monkeypatch):
    monkeypatch.setattr(legal, "documents", lambda: LEGAL_DOCS)


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
        "role": "customer", **LEGAL_VERSIONS}).status_code == 200
    code = client.get("/api/v3/_dev/otp", params={"email": email}).json()["code"]
    r = client.post("/api/v3/new-verify-otp", json={
        "email": email, "code": code, "purpose": "register"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _vendor_headers(client):
    r = client.post("/api/v3/login", json={
        "email": "vendor@coupon-go.example", "password": "Demo123!"})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    if r.json()["consent_required"]:
        accepted = client.post("/api/v3/legal/accept", headers=headers, json=LEGAL_VERSIONS)
        assert accepted.status_code == 200, accepted.text
        headers = {"Authorization": f"Bearer {accepted.json()['access_token']}"}
    return headers


def _create_package(client, name, stock=5):
    vh = _vendor_headers(client)
    r = client.post("/api/v3/packages", json={
        "shop_id": 1, "name": name,
        "items": [{"name": "主菜一份", "qty": 1}],
        "person_count": 2, "original_price": 200, "package_price": 150,
        "valid_from": "2026-11-01T00:00:00",
        "valid_to": "2026-12-31T00:00:00",
        "usage_rules": {"need_booking": True},
        "stock_total": stock, "refund_policy": "anytime",
    }, headers=vh)
    assert r.status_code == 201, r.text
    return r.json()["package"]


def _purchase(client, headers, package_id):
    r = client.post(f"/api/v3/packages/{package_id}/purchase",
                    json={"payment_method": "fps", "client_ref": ""},
                    headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _pending_order(client, email, pkg_name):
    """Register a buyer, create a package, purchase -> pending order."""
    h = _register_and_login(client, email)
    pkg = _create_package(client, pkg_name)
    bought = _purchase(client, h, pkg["id"])
    return h, bought


def _order_detail(client, headers, order_id):
    r = client.get(f"/api/v3/orders/{order_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["order"]


def _db_gateway_ref(order_id):
    db = TestingSession()
    try:
        pay = (db.query(Payment).filter(Payment.order_id == order_id)
               .order_by(Payment.id.desc()).first())
        return pay.gateway_ref if pay else ""
    finally:
        db.close()


def _wallet_count(order_id):
    db = TestingSession()
    try:
        return (db.query(models.WalletItem)
                .filter(models.WalletItem.order_id == order_id).count())
    finally:
        db.close()


def _set_env(monkeypatch, env="dev", key="", secret=""):
    """Patch the app env + Stripe settings as the running code reads them."""
    monkeypatch.setattr(config, "ENV", env, raising=False)
    monkeypatch.setattr(config, "APP_ENV", env, raising=False)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", key, raising=False)
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", secret, raising=False)


def _sign_webhook(secret, payload_bytes, ts=None):
    ts = int(time.time()) if ts is None else ts
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + payload_bytes,
                      hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def _webhook_payload(gateway_ref, event_type="payment_intent.succeeded"):
    return {"type": event_type, "data": {"object": {"id": gateway_ref}}}


def _post_webhook(client, payload, signature=None):
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["Stripe-Signature"] = signature
    return client.post("/api/v3/payments/webhook", content=raw, headers=headers)


# ---------- FIX-1a: confirm-payment must not trust the client ----------
def test_fix1_confirm_payment_client_outcome_forbidden_non_dev(client, monkeypatch):
    """Non-dev, no gateway key: buyer posting outcome=succeeded -> 403,
    order stays pending, no voucher issued."""
    h, bought = _pending_order(client, "fix1-nondev@example.com", "FIX1 非開發套餐")
    order_id = bought["order"]["id"]
    _set_env(monkeypatch, env="prod", key="", secret="")
    r = client.post(f"/api/v3/orders/{order_id}/confirm-payment",
                    json={"outcome": "succeeded"}, headers=h)
    assert r.status_code == 403, r.text
    order = _order_detail(client, h, order_id)
    assert order["payment_status"] == "pending"
    assert order["wallet_item_id"] is None
    assert _wallet_count(order_id) == 0


def test_fix1_confirm_payment_dev_outcome_still_allowed(client, monkeypatch):
    """Dev without a key keeps the local-simulator flow: outcome works."""
    h, bought = _pending_order(client, "fix1-dev@example.com", "FIX1 開發套餐")
    order_id = bought["order"]["id"]
    _set_env(monkeypatch, env="dev", key="", secret="")
    r = client.post(f"/api/v3/orders/{order_id}/confirm-payment",
                    json={"outcome": "succeeded"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["order"]["payment_status"] == "paid"
    assert _wallet_count(order_id) == 1


def test_fix1_confirm_payment_with_key_ignores_client_outcome(client, monkeypatch):
    """Key set: gateway says succeeded while the client claims failed ->
    the gateway wins: order paid, voucher issued."""
    h, bought = _pending_order(client, "fix1-key-ok@example.com", "FIX1 網關套餐")
    order_id = bought["order"]["id"]
    _set_env(monkeypatch, env="prod", key=FAKE_KEY, secret=FAKE_WEBHOOK_SECRET)
    monkeypatch.setattr(
        "app.routers.packages.retrieve_payment_intent",
        lambda ref: {"status": "succeeded", "gateway_ref": ref},
        raising=False)
    r = client.post(f"/api/v3/orders/{order_id}/confirm-payment",
                    json={"outcome": "failed"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["order"]["payment_status"] == "paid"
    assert _wallet_count(order_id) == 1


def test_fix1_confirm_payment_with_key_gateway_pending_stays_pending(client, monkeypatch):
    """Key set: gateway says requires_payment_method -> 400, still pending,
    no voucher, even though the client claimed succeeded."""
    h, bought = _pending_order(client, "fix1-key-pend@example.com", "FIX1 待付套餐")
    order_id = bought["order"]["id"]
    _set_env(monkeypatch, env="prod", key=FAKE_KEY, secret=FAKE_WEBHOOK_SECRET)
    monkeypatch.setattr(
        "app.routers.packages.retrieve_payment_intent",
        lambda ref: {"status": "requires_payment_method", "gateway_ref": ref},
        raising=False)
    r = client.post(f"/api/v3/orders/{order_id}/confirm-payment",
                    json={"outcome": "succeeded"}, headers=h)
    assert r.status_code == 400, r.text
    order = _order_detail(client, h, order_id)
    assert order["payment_status"] == "pending"
    assert _wallet_count(order_id) == 0


def test_fix1_confirm_payment_with_key_gateway_canceled_marks_failed(client, monkeypatch):
    """Key set: gateway says canceled -> order failed, no voucher."""
    h, bought = _pending_order(client, "fix1-key-cancel@example.com", "FIX1 取消套餐")
    order_id = bought["order"]["id"]
    _set_env(monkeypatch, env="prod", key=FAKE_KEY, secret=FAKE_WEBHOOK_SECRET)
    monkeypatch.setattr(
        "app.routers.packages.retrieve_payment_intent",
        lambda ref: {"status": "canceled", "gateway_ref": ref},
        raising=False)
    r = client.post(f"/api/v3/orders/{order_id}/confirm-payment",
                    json={"outcome": "succeeded"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["order"]["payment_status"] == "failed"
    assert _wallet_count(order_id) == 0


def test_fix1_confirm_payment_with_key_gateway_error_is_502(client, monkeypatch):
    """Key set: gateway lookup fails -> 502, order stays pending."""
    h, bought = _pending_order(client, "fix1-key-err@example.com", "FIX1 網關故障套餐")
    order_id = bought["order"]["id"]
    _set_env(monkeypatch, env="prod", key=FAKE_KEY, secret=FAKE_WEBHOOK_SECRET)

    def _boom(ref):
        raise GatewayError("Stripe 测试网关连接失败")

    monkeypatch.setattr("app.routers.packages.retrieve_payment_intent",
                        _boom, raising=False)
    r = client.post(f"/api/v3/orders/{order_id}/confirm-payment",
                    json={"outcome": "succeeded"}, headers=h)
    assert r.status_code == 502, r.text
    assert _order_detail(client, h, order_id)["payment_status"] == "pending"
    assert _wallet_count(order_id) == 0


# ---------- FIX-1b: webhook signature verification + idempotency ----------
def test_fix1_webhook_unsigned_rejected_when_secret_configured(client, monkeypatch):
    h, bought = _pending_order(client, "fix1-wh-nosig@example.com", "FIX1 無簽名套餐")
    order_id = bought["order"]["id"]
    _set_env(monkeypatch, env="prod", key=FAKE_KEY, secret=FAKE_WEBHOOK_SECRET)
    ref = _db_gateway_ref(order_id)
    assert ref.startswith("pi_test_")  # server-side only; see pi-leak test
    r = _post_webhook(client, _webhook_payload(ref))
    assert r.status_code == 400, r.text
    assert _order_detail(client, h, order_id)["payment_status"] == "pending"
    assert _wallet_count(order_id) == 0


def test_fix1_webhook_wrong_signature_rejected(client, monkeypatch):
    h, bought = _pending_order(client, "fix1-wh-badsig@example.com", "FIX1 錯簽名套餐")
    order_id = bought["order"]["id"]
    _set_env(monkeypatch, env="prod", key=FAKE_KEY, secret=FAKE_WEBHOOK_SECRET)
    payload = _webhook_payload(_db_gateway_ref(order_id))
    raw = json.dumps(payload).encode()
    bad_sig = _sign_webhook("whsec_wrong_secret", raw)
    r = _post_webhook(client, payload, signature=bad_sig)
    assert r.status_code == 400, r.text
    assert _order_detail(client, h, order_id)["payment_status"] == "pending"
    assert _wallet_count(order_id) == 0


def test_fix1_webhook_stale_timestamp_rejected(client, monkeypatch):
    h, bought = _pending_order(client, "fix1-wh-stale@example.com", "FIX1 過期簽名套餐")
    order_id = bought["order"]["id"]
    _set_env(monkeypatch, env="prod", key=FAKE_KEY, secret=FAKE_WEBHOOK_SECRET)
    payload = _webhook_payload(_db_gateway_ref(order_id))
    raw = json.dumps(payload).encode()
    stale_sig = _sign_webhook(FAKE_WEBHOOK_SECRET, raw,
                              ts=int(time.time()) - 3600)
    r = _post_webhook(client, payload, signature=stale_sig)
    assert r.status_code == 400, r.text
    assert _order_detail(client, h, order_id)["payment_status"] == "pending"


def test_fix1_webhook_valid_signature_pays_exactly_one_voucher(client, monkeypatch):
    h, bought = _pending_order(client, "fix1-wh-ok@example.com", "FIX1 正確簽名套餐")
    order_id = bought["order"]["id"]
    _set_env(monkeypatch, env="prod", key=FAKE_KEY, secret=FAKE_WEBHOOK_SECRET)
    payload = _webhook_payload(_db_gateway_ref(order_id))
    raw = json.dumps(payload).encode()
    r = _post_webhook(client, payload,
                      signature=_sign_webhook(FAKE_WEBHOOK_SECRET, raw))
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "succeeded"
    order = _order_detail(client, h, order_id)
    assert order["payment_status"] == "paid"
    assert order["wallet_item_id"] is not None
    assert _wallet_count(order_id) == 1


def test_fix1_webhook_replay_is_idempotent(client, monkeypatch):
    """Same signed event delivered twice: both 200, still one voucher."""
    h, bought = _pending_order(client, "fix1-wh-replay@example.com", "FIX1 重放套餐")
    order_id = bought["order"]["id"]
    _set_env(monkeypatch, env="prod", key=FAKE_KEY, secret=FAKE_WEBHOOK_SECRET)
    payload = _webhook_payload(_db_gateway_ref(order_id))
    raw = json.dumps(payload).encode()
    sig = _sign_webhook(FAKE_WEBHOOK_SECRET, raw)
    r1 = _post_webhook(client, payload, signature=sig)
    assert r1.status_code == 200, r1.text
    r2 = _post_webhook(client, payload, signature=sig)
    assert r2.status_code == 200, r2.text
    assert r2.json().get("ignored") == "order already settled"
    assert _order_detail(client, h, order_id)["payment_status"] == "paid"
    assert _wallet_count(order_id) == 1


def test_fix1_webhook_rejected_non_dev_without_secret(client, monkeypatch):
    """Non-dev with no key and no webhook secret: webhook refused outright."""
    h, bought = _pending_order(client, "fix1-wh-prodnosec@example.com", "FIX1 非開發無密鑰套餐")
    order_id = bought["order"]["id"]
    _set_env(monkeypatch, env="prod", key="", secret="")
    r = _post_webhook(client, _webhook_payload(_db_gateway_ref(order_id)))
    assert r.status_code == 400, r.text
    assert _order_detail(client, h, order_id)["payment_status"] == "pending"
    assert _wallet_count(order_id) == 0


# ---------- FIX-1c: simulated pi_test_ ids must not reach the client ----------
def test_fix1_simulated_pi_id_not_exposed_to_client(client, monkeypatch):
    _set_env(monkeypatch, env="dev", key="", secret="")
    h, bought = _pending_order(client, "fix1-noleak@example.com", "FIX1 不洩漏套餐")
    order_id = bought["order"]["id"]
    blob = json.dumps(bought)
    assert "pi_test_" not in blob
    assert bought["payment"]["gateway_ref"] == ""
    assert "pi_test_" not in bought.get("client_secret", "")
    order = _order_detail(client, h, order_id)
    assert "pi_test_" not in json.dumps(order)
    assert order["payments"][0]["gateway_ref"] == ""
    # The id still exists server-side (DB) so the webhook can match it.
    assert _db_gateway_ref(order_id).startswith("pi_test_")


# ---------- FIX-1d: startup check ----------
def test_fix1_startup_check_rejects_non_dev_without_webhook_secret(monkeypatch):
    _set_env(monkeypatch, env="prod", key=FAKE_KEY, secret="")
    with pytest.raises(RuntimeError):
        config.validate_payment_config()
    _set_env(monkeypatch, env="prod", key=FAKE_KEY, secret=FAKE_WEBHOOK_SECRET)
    # P1-2: prod also needs a non-default SECRET_KEY, not just the
    # webhook secret, for the startup gate to pass.
    monkeypatch.setattr(config, "SECRET_KEY", "test-only-non-default-secret",
                        raising=False)
    config.validate_payment_config()  # secret present: starts fine
    _set_env(monkeypatch, env="dev", key="", secret="")
    config.validate_payment_config()  # dev without secret: starts fine
