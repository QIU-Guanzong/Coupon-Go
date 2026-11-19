"""P1-1 tests: revoking a redemption reverses its cashback and rolls
back the dine-in unlocks that redemption triggered.

Rules under test (see README 返现一节）:
* the revoked redemption's own earning is voided (void_reason=revoked);
* a predecessor earning unlocked BY this redemption reverts to pending
  (待解锁） — only its un-withdrawn remainder survives, as the earning
  itself is still valid; its withdrawn part was already paid out and
  stays paid (no adjustment: the earning is real, only the
  confirmation is rolled back);
* if the revoked redemption's own earning was already (partly)
  withdrawn, the withdrawn part is recorded as a negative
  CashbackAdjustment — the money is gone, the earning is dead — and
  the (possibly negative) withdrawable balance blocks further
  withdrawals;
* unlocks triggered by OTHER redemptions are untouched (provenance is
  tracked per redemption).
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

TEST_DB = "/tmp/coupon_go_test_p1_revoke.db"

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


def _register_and_login(client, email, name="Test"):
    r = client.post("/api/v3/new-register", json={
        "email": email, "password": "Demo123!", "name": name,
        "role": "customer"})
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


def _make_coupon(shop_id=1, discount_price=1200, name="測試券"):
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
    return r.json()


def _txns(client, h):
    return client.get("/api/v3/cashback/transactions", headers=h).json()["transactions"]


def _txn_for(client, h, redemption_id):
    mine = [t for t in _txns(client, h) if t["redemption_id"] == redemption_id]
    assert len(mine) == 1, _txns(client, h)
    return mine[0]


def _balances(client, h):
    return client.get("/api/v3/cashback", headers=h).json()


def _revoke(client, vh, redemption_id):
    r = client.post(f"/api/v3/redemptions/{redemption_id}/revoke", headers=vh)
    assert r.status_code == 200, r.text
    return r.json()


def _withdraw(client, h, amount, account_name="Test"):
    return client.post("/api/v3/cashback/withdraw", json={
        "amount": amount, "method": "fps",
        "account_name": account_name}, headers=h)


def test_revoke_voids_own_pending_cashback(client):
    h = _register_and_login(client, "p1-rev-alice@example.com")
    vh = _vendor_headers(client)
    scan = _redeem(client, h, vh, _make_coupon(shop_id=1, discount_price=1200))
    t = _txn_for(client, h, scan["redemption_id"])
    assert t["status"] == "pending" and t["amount"] == 60.0
    _revoke(client, vh, scan["redemption_id"])
    t = _txn_for(client, h, scan["redemption_id"])
    assert t["status"] == "void" and t["void_reason"] == "revoked"
    bal = _balances(client, h)
    assert bal["pending_balance"] == 0.0 and bal["withdrawable_balance"] == 0.0


def test_revoke_reverts_unlock_triggered_by_revoked_redemption(client):
    h = _register_and_login(client, "p1-rev-bob@example.com")
    vh = _vendor_headers(client)
    first = _redeem(client, h, vh, _make_coupon(shop_id=1, discount_price=1200))
    second = _redeem(client, h, vh, _make_coupon(shop_id=1, discount_price=1200))
    assert _txn_for(client, h, first["redemption_id"])["status"] == "withdrawable"
    assert _txn_for(client, h, second["redemption_id"])["status"] == "pending"
    _revoke(client, vh, second["redemption_id"])
    # The unlock is rolled back: the first earning waits for its next
    # visit again; the revoked redemption's own earning is void.
    t1 = _txn_for(client, h, first["redemption_id"])
    assert t1["status"] == "pending" and t1["confirmed_at"] is None
    t2 = _txn_for(client, h, second["redemption_id"])
    assert t2["status"] == "void" and t2["void_reason"] == "revoked"
    bal = _balances(client, h)
    assert bal["pending_balance"] == 60.0 and bal["withdrawable_balance"] == 0.0


def test_revoke_does_not_revert_unlock_from_other_redemption(client):
    h = _register_and_login(client, "p1-rev-carol@example.com")
    vh = _vendor_headers(client)
    first = _redeem(client, h, vh, _make_coupon(shop_id=1, discount_price=1200))
    unlocker = _redeem(client, h, vh, _make_coupon(shop_id=1, discount_price=1200))
    other_shop = _redeem(client, h, vh, _make_coupon(shop_id=2, discount_price=1200))
    assert _txn_for(client, h, first["redemption_id"])["status"] == "withdrawable"
    _revoke(client, vh, other_shop["redemption_id"])
    # The first earning was unlocked by `unlocker`, not by the revoked
    # redemption at shop 2 — it stays withdrawable.
    assert _txn_for(client, h, first["redemption_id"])["status"] == "withdrawable"
    assert _txn_for(client, h, unlocker["redemption_id"])["status"] == "pending"


def test_revoke_own_withdrawn_cashback_creates_negative_adjustment(client):
    h = _register_and_login(client, "p1-rev-dave@example.com")
    vh = _vendor_headers(client)
    first = _redeem(client, h, vh, _make_coupon(shop_id=1, discount_price=1200))
    _redeem(client, h, vh, _make_coupon(shop_id=1, discount_price=1200))
    r = _withdraw(client, h, 50)
    assert r.status_code == 200, r.text
    bal = _balances(client, h)
    assert bal["withdrawable_balance"] == 10.0
    # Revoke the FIRST redemption: its earning (HK$60, HK$50 already
    # paid out) is dead, so the paid-out HK$50 becomes a negative
    # adjustment and the balance goes negative.
    _revoke(client, vh, first["redemption_id"])
    t = _txn_for(client, h, first["redemption_id"])
    assert t["status"] == "void" and t["void_reason"] == "revoked"
    bal = _balances(client, h)
    assert bal["withdrawable_balance"] == -50.0
    assert bal["adjustment_total"] == -50.0
    assert any(a["amount"] == -50.0 for a in bal["adjustments"])
    # A negative balance blocks any further withdrawal.
    r = _withdraw(client, h, 50)
    assert r.status_code == 400, r.text
    assert "餘額不足" in r.json()["detail"]


def test_revoke_partially_withdrawn_predecessor_reverts_remainder(client):
    h = _register_and_login(client, "p1-rev-erin@example.com")
    vh = _vendor_headers(client)
    first = _redeem(client, h, vh, _make_coupon(shop_id=1, discount_price=1200))
    second = _redeem(client, h, vh, _make_coupon(shop_id=1, discount_price=1200))
    r = _withdraw(client, h, 50)
    assert r.status_code == 200, r.text
    # Revoke the unlocking redemption: the first earning (HK$60, HK$50
    # paid) reverts to pending for its HK$10 remainder only — the
    # earning is real, only its confirmation is rolled back, so no
    # negative adjustment is recorded for it.
    _revoke(client, vh, second["redemption_id"])
    t1 = _txn_for(client, h, first["redemption_id"])
    assert t1["status"] == "pending" and t1["amount"] == 10.0
    bal = _balances(client, h)
    assert bal["pending_balance"] == 10.0
    assert bal["withdrawable_balance"] == 0.0
    assert bal["adjustment_total"] == 0.0
    assert bal["adjustments"] == []
    # A third visit unlocks the HK$10 remainder again.
    _redeem(client, h, vh, _make_coupon(shop_id=1, discount_price=1200))
    t1 = _txn_for(client, h, first["redemption_id"])
    assert t1["status"] == "withdrawable" and t1["amount"] == 10.0


def test_revoke_redemption_without_cashback_ok(client):
    h = _register_and_login(client, "p1-rev-fay@example.com")
    vh = _vendor_headers(client)
    scan = _redeem(client, h, vh, _make_coupon(shop_id=1, discount_price=None))
    assert _txns(client, h) == []
    r = client.post(f"/api/v3/redemptions/{scan['redemption_id']}/revoke",
                    headers=vh)
    assert r.status_code == 200, r.text


def test_third_visit_remains_a_valid_unlock_after_middle_reversal(client):
    customer = _register_and_login(client, "three-visits@example.com")
    vendor = _vendor_headers(client)
    first, middle, last = [_redeem(client, customer, vendor, _make_coupon()) for _ in range(3)]
    _revoke(client, vendor, middle["redemption_id"])
    assert _txn_for(client, customer, first["redemption_id"])["status"] == "withdrawable"
    _revoke(client, vendor, last["redemption_id"])
    assert _txn_for(client, customer, first["redemption_id"])["status"] == "pending"


def test_reversing_original_earning_retains_partial_payout_debt(client):
    customer = _register_and_login(client, "partial-then-revoke@example.com")
    vendor = _vendor_headers(client)
    first, second = [_redeem(client, customer, vendor, _make_coupon()) for _ in range(2)]
    assert _withdraw(client, customer, 50).status_code == 200
    _revoke(client, vendor, second["redemption_id"])
    assert _balances(client, customer)["total_earned"] == 60
    _revoke(client, vendor, first["redemption_id"])
    balances = _balances(client, customer)
    assert balances["withdrawable_balance"] == -50 and balances["pending_balance"] == 0
