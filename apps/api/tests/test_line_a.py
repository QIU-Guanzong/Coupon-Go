"""Line A tests: redemption hardening + nearby map.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/ -v
Uses its own throwaway SQLite file (coupon_go_test_line_a.db) so parallel
lines running the same suite do not delete each other's test DB.
Covers: prepare -> consume -> duplicate intercept -> revoke window ->
distance sorting (report §10 F7 / F5).
"""
import os
import sys
from datetime import datetime, timedelta, timezone

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

TEST_DB = "/tmp/coupon_go_test_line_a.db"

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


def _db():
    return TestingSession()


def _register_and_login(client, email, role="customer"):
    assert client.post("/api/v3/new-register", json={
        "email": email, "password": "Demo123!", "name": "Test",
        "role": role}).status_code == 200
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
    """Claim a coupon and present it; return (wallet_item_id, code)."""
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": coupon_id}, headers=headers)
    assert r.status_code == 200, r.text
    wid = r.json()["wallet_item_id"]
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=headers)
    assert qr.status_code == 200, qr.text
    return wid, qr.json()["code"]


def _points(client, headers):
    return client.get("/api/v3/points", headers=headers).json()["balance"]


# ---------- prepare -> consume -> duplicate intercept ----------
def test_prepare_consume_idempotent_chain(client):
    h = _register_and_login(client, "line-a-carol@example.com")
    vh = _vendor_headers(client)
    wid, code = _claim_and_qr(client, h, 2)
    assert _points(client, h) == 10

    prep = client.post("/api/v3/new-scan-coupon/prepare",
                       json={"code": code}, headers=vh)
    assert prep.status_code == 200, prep.text
    body = prep.json()
    assert body["can_redeem"] is True
    assert body["wallet_item_id"] == wid
    assert body["prepare_token"]

    assert client.get(f"/api/v3/wallet/items/{wid}/status", headers=h).json()["status"] == 1
    assert client.get(f"/api/v3/wallet/items/{wid}/status", headers=vh).status_code == 404
    assert client.get("/api/v3/coupon-details-new", headers=h, params={"coupon_id": 2}).json()["is_claimed"]
    # Consume with prepare token + idempotency key.
    r = client.post("/api/v3/new-scan-coupon", json={
        "code": code, "prepare_token": body["prepare_token"],
        "idempotency_key": "line-a-key-1"}, headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["duplicate"] is False
    rid = r.json()["redemption_id"]
    assert _points(client, h) == 30

    assert client.get(f"/api/v3/wallet/items/{wid}/status", headers=h).json()["status"] == 2

    # The presented code is dead after consume (screenshot reuse blocked).
    r = client.post("/api/v3/new-scan-coupon", json={"code": code}, headers=vh)
    assert r.status_code == 400

    # Replaying the same idempotency key returns the original result once.
    r = client.post("/api/v3/new-scan-coupon", json={
        "code": code, "idempotency_key": "line-a-key-1"}, headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["duplicate"] is True
    assert r.json()["redemption_id"] == rid
    assert _points(client, h) == 30  # no second +20

    with _db() as db:
        reds = db.query(models.Redemption).filter(
            models.Redemption.idempotency_key == "line-a-key-1").all()
        assert len(reds) == 1
        assert reds[0].prepared_at is not None  # prepare was recorded


def test_prepare_invalid_code(client):
    vh = _vendor_headers(client)
    r = client.post("/api/v3/new-scan-coupon/prepare",
                    json={"code": "000000"}, headers=vh)
    assert r.status_code == 200
    assert r.json()["can_redeem"] is False


def test_prepare_wrong_shop(client):
    # Second vendor owns a different shop; vendor 1 must not redeem its code.
    vh2 = _register_and_login(client, "line-a-vendor2@example.com", role="vendor")
    h = _register_and_login(client, "line-a-dave@example.com")
    with _db() as db:
        v2 = db.query(models.User).filter(
            models.User.email == "line-a-vendor2@example.com").first()
        shop2 = models.Shop(vendor_id=v2.id, name="他店測試", category_id=1,
                            area_id=1, address="測試地址", is_approved=True,
                            latitude=22.3, longitude=114.17)
        db.add(shop2)
        db.flush()
        coupon2 = models.Coupon(
            shop_id=shop2.id, vendor_id=v2.id, coupon_type="Discount",
            coupon_name="他店 9 折", percentage=90,
            valid_from=datetime.now(timezone.utc).replace(tzinfo=None),
            valid_to=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
            total=10, remaining=10, status="active")
        db.add(coupon2)
        db.commit()
        coupon2_id = coupon2.id
    _, code = _claim_and_qr(client, h, coupon2_id)
    vh = _vendor_headers(client)
    r = client.post("/api/v3/new-scan-coupon/prepare",
                    json={"code": code}, headers=vh)
    assert r.status_code == 200
    assert r.json()["can_redeem"] is False
    assert "其他商店" in r.json()["reason"]


def test_consume_without_prepare_still_works(client):
    # Legacy direct scan (no prepare step) keeps working.
    h = _register_and_login(client, "line-a-erin@example.com")
    vh = _vendor_headers(client)
    _, code = _claim_and_qr(client, h, 4)
    r = client.post("/api/v3/new-scan-coupon", json={"code": code}, headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["duplicate"] is False


# ---------- revoke window ----------
def test_revoke_within_window_restores_coupon(client):
    h = _register_and_login(client, "line-a-frank@example.com")
    vh = _vendor_headers(client)
    wid, code = _claim_and_qr(client, h, 5)
    prep = client.post("/api/v3/new-scan-coupon/prepare",
                       json={"code": code}, headers=vh).json()
    r = client.post("/api/v3/new-scan-coupon", json={
        "code": code, "prepare_token": prep["prepare_token"],
        "idempotency_key": "line-a-key-revoke"}, headers=vh)
    assert r.status_code == 200, r.text
    rid = r.json()["redemption_id"]
    assert _points(client, h) == 30

    r = client.post(f"/api/v3/redemptions/{rid}/revoke", headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["wallet_item_id"] == wid
    assert _points(client, h) == 10  # +20 reversed

    wallet = client.get("/api/v3/get-favorite-coupons-list",
                        params={"status": 1}, headers=h).json()
    assert any(i["id"] == wid for i in wallet["items"])  # back to unused

    # Re-present rotates a fresh code; the revoked coupon redeems again.
    qr2 = client.post("/api/v3/store-scaned-qr-values",
                      params={"wallet_item_id": wid}, headers=h)
    assert qr2.status_code == 200
    assert qr2.json()["code"] != code
    r = client.post("/api/v3/new-scan-coupon", json={
        "code": qr2.json()["code"],
        "idempotency_key": "line-a-key-revoke-2"}, headers=vh)
    assert r.status_code == 200, r.text
    assert _points(client, h) == 30


def test_revoke_twice_rejected(client):
    h = _register_and_login(client, "line-a-grace@example.com")
    vh = _vendor_headers(client)
    _, code = _claim_and_qr(client, h, 6)
    r = client.post("/api/v3/new-scan-coupon", json={
        "code": code, "idempotency_key": "line-a-key-revoke-twice"}, headers=vh)
    rid = r.json()["redemption_id"]
    assert client.post(f"/api/v3/redemptions/{rid}/revoke",
                       headers=vh).status_code == 200
    r = client.post(f"/api/v3/redemptions/{rid}/revoke", headers=vh)
    assert r.status_code == 400


def test_revoke_after_window_rejected(client):
    h = _register_and_login(client, "line-a-heidi@example.com")
    vh = _vendor_headers(client)
    _, code = _claim_and_qr(client, h, 8)
    r = client.post("/api/v3/new-scan-coupon", json={
        "code": code, "idempotency_key": "line-a-key-late"}, headers=vh)
    rid = r.json()["redemption_id"]
    with _db() as db:
        red = db.query(models.Redemption).filter(
            models.Redemption.id == rid).first()
        red.scanned_at = datetime.now(timezone.utc).replace(
            tzinfo=None) - timedelta(minutes=11)
        db.commit()
    r = client.post(f"/api/v3/redemptions/{rid}/revoke", headers=vh)
    assert r.status_code == 400
    assert "10 分鐘" in r.json()["detail"]


# ---------- export ----------
def test_vendor_redemption_export(client):
    vh = _vendor_headers(client)
    r = client.get("/api/v3/vendor/redemptions/export", headers=vh)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 1
    assert any(rec["idempotency_key"] == "line-a-key-1"
               for rec in body["records"])
    r = client.get("/api/v3/vendor/redemptions/export",
                   params={"format": "csv"}, headers=vh)
    assert r.status_code == 200
    assert r.text.startswith("redemption_id,wallet_item_id,coupon_name")


# ---------- nearby map ----------
def test_nearby_distance_sorting_and_radius(client):
    # Centre: 灣仔軒尼詩道 (shop 1 櫻花壽司屋, distance ~0).
    r = client.get("/api/v3/nearby-shops",
                   params={"lat": 22.2783, "lng": 114.1747})
    assert r.status_code == 200, r.text
    shops = r.json()["shops"]
    assert shops[0]["shop_name"] == "櫻花壽司屋"
    assert shops[0]["distance_km"] == 0.0
    dists = [s["distance_km"] for s in shops]
    assert dists == sorted(dists)
    assert all("google_maps_url" in s and "directions_url" in s
               for s in shops)

    r = client.get("/api/v3/nearby-shops",
                   params={"lat": 22.2783, "lng": 114.1747, "radius_km": 0.1})
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["shops"][0]["shop_name"] == "櫻花壽司屋"


def test_nearby_requires_location(client):
    r = client.get("/api/v3/nearby-shops")
    assert r.status_code == 400
    # Gap 8: sort=rating is now supported (was 400 before); only an
    # unknown sort value is rejected.
    r = client.get("/api/v3/nearby-shops",
                   params={"lat": 22.2783, "lng": 114.1747, "sort": "rating"})
    assert r.status_code == 200, r.text
    assert r.json()["sort"] == "rating"
    r = client.get("/api/v3/nearby-shops",
                   params={"lat": 22.2783, "lng": 114.1747, "sort": "price"})
    assert r.status_code == 400


def test_shop_map_links(client):
    r = client.get("/api/v3/shops/1/map")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["latitude"] == 22.2783
    assert body["longitude"] == 114.1747
    assert "google.com/maps" in body["google_maps_url"]
    assert "/dir/" in body["directions_url"]
    assert client.get("/api/v3/shops/99999/map").status_code == 404


def test_qbot_public_offers_and_private_points(client, monkeypatch):
    import json
    import httpx
    from app.routers import qbot
    h = _register_and_login(client, "qbot-user@example.com")
    _claim_and_qr(client, h, 2)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-fixture")
    calls = []

    def provider(url, **kwargs):
        content = json.loads(kwargs["json"]["messages"][1]["content"])
        assert content["public_offers"]
        assert "qbot-user" not in str(content) and "points_balance" not in content
        calls.append(content)
        return httpx.Response(200, request=httpx.Request("POST", url),
                              json={"choices": [{"message": {"content": "打開 Q Wallet。"}}]})

    monkeypatch.setattr(qbot.httpx, "post", provider)
    body = {"message": "我有幾多積分？"}
    result = client.post("/api/v3/qbot/chat", headers=h, json=body)
    assert result.status_code == 200
    assert result.json() == {"answer": "打開 Q Wallet。", "points_balance": 10}
    assert client.post("/api/v3/qbot/chat", json=body).json()["points_balance"] is None
    assert len(calls) == 2
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    assert client.post("/api/v3/qbot/chat", json=body).status_code == 503


def test_refresh_requires_new_merchant_confirmation(client):
    h = _register_and_login(client, "refresh-confirm@example.com")
    vh = _vendor_headers(client)
    wid, initial = _claim_and_qr(client, h, 2)
    old = client.post("/api/v3/new-scan-coupon/prepare", headers=vh, json={"code": initial}).json()
    code = client.post("/api/v3/store-scaned-qr-values", headers=h,
                       params={"wallet_item_id": wid}).json()["code"]
    assert code != initial
    assert not client.post("/api/v3/new-scan-coupon/prepare", headers=vh, json={"code": initial}).json()["can_redeem"]
    stale = client.post("/api/v3/new-scan-coupon", headers=vh,
                        json={"code": code, "prepare_token": old["prepare_token"]})
    assert stale.status_code == 400 and _points(client, h) == 10
    current = client.post("/api/v3/new-scan-coupon/prepare", headers=vh, json={"code": code}).json()
    assert current["can_redeem"] and current["prepare_token"] != old["prepare_token"]
