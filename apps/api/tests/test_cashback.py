"""Line D tests: cashback state machines, withdrawal, expiry, void, appeals.

Run:  cd apps/api && ~/workspace/coupon-go-fullstack/.venv/bin/python -m pytest tests/ -v
Uses its own throwaway SQLite file; never touches production data.
Default assumptions under test: dine-in rate 5%, 90-day unlock/expiry,
online confirm 30 days, HK$50 minimum withdrawal.
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

from app import models, models_cashback  # noqa: E402,F401
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

TEST_DB = "/tmp/coupon_go_test_cashback.db"

engine = create_engine(f"sqlite:///{TEST_DB}", connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


def _record(client, h, merchant_id=1, amount=1200, channel="dinein"):
    r = client.post("/api/v3/cashback/record", json={
        "merchant_id": merchant_id, "amount": amount, "channel": channel},
        headers=h)
    assert r.status_code == 200, r.text
    return r.json()["transaction"]


def _backdate_txn(txn_id, **kwargs):
    """Move a transaction's timestamps (test-only time travel)."""
    db = TestingSession()
    try:
        t = db.query(models_cashback.CashbackTransaction).filter(
            models_cashback.CashbackTransaction.id == txn_id).first()
        assert t is not None
        for k, v in kwargs.items():
            setattr(t, k, v)
        db.commit()
    finally:
        db.close()


# ---------- dine-in: pending -> next visit unlocks -> withdrawable -> withdraw ----------
def test_dinein_unlock_withdraw_chain(client):
    h = _register_and_login(client, "cb-alice@example.com")
    t1 = _record(client, h, merchant_id=1, amount=1200, channel="dinein")
    assert t1["status"] == "pending" and t1["amount"] == 60.0 and t1["rate"] == 0.05
    assert t1["expires_at"] is not None  # 90-day unlock deadline set
    bal = client.get("/api/v3/cashback", headers=h).json()
    assert bal["pending_balance"] == 60.0 and bal["withdrawable_balance"] == 0.0

    # Second spend at the SAME merchant unlocks the first earning.
    t2 = _record(client, h, merchant_id=1, amount=1200, channel="dinein")
    assert t2["status"] == "pending"
    txns = client.get("/api/v3/cashback/transactions",
                      headers=h).json()["transactions"]
    first = [t for t in txns if t["id"] == t1["id"]][0]
    assert first["status"] == "withdrawable" and first["confirmed_at"] is not None
    bal = client.get("/api/v3/cashback", headers=h).json()
    assert bal["withdrawable_balance"] == 60.0 and bal["pending_balance"] == 60.0

    # Below the HK$50 minimum -> rejected.
    r = client.post("/api/v3/cashback/withdraw", json={
        "amount": 5, "method": "fps", "account_name": "Test"}, headers=h)
    assert r.status_code == 400
    # Account name must match the registered name.
    r = client.post("/api/v3/cashback/withdraw", json={
        "amount": 50, "method": "fps", "account_name": "Someone Else"}, headers=h)
    assert r.status_code == 400
    # Over the withdrawable balance -> rejected.
    r = client.post("/api/v3/cashback/withdraw", json={
        "amount": 61, "method": "fps", "account_name": "Test"}, headers=h)
    assert r.status_code == 400

    r = client.post("/api/v3/cashback/withdraw", json={
        "amount": 50, "method": "fps", "account_name": "Test",
        "account_ref": "12345678"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["withdrawal"]["status"] == "pending"
    bal = client.get("/api/v3/cashback", headers=h).json()
    assert bal["withdrawable_balance"] == 10.0  # 60 - 50, partial FIFO consume
    wds = client.get("/api/v3/cashback/withdrawals", headers=h).json()["withdrawals"]
    assert len(wds) == 1 and wds[0]["amount"] == 50.0 and wds[0]["method"] == "fps"


def test_dinein_unlock_is_same_merchant_only(client):
    h = _register_and_login(client, "cb-bob@example.com")
    _record(client, h, merchant_id=1, amount=1000, channel="dinein")  # 50 pending
    _record(client, h, merchant_id=2, amount=1000, channel="dinein")  # other shop
    bal = client.get("/api/v3/cashback", headers=h).json()
    assert bal["withdrawable_balance"] == 0.0 and bal["pending_balance"] == 100.0


def test_dinein_90_day_expiry_voids(client):
    h = _register_and_login(client, "cb-carol@example.com")
    t = _record(client, h, merchant_id=1, amount=1000, channel="dinein")
    # 91 days pass with no return visit: sweep on read voids it.
    past = _now() - timedelta(days=91)
    _backdate_txn(t["id"], created_at=past,
                  expires_at=past + timedelta(days=90))
    txns = client.get("/api/v3/cashback/transactions",
                      headers=h).json()["transactions"]
    mine = [x for x in txns if x["id"] == t["id"]][0]
    assert mine["status"] == "void" and mine["void_reason"] == "expired_90d"
    bal = client.get("/api/v3/cashback", headers=h).json()
    assert bal["pending_balance"] == 0.0 and bal["withdrawable_balance"] == 0.0


def test_online_confirm_window(client):
    h = _register_and_login(client, "cb-dave@example.com")
    t = _record(client, h, merchant_id=1, amount=1000, channel="online")
    assert t["status"] == "pending" and t["expected_confirm_at"] is not None
    assert t["expires_at"] is None  # no 90-day unlock deadline online
    # Merchant confirm window passes -> withdrawable, confirmed_at stamped.
    _backdate_txn(t["id"], expected_confirm_at=_now() - timedelta(days=1))
    txns = client.get("/api/v3/cashback/transactions",
                      headers=h).json()["transactions"]
    mine = [x for x in txns if x["id"] == t["id"]][0]
    assert mine["status"] == "withdrawable" and mine["confirmed_at"] is not None
    bal = client.get("/api/v3/cashback", headers=h).json()
    assert bal["withdrawable_balance"] == 50.0


def test_cancel_return_voids_pending_only(client):
    h = _register_and_login(client, "cb-eve@example.com")
    t = _record(client, h, merchant_id=1, amount=500, channel="dinein")
    r = client.post(f"/api/v3/cashback/transactions/{t['id']}/void",
                    json={"reason": "cancelled"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["transaction"]["status"] == "void"
    assert r.json()["transaction"]["void_reason"] == "cancelled"
    # Voiding twice -> rejected.
    r = client.post(f"/api/v3/cashback/transactions/{t['id']}/void",
                    json={"reason": "returned"}, headers=h)
    assert r.status_code == 400
    # A withdrawable earning cannot be voided (confirmed by merchant).
    t2 = _record(client, h, merchant_id=1, amount=1000, channel="online")
    _backdate_txn(t2["id"], expected_confirm_at=_now() - timedelta(days=1))
    client.get("/api/v3/cashback", headers=h)  # sweep -> withdrawable
    r = client.post(f"/api/v3/cashback/transactions/{t2['id']}/void",
                    json={"reason": "returned"}, headers=h)
    assert r.status_code == 400


def test_cashback_separate_from_points(client):
    h = _register_and_login(client, "cb-frank@example.com")
    assert client.get("/api/v3/points", headers=h).json()["balance"] == 0
    _record(client, h, merchant_id=1, amount=1000, channel="dinein")
    # Earning cashback does not move points, and vice versa.
    assert client.get("/api/v3/points", headers=h).json()["balance"] == 0
    client.post("/api/v3/new-favorite-coupon", params={"coupon_id": 2}, headers=h)
    assert client.get("/api/v3/points", headers=h).json()["balance"] == 10
    bal = client.get("/api/v3/cashback", headers=h).json()
    assert bal["pending_balance"] == 50.0  # unchanged by the +10 points


def test_missing_cashback_appeal(client):
    h = _register_and_login(client, "cb-grace@example.com")
    r = client.post("/api/v3/cashback/missing", json={
        "merchant_id": 1, "order_no": "ORD-20261116-001",
        "spent_at": _now().isoformat(), "amount": 300,
        "evidence": "訂單確認截圖 https://example.invalid/receipt.png",
        "note": "到店用 ShopBack 付款，未見返現"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["appeal"]["status"] == "submitted"
    assert r.json()["appeal"]["order_no"] == "ORD-20261116-001"
    # Older than the 30-day filing window -> rejected.
    r = client.post("/api/v3/cashback/missing", json={
        "merchant_id": 1, "order_no": "ORD-OLD",
        "spent_at": (_now() - timedelta(days=31)).isoformat(),
        "amount": 300}, headers=h)
    assert r.status_code == 400


def test_cashback_requires_auth(client):
    assert client.get("/api/v3/cashback").status_code == 401
    assert client.post("/api/v3/cashback/record", json={
        "merchant_id": 1, "amount": 100, "channel": "dinein"}).status_code == 401
    assert client.post("/api/v3/cashback/missing", json={
        "merchant_id": 1, "order_no": "X",
        "spent_at": _now().isoformat(), "amount": 100}).status_code == 401
