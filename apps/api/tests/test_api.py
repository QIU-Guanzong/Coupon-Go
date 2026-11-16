"""Backend tests: auth, claim, redeem, points (core chains).

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/ -v
Uses a throwaway SQLite file; never touches production data.
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

TEST_DB = "/tmp/coupon_go_test.db"

engine = create_engine(f"sqlite:///{TEST_DB}", connect_args={"check_same_thread": False})
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


# ---------- auth ----------
def test_register_login_otp_flow(client):
    h = _register_and_login(client, "alice@example.com")
    r = client.get("/api/v3/get-profile", headers=h)
    assert r.status_code == 200 and r.json()["email"] == "alice@example.com"


def test_login_wrong_password_rejected(client):
    r = client.post("/api/v3/login", json={
        "email": "demo@coupon-go.example", "password": "Wrong123!"})
    assert r.status_code == 401


def test_forgot_reset_flow(client):
    _register_and_login(client, "bob@example.com")
    assert client.post("/api/v3/password/forgot",
                       json={"email": "bob@example.com"}).status_code == 200
    code = client.get("/api/v3/_dev/otp",
                      params={"email": "bob@example.com"}).json()["code"]
    r = client.post("/api/v3/password/reset", json={
        "email": "bob@example.com", "code": code, "new_password": "NewPass123!"})
    assert r.status_code == 200, r.text
    r = client.post("/api/v3/login", json={
        "email": "bob@example.com", "password": "NewPass123!"})
    assert r.status_code == 200


def test_unauthenticated_wallet_rejected(client):
    assert client.get("/api/v3/get-favorite-coupons-list").status_code == 401


# ---------- discovery ----------
def test_home_sections(client):
    r = client.get("/api/v3/home")
    assert r.status_code == 200
    body = r.json()
    for key in ("popular_coupons", "week_coupons", "most_grab", "recommended",
                "limited_time_coupons", "popular_merchants", "categories"):
        assert key in body and len(body[key]) > 0, key
    assert len(body["limited_time_coupons"]) == 13


def test_search_suggestion_three_branches(client):
    r = client.post("/api/v3/auto-suggestion-new", params={"keyword": "壽司"})
    assert r.status_code == 200
    kinds = {s["kind"] for s in r.json()["suggestions"]}
    assert "coupon" in kinds and "shop" in kinds


def test_coupon_and_shop_details(client):
    assert client.get("/api/v3/coupon-details-new",
                      params={"coupon_id": 1}).status_code == 200
    r = client.get("/api/v3/shop-details", params={"shop_id": 1})
    assert r.status_code == 200 and r.json()["shop"]["shop_name"] == "櫻花壽司屋"


# ---------- claim -> QR -> scan -> points ----------
def test_claim_redeem_review_points_chain(client):
    h = _register_and_login(client, "carol@example.com")
    r = client.post("/api/v3/new-favorite-coupon", params={"coupon_id": 2}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "已存入 Q Wallet"
    assert client.get("/api/v3/points", headers=h).json()["balance"] == 10

    r = client.post("/api/v3/new-favorite-coupon", params={"coupon_id": 2}, headers=h)
    assert r.status_code == 409  # duplicate claim blocked

    wid = client.get("/api/v3/get-favorite-coupons-list",
                     headers=h).json()["items"][0]["id"]
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=h)
    assert qr.status_code == 200 and qr.json()["expires_in_seconds"] == 900

    vh = _vendor_headers(client)
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 200, r.text
    assert client.get("/api/v3/points", headers=h).json()["balance"] == 30

    r = client.post("/api/v3/review", params={"wallet_item_id": wid}, headers=h)
    assert r.status_code == 200, r.text
    assert client.get("/api/v3/points", headers=h).json()["balance"] == 45

    r = client.post("/api/v3/review", params={"wallet_item_id": wid}, headers=h)
    assert r.status_code == 400  # review only once

    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 400  # already used

    wallet = client.get("/api/v3/get-favorite-coupons-list",
                        params={"status": 2}, headers=h).json()
    assert wallet["total"] == 1


def test_scan_wrong_code_rejected(client):
    vh = _vendor_headers(client)
    r = client.post("/api/v3/new-scan-coupon", json={"code": "000000"}, headers=vh)
    assert r.status_code == 400


def test_points_exchange_9_point_coupon(client):
    h = _register_and_login(client, "dave@example.com")
    # claim 1 coupon (+10) to afford the 9-point exchange
    client.post("/api/v3/new-favorite-coupon", params={"coupon_id": 3}, headers=h)
    coupons = client.get("/api/v3/get-classification-coupons").json()["coupons"]
    exchange = [c for c in coupons if c["coupon_name"] == "9 分分券"]
    assert not exchange  # points-exchange coupon is not a regular listing
    # exchange endpoint needs the coupon id: find it via home? use direct id 37
    r = client.post("/api/v3/points/exchange", params={"coupon_id": 37}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["balance"] == 1


# ---------- vendor flow ----------
def test_vendor_preview_create_approve(client):
    vh = _vendor_headers(client)
    body = {
        "shop_id": 1, "coupon_type": "Discount", "coupon_name": "測試 9 折",
        "percentage": 90, "valid_from": "2026-11-16T00:00:00",
        "valid_to": "2026-12-16T00:00:00", "total": 50,
    }
    r = client.post("/api/v3/add-coupon-preview", json=body, headers=vh)
    assert r.status_code == 200 and r.json()["amount_text"] == "90 折"
    r = client.post("/api/v3/coupon-previewed", json=body, headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["coupon"]["status"] == "pending_review"
    cid = r.json()["coupon"]["id"]
    ah = _admin_headers(client)
    r = client.post(f"/api/v3/vendor/coupons/{cid}/approve",
                    params={"approve": True}, headers=ah)
    assert r.status_code == 200 and r.json()["status"] == "active"


def test_vendor_usage_data(client):
    vh = _vendor_headers(client)
    r = client.get("/api/v3/get-vendor-usage-data-new", headers=vh)
    assert r.status_code == 200
    assert r.json()["total_downloads"] >= 1
    assert r.json()["total_usage"] >= 1


def test_customer_cannot_issue_coupon(client):
    h = _register_and_login(client, "eve@example.com")
    body = {"shop_id": 1, "coupon_type": "Discount", "coupon_name": "x",
            "valid_from": "2026-11-16T00:00:00",
            "valid_to": "2026-12-16T00:00:00", "total": 10}
    r = client.post("/api/v3/coupon-previewed", json=body, headers=h)
    assert r.status_code == 403
