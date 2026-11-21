"""M10 tests: cashback master switch CASHBACK_ENABLED (default OFF).

Frontend already hides the cashback entry (§3.2), but the backend still
records cashback on every redemption (routers/coupons.py:259) — invisible
HK$ liability. With the switch OFF:
* a redemption must not call record_spend (no CashbackTransaction);
* POST /cashback/withdraw returns 403 (new withdrawals blocked);
* balance query / ledger / admin review / void / missing appeals stay
  available; void_for_revocation still reverses old earnings created
  while the switch was ON.

/app-config gains three fields: cashback_enabled (follows the switch),
points_exchange_cost (= 9, from config), open_login (default false,
field only — PROMPTS_OPEN_LOGIN_20261120.md was not found anywhere).

Fixture pattern copied from tests/test_cashback_fix2.py. Uses its own
throwaway SQLite file; never touches production data.
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

from app import config, models, models_cashback  # noqa: E402,F401
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

TEST_DB = "/tmp/coupon_go_test_m10_cashback_switch.db"
# Real-Postgres mode (mirrors test_m6_settlement_basis.py): set
# M10_TEST_POSTGRES_URL to a postgresql+psycopg2:// URL whose database
# is m10_cash and whose host comes via the ?host= socket query.
PG_URL = os.getenv("M10_TEST_POSTGRES_URL")
if PG_URL:
    from sqlalchemy.engine import make_url
    parts = make_url(PG_URL)
    assert parts.database == "m10_cash" and not parts.host
    assert str(parts.query.get("host", "")).startswith("/")
    engine = create_engine(PG_URL)
else:
    engine = create_engine(f"sqlite:///{TEST_DB}", connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture(scope="module")
def client():
    if PG_URL:
        Base.metadata.drop_all(bind=engine)
    elif os.path.exists(TEST_DB):
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


def _make_coupon(shop_id=1, discount_price=1000, name="M10 測試券"):
    db = TestingSession()
    try:
        vendor = db.query(models.User).filter(
            models.User.email == "vendor@coupon-go.example").first()
        shop = db.query(models.Shop).filter(models.Shop.id == shop_id).first()
        c = models.Coupon(
            shop_id=shop.id, vendor_id=vendor.id, coupon_type="Cash",
            coupon_name=name, category_id=shop.category_id,
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
    assert r.json()["duplicate"] is False
    return r.json()


def _txns(client, h):
    return client.get("/api/v3/cashback/transactions",
                      headers=h).json()["transactions"]


def _balances(client, h):
    return client.get("/api/v3/cashback", headers=h).json()


def _txn_for(client, h, redemption_id):
    mine = [t for t in _txns(client, h) if t["redemption_id"] == redemption_id]
    assert len(mine) == 1, _txns(client, h)
    return mine[0]


def _withdraw(client, h, amount, account_name="Test"):
    return client.post("/api/v3/cashback/withdraw", json={
        "amount": amount, "method": "fps",
        "account_name": account_name}, headers=h)


# ---------- 1. switch OFF: redemption credits nothing ----------
def test_switch_off_redemption_creates_no_cashback(client, monkeypatch):
    monkeypatch.setattr(config, "CASHBACK_ENABLED", False)
    h = _register_and_login(client, "m10-off-alice@example.com")
    vh = _vendor_headers(client)
    scan = _redeem(client, h, vh, _make_coupon())
    # The redemption itself succeeds; only the cashback credit is skipped.
    db = TestingSession()
    try:
        row = db.query(models_cashback.CashbackTransaction).filter(
            models_cashback.CashbackTransaction.redemption_id ==
            scan["redemption_id"]).first()
        assert row is None
    finally:
        db.close()
    assert _txns(client, h) == []
    bal = _balances(client, h)
    assert bal["withdrawable_balance"] == 0.0
    assert bal["pending_balance"] == 0.0
    assert bal["total_earned"] == 0.0


# ---------- 2. switch ON: behaviour unchanged ----------
def test_switch_on_redemption_credits_as_before(client, monkeypatch):
    monkeypatch.setattr(config, "CASHBACK_ENABLED", True)
    h = _register_and_login(client, "m10-on-bob@example.com")
    vh = _vendor_headers(client)
    scan = _redeem(client, h, vh, _make_coupon())
    txns = _txns(client, h)
    assert len(txns) == 1, txns
    t = txns[0]
    assert t["redemption_id"] == scan["redemption_id"]
    assert t["amount"] == 50.0 and t["rate"] == 0.05
    assert t["status"] == "pending" and t["merchant_id"] == 1
    bal = _balances(client, h)
    assert bal["pending_balance"] == 50.0 and bal["total_earned"] == 50.0


# ---------- 3. switch OFF: revoking an old (switch-ON) earning still reverses ----------
def test_switch_off_revoke_still_reverses_old_cashback(client, monkeypatch):
    monkeypatch.setattr(config, "CASHBACK_ENABLED", True)
    h = _register_and_login(client, "m10-rev-carol@example.com")
    vh = _vendor_headers(client)
    first = _redeem(client, h, vh, _make_coupon(discount_price=1200))
    _redeem(client, h, vh, _make_coupon(discount_price=1200))
    # First earning (HK$60) is withdrawable after the second visit;
    # withdraw HK$50 while the switch is still ON.
    assert _withdraw(client, h, 50).status_code == 200
    assert _balances(client, h)["withdrawable_balance"] == 10.0
    # Now switch OFF and revoke the first redemption: the old earning
    # must still be voided and the paid-out HK$50 recorded as a
    # negative adjustment — the switch only blocks NEW credits.
    monkeypatch.setattr(config, "CASHBACK_ENABLED", False)
    r = client.post(f"/api/v3/redemptions/{first['redemption_id']}/revoke",
                    headers=vh)
    assert r.status_code == 200, r.text
    t = _txn_for(client, h, first["redemption_id"])
    assert t["status"] == "void" and t["void_reason"] == "revoked"
    bal = _balances(client, h)
    assert bal["withdrawable_balance"] == -50.0
    assert bal["adjustment_total"] == -50.0


# ---------- 4. switch OFF: new withdrawals 403; switch ON: unchanged ----------
def test_switch_off_withdraw_403_switch_on_withdraw_ok(client, monkeypatch):
    monkeypatch.setattr(config, "CASHBACK_ENABLED", True)
    h = _register_and_login(client, "m10-wd-dave@example.com")
    vh = _vendor_headers(client)
    _redeem(client, h, vh, _make_coupon(discount_price=1200))
    _redeem(client, h, vh, _make_coupon(discount_price=1200))
    assert _balances(client, h)["withdrawable_balance"] == 60.0
    # Switch OFF: even with an old withdrawable balance, a new
    # withdrawal request is refused.
    monkeypatch.setattr(config, "CASHBACK_ENABLED", False)
    r = _withdraw(client, h, 50)
    assert r.status_code == 403, r.text
    assert _balances(client, h)["withdrawable_balance"] == 60.0
    # Switch back ON: the withdrawal flow is unchanged.
    monkeypatch.setattr(config, "CASHBACK_ENABLED", True)
    r = _withdraw(client, h, 50)
    assert r.status_code == 200, r.text
    assert r.json()["withdrawal"]["status"] == "pending"


# ---------- 5. /app-config: three new fields, cashback_enabled follows the switch ----------
def test_app_config_new_fields_follow_switch(client, monkeypatch):
    monkeypatch.setattr(config, "CASHBACK_ENABLED", False)
    result = client.get("/api/v3/app-config")
    assert result.status_code == 200
    body = result.json()
    assert body["cashback_enabled"] is False
    assert body["points_exchange_cost"] == 9
    assert body["open_login"] is False
    monkeypatch.setattr(config, "CASHBACK_ENABLED", True)
    result = client.get("/api/v3/app-config")
    assert result.status_code == 200
    assert result.json()["cashback_enabled"] is True
