"""FIX-2 tests: cashback may only be credited from a redemption record.

POST /cashback/record (user-supplied amount / merchant / rate) is gone.
Credit happens server-side, inside the successful vendor scan
(/new-scan-coupon), via cashback_service.record_spend():
amount basis = the coupon's own server-side price (discount_price; N1:
min_spend is a threshold, never a spend basis), rate = the configured
dine-in merchant rate. One redemption
credits at most once (service guard + UNIQUE on
cashback_transactions.redemption_id).

Run:  cd apps/api && ~/workspace/coupon-go-fullstack/.venv/bin/python -m pytest tests/test_cashback_fix2.py -v
Uses its own throwaway SQLite file; never touches production data.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import models, models_cashback  # noqa: E402,F401
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

TEST_DB = "/tmp/coupon_go_test_cashback_fix2.db"

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


def _make_coupon(shop_id=1, min_spend=0, discount_price=None,
                 coupon_type="Cash", name="FIX-2 測試券"):
    """Insert a fresh active coupon straight into the test DB (server-side
    data the client cannot forge) and return its id."""
    db = TestingSession()
    try:
        vendor = db.query(models.User).filter(
            models.User.email == "vendor@coupon-go.example").first()
        shop = db.query(models.Shop).filter(models.Shop.id == shop_id).first()
        c = models.Coupon(
            shop_id=shop.id, vendor_id=vendor.id, coupon_type=coupon_type,
            coupon_name=name, category_id=shop.category_id,
            min_spend=min_spend, discount_price=discount_price,
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


def _balances(client, h):
    return client.get("/api/v3/cashback", headers=h).json()


def _txns(client, h):
    return client.get("/api/v3/cashback/transactions",
                      headers=h).json()["transactions"]


# ---------- 1. the user-adjustable endpoint is gone ----------
def test_record_endpoint_gone(client):
    h = _register_and_login(client, "fix2-alice@example.com")
    r = client.post("/api/v3/cashback/record", json={
        "merchant_id": 1, "amount": 1200, "channel": "dinein"}, headers=h)
    assert r.status_code == 404, r.text
    # Even a "fully specified" forged record (custom rate + redemption id)
    # must not create anything.
    r = client.post("/api/v3/cashback/record", json={
        "merchant_id": 1, "amount": 99999, "channel": "dinein",
        "rate": 1.0, "redemption_id": 1}, headers=h)
    assert r.status_code == 404, r.text
    bal = _balances(client, h)
    assert bal["withdrawable_balance"] == 0.0
    assert bal["pending_balance"] == 0.0
    assert bal["total_earned"] == 0.0


def test_forged_spends_cannot_withdraw(client):
    """Two self-invented records, then a withdrawal: impossible, balance 0."""
    h = _register_and_login(client, "fix2-bob@example.com")
    for amount, merchant_id, rate in ((1200, 1, 0.05), (5000, 2, 1.0)):
        r = client.post("/api/v3/cashback/record", json={
            "merchant_id": merchant_id, "amount": amount,
            "channel": "dinein", "rate": rate}, headers=h)
        assert r.status_code == 404, r.text
    bal = _balances(client, h)
    assert bal["withdrawable_balance"] == 0.0
    assert bal["pending_balance"] == 0.0
    r = client.post("/api/v3/cashback/withdraw", json={
        "amount": 50, "method": "fps", "account_name": "Test"}, headers=h)
    assert r.status_code == 400, r.text


def test_no_redemption_no_cashback(client):
    h = _register_and_login(client, "fix2-carol@example.com")
    bal = _balances(client, h)
    for key in ("withdrawable_balance", "pending_balance",
                "tracked_balance", "total_earned"):
        assert bal[key] == 0.0, bal
    assert _txns(client, h) == []


# ---------- 2. a real redemption credits exactly once ----------
def test_real_redemption_auto_credits_once(client):
    h = _register_and_login(client, "fix2-dave@example.com")
    vh = _vendor_headers(client)
    # discount_price HK$1000 x configured dine-in rate 5% = HK$50.
    c1 = _make_coupon(shop_id=1, discount_price=1000, name="FIX-2 券 A")
    scan1 = _redeem(client, h, vh, c1)
    txns = _txns(client, h)
    assert len(txns) == 1, txns
    t1 = txns[0]
    assert t1["redemption_id"] == scan1["redemption_id"]
    assert t1["amount"] == 50.0 and t1["rate"] == 0.05
    assert t1["status"] == "pending" and t1["merchant_id"] == 1
    assert t1["channel"] == "dinein"
    bal = _balances(client, h)
    assert bal["pending_balance"] == 50.0 and bal["total_earned"] == 50.0

    # A second redemption at the SAME merchant unlocks the first earning
    # (dine-in next-visit unlock, triggered only by a new valid redemption).
    c2 = _make_coupon(shop_id=1, discount_price=1000, name="FIX-2 券 B")
    scan2 = _redeem(client, h, vh, c2)
    txns = _txns(client, h)
    assert len(txns) == 2, txns
    by_red = {t["redemption_id"]: t for t in txns}
    assert set(by_red) == {scan1["redemption_id"], scan2["redemption_id"]}
    assert by_red[scan1["redemption_id"]]["status"] == "withdrawable"
    assert by_red[scan2["redemption_id"]]["status"] == "pending"
    assert by_red[scan2["redemption_id"]]["amount"] == 50.0
    bal = _balances(client, h)
    assert bal["withdrawable_balance"] == 50.0
    assert bal["pending_balance"] == 50.0


def test_redemption_cashback_amount_from_coupon_price(client):
    """Price-type coupon: paid price = discount_price (HK$138) x 5% = 6.90."""
    h = _register_and_login(client, "fix2-eve@example.com")
    vh = _vendor_headers(client)
    c = _make_coupon(shop_id=1, discount_price=138, coupon_type="Price",
                     name="FIX-2 點心拼盤")
    scan = _redeem(client, h, vh, c)
    txns = _txns(client, h)
    assert len(txns) == 1
    assert txns[0]["redemption_id"] == scan["redemption_id"]
    assert txns[0]["amount"] == 6.9


def test_redemption_cashback_belongs_to_redeemer_only(client):
    h = _register_and_login(client, "fix2-frank@example.com")
    other = _register_and_login(client, "fix2-grace@example.com")
    vh = _vendor_headers(client)
    c = _make_coupon(shop_id=1, discount_price=1000, name="FIX-2 券 C")
    scan = _redeem(client, h, vh, c)
    assert _txns(client, other) == []
    assert _balances(client, other)["total_earned"] == 0.0
    db = TestingSession()
    try:
        t = db.query(models_cashback.CashbackTransaction).filter(
            models_cashback.CashbackTransaction.redemption_id ==
            scan["redemption_id"]).first()
        assert t is not None
        owner = db.query(models.WalletItem).filter(
            models.WalletItem.id == db.query(models.Redemption).filter(
                models.Redemption.id == scan["redemption_id"]
            ).first().wallet_item_id).first()
        assert t.user_id == owner.user_id
    finally:
        db.close()


# ---------- 3. one redemption, one credit — enforced twice ----------
def test_duplicate_redemption_credit_rejected(client):
    from app.cashback_service import CashbackError, record_spend

    h = _register_and_login(client, "fix2-heidi@example.com")
    vh = _vendor_headers(client)
    c = _make_coupon(shop_id=1, discount_price=1000, name="FIX-2 券 D")
    scan = _redeem(client, h, vh, c)
    assert len(_txns(client, h)) == 1

    # Replaying the internal credit call for the same redemption is refused.
    db = TestingSession()
    try:
        red = db.query(models.Redemption).filter(
            models.Redemption.id == scan["redemption_id"]).first()
        with pytest.raises(CashbackError):
            record_spend(db, red)
        db.rollback()
        count = db.query(models_cashback.CashbackTransaction).filter(
            models_cashback.CashbackTransaction.redemption_id ==
            scan["redemption_id"]).count()
        assert count == 1
    finally:
        db.close()

    # The DB-level UNIQUE constraint rejects a second row outright.
    db = TestingSession()
    try:
        t = db.query(models_cashback.CashbackTransaction).filter(
            models_cashback.CashbackTransaction.redemption_id ==
            scan["redemption_id"]).first()
        dupe = models_cashback.CashbackTransaction(
            user_id=t.user_id, merchant_id=t.merchant_id,
            redemption_id=scan["redemption_id"], channel="dinein",
            amount=50, rate=0.05, status="pending",
            funded_by="merchant_commission")
        db.add(dupe)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()
    assert len(_txns(client, h)) == 1


def test_revoked_redemption_earns_nothing(client):
    """A redemption already revoked can never be credited (service guard)."""
    from app.cashback_service import CashbackError, record_spend

    h = _register_and_login(client, "fix2-ivan@example.com")
    vh = _vendor_headers(client)
    c = _make_coupon(shop_id=1, discount_price=1000, name="FIX-2 券 E")
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": c}, headers=h)
    wid = r.json()["wallet_item_id"]
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=h)
    code = qr.json()["code"]
    # Build a revoked redemption row directly (a revoked scan that was
    # never credited): the guard must refuse to credit it afterwards.
    db = TestingSession()
    try:
        vendor = db.query(models.User).filter(
            models.User.email == "vendor@coupon-go.example").first()
        red = models.Redemption(wallet_item_id=wid, coupon_id=c, shop_id=1,
                                scanned_by_vendor_id=vendor.id,
                                revoked_at=_now(), revoked_by=vendor.id)
        db.add(red)
        db.commit()
        red_id = red.id
        with pytest.raises(CashbackError):
            record_spend(db, red)
        db.rollback()
        assert db.query(models_cashback.CashbackTransaction).filter(
            models_cashback.CashbackTransaction.redemption_id == red_id
        ).count() == 0
    finally:
        db.close()
    assert _txns(client, h) == []
