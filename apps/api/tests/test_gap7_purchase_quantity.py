"""Gap 7 tests: POST /packages/{id}/purchase supports quantity.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_gap7_purchase_quantity.py -v
Uses an in-memory SQLite DB (StaticPool), same pattern as
tests/test_packages.py.
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

from app import models  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

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



def _admin_headers(client):
    r = client.post("/api/v3/login", json={
        "email": "admin@coupon-go.example", "password": "Demo123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _create_package(client, name, stock=5, price=150, original=200):
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
    pkg = r.json()["package"]
    # §3.2: packages enter review on creation; approve before use.
    ah = _admin_headers(client)
    r = client.post(f"/api/v3/admin/packages/{pkg['id']}/approve",
                    params={"approve": True}, headers=ah)
    assert r.status_code == 200, r.text
    r = client.get("/api/v3/package-details", params={"package_id": pkg["id"]})
    assert r.status_code == 200, r.text
    return r.json()["package"]


def _stock_left(client, package_id):
    r = client.get("/api/v3/package-details", params={"package_id": package_id})
    assert r.status_code == 200, r.text
    return r.json()["package"]["stock_left"]


def _purchase_raw(client, headers, package_id, body):
    return client.post(f"/api/v3/packages/{package_id}/purchase",
                       json=body, headers=headers)


def test_purchase_quantity_two_doubles_total_and_deducts_two(client):
    h = _register_and_login(client, "gap7-qty2@example.com")
    pkg = _create_package(client, "Gap7 雙份套餐", stock=5, price=150)
    assert _stock_left(client, pkg["id"]) == 5
    r = _purchase_raw(client, h, pkg["id"],
                      {"payment_method": "fps", "quantity": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["order"]["amount"] == 300
    assert body["order"]["quantity"] == 2
    assert body["payment"]["amount"] == 300
    assert _stock_left(client, pkg["id"]) == 3


def test_purchase_quantity_over_stock_rejected(client):
    h = _register_and_login(client, "gap7-overstock@example.com")
    pkg = _create_package(client, "Gap7 超庫存套餐", stock=5, price=150)
    r = _purchase_raw(client, h, pkg["id"],
                      {"payment_method": "fps", "quantity": 6})
    # Existing error wording for sold out / insufficient stock.
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "此套餐已售罄"
    assert _stock_left(client, pkg["id"]) == 5


def test_purchase_default_quantity_is_one(client):
    h = _register_and_login(client, "gap7-default@example.com")
    pkg = _create_package(client, "Gap7 默認一份套餐", stock=5, price=150)
    r = _purchase_raw(client, h, pkg["id"], {"payment_method": "fps"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["order"]["amount"] == 150
    assert body["order"]["quantity"] == 1
    assert _stock_left(client, pkg["id"]) == 4
