"""Gap 5 tests: prepare_scan returns customer phone last-4 + coupon validity.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_gap5_prepare_scan_fields.py -v
Uses its own throwaway SQLite file so parallel gap workers do not
delete each other's test DB.
"""
import os
import sys

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
from app.seed import seed  # noqa: E402

TEST_DB = "/tmp/coupon_go_test_gap5.db"

engine = create_engine(f"sqlite:///{TEST_DB}",
                       connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine)


@pytest.fixture(scope="module")
def client():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
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


def _db():
    return TestingSession()


def _register_and_login(client, email, phone=None):
    body = {"email": email, "password": "Demo123!", "name": "Test",
            "role": "customer"}
    if phone is not None:
        body["phone"] = phone
    assert client.post("/api/v3/new-register", json=body).status_code == 200
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


def _claim_and_qr(client, headers, coupon_id):
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": coupon_id}, headers=headers)
    assert r.status_code == 200, r.text
    wid = r.json()["wallet_item_id"]
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=headers)
    assert qr.status_code == 200, qr.text
    return wid, qr.json()["code"]


def _prepare(client, vh, code):
    r = client.post("/api/v3/new-scan-coupon/prepare",
                    json={"code": code}, headers=vh)
    assert r.status_code == 200, r.text
    return r.json()


def test_prepare_returns_phone_last4_and_validity(client):
    h = _register_and_login(client, "gap5-with-phone@example.com",
                            phone="91234567")
    vh = _vendor_headers(client)
    _, code = _claim_and_qr(client, h, 2)
    body = _prepare(client, vh, code)
    assert body["can_redeem"] is True
    # Phone last-4 (task name + the key the merchant page already reads).
    assert body["phone_last4"] == "4567"
    assert body["customer_phone_last4"] == "4567"
    # Coupon validity comes from Coupon.valid_to.
    db = _db()
    coupon = db.query(models.Coupon).filter(models.Coupon.id == 2).first()
    expected = coupon.valid_to.isoformat()
    db.close()
    assert body["valid_until"] == expected
    assert body["expires_at"] == expected
    assert body["valid_to"] == expected


def test_prepare_phone_last4_null_without_phone(client):
    h = _register_and_login(client, "gap5-no-phone@example.com")
    vh = _vendor_headers(client)
    _, code = _claim_and_qr(client, h, 2)
    body = _prepare(client, vh, code)
    assert body["can_redeem"] is True
    assert body["phone_last4"] is None
    assert body["customer_phone_last4"] is None
    # Validity is still returned: it belongs to the coupon, not the user.
    assert body["valid_until"]
    assert body["expires_at"] == body["valid_until"]
