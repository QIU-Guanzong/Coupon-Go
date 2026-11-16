"""P1-3 tests: bargain refunds go through the payment gateway
(refund_payment, same calibre as package refunds).

* A refund record carries status pending/succeeded/failed + gateway_ref.
* The user-facing note is "退款将在 1–3 个工作日原路退回" — no dev
  wording ("开发版未接支付网关，仅记账" must not appear).
* A failed gateway refund stays failed (retryable) and can be retried
  on the SAME refund row via POST /bargain/refunds/{id}/retry.
"""
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import models  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.payments_gateway import GatewayError  # noqa: E402
from app.routers import bargain as bargain_router  # noqa: E402
from app.seed import seed  # noqa: E402
from app.seed_bargain import seed_bargain_campaigns  # noqa: E402

TEST_DB = os.path.join(tempfile.mkdtemp(prefix="coupon_go_test_p1_bargain_"),
                       "coupon_go_test.db")

engine = create_engine(f"sqlite:///{TEST_DB}", connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine)


@pytest.fixture(scope="module")
def client():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    Base.metadata.create_all(bind=engine)
    db = TestingSession()
    seed(db)
    seed_bargain_campaigns(db)
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


def _login(client, email, password="Demo123!"):
    r = client.post("/api/v3/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_campaign(client, original=200, floor=190, stock=5,
                   helper_limit=10, duration_minutes=1440):
    vh = _login(client, "vendor@coupon-go.example")
    r = client.post("/api/v3/bargain-campaigns", json={
        "shop_id": 1, "name": "P1 測試雙人餐",
        "items": "主菜、飲品", "person_count": 2,
        "original_price": original, "floor_price": floor,
        "stock_total": stock, "helper_limit": helper_limit,
        "duration_minutes": duration_minutes, "campaign_days": 7,
        "lock_stock": True}, headers=vh)
    assert r.status_code == 200, r.text
    return r.json()["campaign"]


def _start(client, headers, campaign_id):
    r = client.post(f"/api/v3/bargain-campaigns/{campaign_id}/start",
                    headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["session"]


def _session(client, session_id):
    return client.get(f"/api/v3/bargain-sessions/{session_id}").json()["session"]


def test_timeout_refund_calls_gateway_and_records_ref(client):
    h = _register_and_login(client, "p1-bgn-alice@example.com")
    camp = _make_campaign(client, original=200, floor=100, stock=5,
                          duration_minutes=0)
    s = _start(client, h, camp["id"])
    s = _session(client, s["id"])
    assert s["status"] == "failed"
    assert len(s["refunds"]) == 1
    refund = s["refunds"][0]
    assert refund["amount"] == 200.0
    assert refund["reason"] == "bargain_failed"
    assert refund["status"] == "succeeded"
    assert refund["gateway_ref"].startswith("re_test_")
    assert refund["note"] == "退款将在 1–3 个工作日原路退回"
    assert "开发" not in refund["note"] and "网关" not in refund["note"]


def test_failed_refund_is_retryable_on_same_row(client, monkeypatch):
    h = _register_and_login(client, "p1-bgn-bob@example.com")
    camp = _make_campaign(client, original=200, floor=100, stock=5,
                          duration_minutes=0)
    s = _start(client, h, camp["id"])

    def _boom(ref, amount, currency="HKD"):
        raise GatewayError("simulated gateway outage")

    monkeypatch.setattr(bargain_router, "refund_payment", _boom)
    s = _session(client, s["id"])
    assert s["status"] == "failed"
    refund = s["refunds"][0]
    assert refund["status"] == "failed"
    assert refund["gateway_ref"] == ""
    refund_id = refund["id"]

    # Retry while the gateway is still down: still failed, same row.
    r = client.post(f"/api/v3/bargain/refunds/{refund_id}/retry", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["refund"]["id"] == refund_id
    assert r.json()["refund"]["status"] == "failed"

    # Gateway recovers: retry the same row succeeds and records the ref.
    monkeypatch.undo()
    r = client.post(f"/api/v3/bargain/refunds/{refund_id}/retry", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["refund"]["id"] == refund_id
    assert r.json()["refund"]["status"] == "succeeded"
    assert r.json()["refund"]["gateway_ref"].startswith("re_test_")

    # A succeeded refund cannot be retried again.
    r = client.post(f"/api/v3/bargain/refunds/{refund_id}/retry", headers=h)
    assert r.status_code == 409, r.text


def test_retry_refund_is_owner_or_admin_only(client):
    h = _register_and_login(client, "p1-bgn-carol@example.com")
    other = _register_and_login(client, "p1-bgn-dave@example.com")
    camp = _make_campaign(client, original=200, floor=100, stock=5,
                          duration_minutes=0)
    s = _start(client, h, camp["id"])
    s = _session(client, s["id"])
    refund_id = s["refunds"][0]["id"]
    r = client.post(f"/api/v3/bargain/refunds/{refund_id}/retry", headers=other)
    assert r.status_code == 403, r.text
    r = client.post(f"/api/v3/bargain/refunds/{refund_id}/retry")
    assert r.status_code == 401, r.text


def test_price_cut_refund_has_gateway_ref(client):
    h = _register_and_login(client, "p1-bgn-erin@example.com")
    helper = _register_and_login(client, "p1-bgn-fay@example.com")
    camp = _make_campaign(client, original=200, floor=190, stock=5,
                          helper_limit=1)
    s = _start(client, h, camp["id"])
    r = client.post(f"/api/v3/bargain-sessions/{s['id']}/help",
                    json={"device_id": "p1-dev-1"}, headers=helper)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/v3/bargain-sessions/{s['id']}/purchase", headers=h)
    assert r.status_code == 200, r.text
    refund = r.json()["refund"]
    assert refund is not None
    assert refund["reason"] == "price_cut"
    assert refund["status"] == "succeeded"
    assert refund["gateway_ref"].startswith("re_test_")
    assert refund["note"] == "退款将在 1–3 个工作日原路退回"
