"""Line D tests: cashback state machines, withdrawal, expiry, void, appeals.

Run:  cd apps/api && ~/workspace/coupon-go-fullstack/.venv/bin/python -m pytest tests/ -v
Uses its own throwaway SQLite file; never touches production data.
Default assumptions under test: dine-in rate 5%, 90-day unlock/expiry,
online confirm 30 days, HK$50 minimum withdrawal.

FIX-2: POST /cashback/record is gone. Every earning below comes from a
real vendor redemption (claim -> QR -> /new-scan-coupon), with the spend
basis taken from the coupon row itself (min_spend). The online-confirm
tests build their legacy online rows straight in the DB — no API can
create an online earning anymore.
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


def _vendor_headers(client):
    r = client.post("/api/v3/login", json={
        "email": "vendor@coupon-go.example", "password": "Demo123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_coupon(shop_id=1, min_spend=0, name="測試券"):
    """Insert a fresh active coupon (server-side data) and return its id."""
    db = TestingSession()
    try:
        vendor = db.query(models.User).filter(
            models.User.email == "vendor@coupon-go.example").first()
        shop = db.query(models.Shop).filter(models.Shop.id == shop_id).first()
        c = models.Coupon(
            shop_id=shop.id, vendor_id=vendor.id, coupon_type="Cash",
            coupon_name=name, category_id=shop.category_id,
            min_spend=min_spend,
            valid_from=_now() - timedelta(days=1),
            valid_to=_now() + timedelta(days=30),
            total=100, remaining=100, status="active")
        db.add(c)
        db.commit()
        return c.id
    finally:
        db.close()


def _redeem(client, h, vh, coupon_id):
    """Claim + present + vendor-scan one coupon; return the scan payload."""
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


def _record(client, h, merchant_id=1, amount=1200, channel="dinein"):
    """FIX-2: a "spend" is a real redemption now. amount is the coupon's
    min_spend (server-side spend basis); channel is accepted for call
    compatibility but redemptions are always dine-in."""
    assert channel == "dinein", "redemptions are dine-in only since FIX-2"
    vh = _vendor_headers(client)
    coupon_id = _make_coupon(shop_id=merchant_id, min_spend=amount)
    scan = _redeem(client, h, vh, coupon_id)
    txns = client.get("/api/v3/cashback/transactions",
                      headers=h).json()["transactions"]
    mine = [t for t in txns
            if t["redemption_id"] == scan["redemption_id"]]
    assert len(mine) == 1, txns
    return mine[0]


def _insert_legacy_online_txn(email, merchant_id=1, amount=50,
                              expected_confirm_at=None):
    """A legacy online-pending earning (pre-FIX-2 shape), built straight
    in the DB because no API can create one anymore. redemption_id is
    NOT NULL since FIX-2, so a bare redemption row is inserted first —
    it never went through the scan, so no dine-in earning references it.
    The user must already hold a claimed coupon (wallet item)."""
    db = TestingSession()
    try:
        user = db.query(models.User).filter(
            models.User.email == email).first()
        vendor = db.query(models.User).filter(
            models.User.email == "vendor@coupon-go.example").first()
        item = db.query(models.WalletItem).filter(
            models.WalletItem.user_id == user.id).first()
        assert item is not None
        red = models.Redemption(wallet_item_id=item.id, coupon_id=item.coupon_id,
                                shop_id=merchant_id,
                                scanned_by_vendor_id=vendor.id)
        db.add(red)
        db.flush()
        t = models_cashback.CashbackTransaction(
            user_id=user.id, merchant_id=merchant_id,
            redemption_id=red.id, channel="online", amount=amount,
            rate=0.05, status="pending", funded_by="merchant_commission",
            expected_confirm_at=expected_confirm_at, expires_at=None)
        db.add(t)
        db.commit()
        return t.id
    finally:
        db.close()


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

    # Second redemption at the SAME merchant unlocks the first earning.
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
    # Legacy online earning (built in the DB; no API creates one anymore):
    # merchant confirm window passes -> withdrawable, confirmed_at stamped.
    h = _register_and_login(client, "cb-dave@example.com")
    vh = _vendor_headers(client)
    _redeem(client, h, vh, 2)  # claim a coupon so a wallet item exists
    t_id = _insert_legacy_online_txn(
        "cb-dave@example.com", merchant_id=1, amount=50,
        expected_confirm_at=_now() - timedelta(days=1))
    txns = client.get("/api/v3/cashback/transactions",
                      headers=h).json()["transactions"]
    mine = [x for x in txns if x["id"] == t_id][0]
    assert mine["status"] == "withdrawable" and mine["confirmed_at"] is not None
    assert mine["channel"] == "online" and mine["expires_at"] is None
    bal = client.get("/api/v3/cashback", headers=h).json()
    # 50 withdrawable from the legacy online row; the seed-coupon
    # redemption above (滿 $200 減 $30, min_spend 200 x 5%) is still
    # pending as a dine-in earning of 10.
    assert bal["withdrawable_balance"] == 50.0
    assert bal["pending_balance"] == 10.0


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
    # Build a legacy online earning whose confirm window has passed.
    vh = _vendor_headers(client)
    _redeem(client, h, vh, 2)
    t2_id = _insert_legacy_online_txn(
        "cb-eve@example.com", merchant_id=1, amount=50,
        expected_confirm_at=_now() - timedelta(days=1))
    client.get("/api/v3/cashback", headers=h)  # sweep -> withdrawable
    r = client.post(f"/api/v3/cashback/transactions/{t2_id}/void",
                    json={"reason": "returned"}, headers=h)
    assert r.status_code == 400


def test_cashback_separate_from_points(client):
    h = _register_and_login(client, "cb-frank@example.com")
    assert client.get("/api/v3/points", headers=h).json()["balance"] == 0
    _record(client, h, merchant_id=1, amount=1000, channel="dinein")
    # Earning cashback does not move points beyond the claim (+10) and
    # redeem (+20) points the redemption itself pays; cashback never
    # touches point_transactions.
    assert client.get("/api/v3/points", headers=h).json()["balance"] == 30
    client.post("/api/v3/new-favorite-coupon", params={"coupon_id": 2}, headers=h)
    assert client.get("/api/v3/points", headers=h).json()["balance"] == 40
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
    # FIX-2: the record endpoint no longer exists at all (404, not 401).
    assert client.post("/api/v3/cashback/record", json={
        "merchant_id": 1, "amount": 100, "channel": "dinein"}).status_code == 404
    assert client.post("/api/v3/cashback/missing", json={
        "merchant_id": 1, "order_no": "X",
        "spent_at": _now().isoformat(), "amount": 100}).status_code == 401
