"""P1-4 tests: withdrawal requires a registered name.

The payout account name must match the registered name (ShopBack HK
rule). Previously the check was skipped entirely when the user had
never filled in a registered name; now a blank registered name
rejects the withdrawal instead of skipping the check.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import models  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


def _register_and_login(client, email, name="Test"):
    body = {"email": email, "password": "Demo123!", "role": "customer"}
    if name is not None:
        body["name"] = name
    r = client.post("/api/v3/new-register", json=body)
    assert r.status_code == 200, r.text
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


def _make_coupon(shop_id=1, discount_price=1200):
    db = TestingSession()
    try:
        vendor = db.query(models.User).filter(
            models.User.email == "vendor@coupon-go.example").first()
        shop = db.query(models.Shop).filter(models.Shop.id == shop_id).first()
        c = models.Coupon(
            shop_id=shop.id, vendor_id=vendor.id, coupon_type="Cash",
            coupon_name="測試券", category_id=shop.category_id,
            discount_price=discount_price,
            valid_from=_now() - timedelta(days=1),
            valid_to=_now() + timedelta(days=30),
            total=100, remaining=100, status="active")
        db.add(c)
        db.commit()
        return c.id
    finally:
        db.close()


def _redeem(client, h, vh, coupon_id):
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": coupon_id}, headers=h)
    assert r.status_code == 200, r.text
    wid = r.json()["wallet_item_id"]
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=h)
    assert qr.status_code == 200, qr.text
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 200, r.text
    return r.json()


def _earn_withdrawable(client, h, vh):
    """Two dine-in redemptions at shop 1: HK$60 unlocked, withdrawable."""
    _redeem(client, h, vh, _make_coupon(shop_id=1, discount_price=1200))
    _redeem(client, h, vh, _make_coupon(shop_id=1, discount_price=1200))
    bal = client.get("/api/v3/cashback", headers=h).json()
    assert bal["withdrawable_balance"] == 60.0, bal


def _withdraw(client, h, amount, account_name):
    return client.post("/api/v3/cashback/withdraw", json={
        "amount": amount, "method": "fps",
        "account_name": account_name}, headers=h)


def test_withdraw_rejected_when_registered_name_blank(client):
    # Registered with no name at all (RegisterIn.name defaults to "").
    h = _register_and_login(client, "p1-wd-noname@example.com", name=None)
    vh = _vendor_headers(client)
    _earn_withdrawable(client, h, vh)
    r = _withdraw(client, h, 50, "Test")
    assert r.status_code == 400, r.text
    assert "註冊姓名" in r.json()["detail"]


def test_withdraw_rejected_when_account_name_mismatches(client):
    h = _register_and_login(client, "p1-wd-mismatch@example.com", name="Test")
    vh = _vendor_headers(client)
    _earn_withdrawable(client, h, vh)
    r = _withdraw(client, h, 50, "Someone Else")
    assert r.status_code == 400, r.text
    assert "註冊姓名一致" in r.json()["detail"]


def test_withdraw_ok_when_account_name_matches_registered(client):
    h = _register_and_login(client, "p1-wd-ok@example.com", name="Test")
    vh = _vendor_headers(client)
    _earn_withdrawable(client, h, vh)
    r = _withdraw(client, h, 50, "Test")
    assert r.status_code == 200, r.text
    assert r.json()["withdrawal"]["status"] == "pending"
