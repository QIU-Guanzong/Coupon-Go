"""Bargain 2C tests (line E): start -> help -> floor/group success ->
purchase -> package coupon in wallet -> redeem; timeout refund;
stock lock/release; duplicate-help / device anti-fraud.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/ -v
Uses its own throwaway SQLite file per run (tempfile.mkdtemp) so the
parallel lines' pytest runs cannot collide on a shared /tmp path.
"""
import os
import sys
import tempfile
from decimal import Decimal

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
from app.models_bargain import BargainCampaign  # noqa: E402,F401
from app.routers.bargain import cut_amount  # noqa: E402
from app.seed import seed  # noqa: E402
from app.seed_bargain import seed_bargain_campaigns  # noqa: E402

TEST_DB = os.path.join(tempfile.mkdtemp(prefix="coupon_go_test_line_e_"),
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


def _make_campaign(client, shop_id=1, original=200, floor=190, stock=5,
                   helper_limit=10, duration_minutes=1440):
    vh = _login(client, "vendor@coupon-go.example")
    r = client.post("/api/v3/bargain-campaigns", json={
        "shop_id": shop_id, "name": "測試雙人餐",
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


# ---------- cut curve (pure function) ----------
def test_cut_amount_curve_decays_and_caps_at_floor():
    # new user: 30% + 10% bonus on first help; old user: 30% flat
    assert cut_amount(Decimal("100"), 0, True) == Decimal("40.00")
    assert cut_amount(Decimal("100"), 0, False) == Decimal("30.00")
    assert cut_amount(Decimal("100"), 1, False) == Decimal("25.00")
    assert cut_amount(Decimal("100"), 5, False) == Decimal("5.00")
    # never below floor: capped at remaining
    assert cut_amount(Decimal("0.50"), 0, True) == Decimal("0.50")
    # min cut HK$1 when remaining is small but > 0
    assert cut_amount(Decimal("2.00"), 5, False) == Decimal("1.00")
    assert cut_amount(Decimal("0"), 0, True) == Decimal("0.00")


# ---------- campaigns ----------
def test_list_campaigns_guest_sees_seeded(client):
    r = client.get("/api/v3/bargain-campaigns")
    assert r.status_code == 200
    names = [c["name"] for c in r.json()["campaigns"]]
    assert any("櫻花壽司屋" in n for n in names)
    assert any("蜀香麻辣火鍋城" in n for n in names)


def test_create_campaign_validation(client):
    vh = _login(client, "vendor@coupon-go.example")
    base = {"shop_id": 1, "name": "x", "original_price": 100,
            "stock_total": 5}
    # floor must be below original
    r = client.post("/api/v3/bargain-campaigns",
                    json={**base, "floor_price": 100}, headers=vh)
    assert r.status_code == 400
    # unknown shop
    r = client.post("/api/v3/bargain-campaigns",
                    json={**base, "shop_id": 9999, "floor_price": 50},
                    headers=vh)
    assert r.status_code == 404
    # customer cannot create campaigns
    h = _register_and_login(client, "bargain-creator@example.com")
    r = client.post("/api/v3/bargain-campaigns",
                    json={**base, "floor_price": 50}, headers=h)
    assert r.status_code == 403


# ---------- main chain: start -> help to floor -> purchase -> wallet -> redeem
def test_start_help_to_floor_purchase_wallet_redeem_chain(client):
    h = _register_and_login(client, "bargain-anna@example.com")
    camp = _make_campaign(client, original=200, floor=190, stock=5,
                          helper_limit=10)
    assert camp["stock_left"] == 5
    s = _start(client, h, camp["id"])
    assert s["current_price"] == 200.0 and s["paid_amount"] == 200.0
    assert s["status"] == "active" and s["share_link"].endswith(f"session={s['id']}")
    # stock locked on start: 5 -> 4
    camps = client.get("/api/v3/bargain-campaigns").json()["campaigns"]
    assert [c for c in camps if c["id"] == camp["id"]][0]["stock_left"] == 4

    # 6 new-user helps: cuts 4.00, 2.10, 1.17, 1.00, 1.00, 0.73 -> floor 190
    expected_cuts = [4.00, 2.10, 1.17, 1.00, 1.00, 0.73]
    for i, expected in enumerate(expected_cuts):
        helper = _register_and_login(client, f"bargain-helper{i}@example.com")
        r = client.post(f"/api/v3/bargain-sessions/{s['id']}/help",
                        json={"device_id": f"dev-chain-{i}"}, headers=helper)
        assert r.status_code == 200, r.text
        assert r.json()["cut_amount"] == expected, r.json()
        assert r.json()["is_new_user_bonus"] is True
    s = client.get(f"/api/v3/bargain-sessions/{s['id']}").json()["session"]
    assert s["status"] == "success" and s["current_price"] == 190.0
    assert s["helper_count"] == 6 and s["can_purchase"] is True

    r = client.post(f"/api/v3/bargain-sessions/{s['id']}/purchase", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["final_price"] == 190.0
    assert r.json()["refund"]["amount"] == 10.0  # 200 - 190 price-cut refund
    assert r.json()["refund"]["reason"] == "price_cut"
    wid = r.json()["wallet_item_id"]

    # package coupon landed in Q Wallet, status 1 (unused), source bargain
    db = TestingSession()
    try:
        item = db.query(models.WalletItem).filter(
            models.WalletItem.id == wid).first()
        assert item is not None and item.status == 1
        assert item.source == "bargain"
        coupon = db.query(models.Coupon).filter(
            models.Coupon.id == item.coupon_id).first()
        assert coupon.kind == "package"
        assert float(coupon.discount_price) == 190.0
        assert float(coupon.regular_price) == 200.0
    finally:
        db.close()

    # existing redeem loop works on the bargain coupon: QR -> vendor scan
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=h)
    assert qr.status_code == 200
    vh = _login(client, "vendor@coupon-go.example")
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 200, r.text
    assert client.get("/api/v3/points", headers=h).json()["balance"] == 20


def test_group_forms_by_helper_limit_even_above_floor(client):
    h = _register_and_login(client, "bargain-ben@example.com")
    camp = _make_campaign(client, original=268, floor=50, stock=3,
                          helper_limit=3)
    s = _start(client, h, camp["id"])
    for i in range(3):
        helper = _register_and_login(client, f"bargain-g{i}@example.com")
        r = client.post(f"/api/v3/bargain-sessions/{s['id']}/help",
                        json={"device_id": f"dev-group-{i}"}, headers=helper)
        assert r.status_code == 200, r.text
    s = client.get(f"/api/v3/bargain-sessions/{s['id']}").json()["session"]
    assert s["status"] == "success"
    assert s["current_price"] > 50.0  # formed by headcount, not by floor
    r = client.post(f"/api/v3/bargain-sessions/{s['id']}/purchase", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["refund"]["amount"] == round(268 - s["current_price"], 2)


def test_purchase_before_success_rejected(client):
    h = _register_and_login(client, "bargain-cara@example.com")
    camp = _make_campaign(client, original=200, floor=100, stock=5,
                          helper_limit=10)
    s = _start(client, h, camp["id"])
    r = client.post(f"/api/v3/bargain-sessions/{s['id']}/purchase", headers=h)
    assert r.status_code == 400


def test_duplicate_self_and_device_help_blocked(client):
    h = _register_and_login(client, "bargain-dan@example.com")
    camp = _make_campaign(client, original=200, floor=100, stock=5,
                          helper_limit=10)
    s = _start(client, h, camp["id"])
    # initiator cannot help own session
    r = client.post(f"/api/v3/bargain-sessions/{s['id']}/help",
                    json={"device_id": "dev-self"}, headers=h)
    assert r.status_code == 400
    helper1 = _register_and_login(client, "bargain-eve@example.com")
    r = client.post(f"/api/v3/bargain-sessions/{s['id']}/help",
                    json={"device_id": "dev-shared"}, headers=helper1)
    assert r.status_code == 200, r.text
    # same user, second help on the same session
    r = client.post(f"/api/v3/bargain-sessions/{s['id']}/help",
                    json={"device_id": "dev-other"}, headers=helper1)
    assert r.status_code == 409
    # same device, different account
    helper2 = _register_and_login(client, "bargain-fay@example.com")
    r = client.post(f"/api/v3/bargain-sessions/{s['id']}/help",
                    json={"device_id": "dev-shared"}, headers=helper2)
    assert r.status_code == 409
    # different device works
    r = client.post(f"/api/v3/bargain-sessions/{s['id']}/help",
                    json={"device_id": "dev-fay"}, headers=helper2)
    assert r.status_code == 200, r.text


def test_one_active_session_per_campaign(client):
    h = _register_and_login(client, "bargain-gus@example.com")
    camp = _make_campaign(client, original=200, floor=100, stock=5,
                          helper_limit=10)
    _start(client, h, camp["id"])
    r = client.post(f"/api/v3/bargain-campaigns/{camp['id']}/start", headers=h)
    assert r.status_code == 409


def test_stock_lock_blocks_second_start_and_releases_on_timeout(client):
    h1 = _register_and_login(client, "bargain-hal@example.com")
    h2 = _register_and_login(client, "bargain-ida@example.com")
    camp = _make_campaign(client, original=200, floor=100, stock=1,
                          helper_limit=10, duration_minutes=0)
    s = _start(client, h1, camp["id"])
    camps = client.get("/api/v3/bargain-campaigns").json()["campaigns"]
    mine = [c for c in camps if c["id"] == camp["id"]][0]
    assert mine["stock_left"] == 0 and mine["status"] == "sold_out"
    # second user cannot start while the only unit is locked
    r = client.post(f"/api/v3/bargain-campaigns/{camp['id']}/start", headers=h2)
    assert r.status_code == 400
    # reading the timed-out session settles it: failed + full refund + release
    s = client.get(f"/api/v3/bargain-sessions/{s['id']}").json()["session"]
    assert s["status"] == "failed"
    assert len(s["refunds"]) == 1
    assert s["refunds"][0]["amount"] == 200.0
    assert s["refunds"][0]["reason"] == "bargain_failed"
    camps = client.get("/api/v3/bargain-campaigns").json()["campaigns"]
    mine = [c for c in camps if c["id"] == camp["id"]][0]
    assert mine["stock_left"] == 1 and mine["status"] == "active"
    # helping a failed session is rejected
    helper = _register_and_login(client, "bargain-jay@example.com")
    r = client.post(f"/api/v3/bargain-sessions/{s['id']}/help",
                    json={}, headers=helper)
    assert r.status_code == 400
    # stock is free again: the second user can start now
    r = client.post(f"/api/v3/bargain-campaigns/{camp['id']}/start", headers=h2)
    assert r.status_code == 200, r.text


def test_expire_sweep_settles_timeout(client):
    h = _register_and_login(client, "bargain-kim@example.com")
    camp = _make_campaign(client, original=200, floor=100, stock=5,
                          helper_limit=10, duration_minutes=0)
    s = _start(client, h, camp["id"])
    ah = _login(client, "admin@coupon-go.example")
    r = client.post("/api/v3/bargain/expire-sweep", headers=ah)
    assert r.status_code == 200 and r.json()["settled"] >= 1, r.text
    s = client.get(f"/api/v3/bargain-sessions/{s['id']}").json()["session"]
    assert s["status"] == "failed"
    assert s["refunds"][0]["amount"] == 200.0


def test_unauthenticated_and_vendor_blocked(client):
    camp = _make_campaign(client, original=200, floor=100, stock=5,
                          helper_limit=10)
    assert client.post(
        f"/api/v3/bargain-campaigns/{camp['id']}/start").status_code == 401
    vh = _login(client, "vendor@coupon-go.example")
    r = client.post(f"/api/v3/bargain-campaigns/{camp['id']}/start", headers=vh)
    assert r.status_code == 403
    h = _register_and_login(client, "bargain-leo@example.com")
    s = _start(client, h, camp["id"])
    assert client.post(
        f"/api/v3/bargain-sessions/{s['id']}/help", json={}).status_code == 401


def test_my_sessions_lists_mine(client):
    h = _register_and_login(client, "bargain-mia@example.com")
    camp = _make_campaign(client, original=200, floor=100, stock=5,
                          helper_limit=10)
    s = _start(client, h, camp["id"])
    r = client.get("/api/v3/bargain-my-sessions", headers=h)
    assert r.status_code == 200
    assert any(x["id"] == s["id"] for x in r.json()["sessions"])
