"""N1 tests (P0): cashback basis is the ACTUAL PAID amount only.

The old record_spend() fell back to coupon.min_spend when a coupon had
no discount_price, so a free / threshold-only coupon credited cashback
on its threshold amount: claim a free coupon -> scan -> withdraw =
money created from nothing.

New rule (N1):
* package order: WalletItem.order_id set -> Order.paid_amount (> 0 only);
* price coupon: coupon.discount_price (> 0 only);
* neither (free coupon, pure-threshold coupon, priceless discount
  coupon) -> no cashback at all (record_spend returns None);
* a redemption with no paid amount still triggers the dine-in unlock
  of earlier pending earnings at the same merchant (unlock logic is
  NOT skipped).

Run:  cd apps/api && ~/workspace/coupon-go-fullstack/.venv/bin/python -m pytest tests/test_n1_cashback_paid_basis.py -v
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

from tests.db_isolation import sqlite_test_path  # noqa: E402

TEST_DB = sqlite_test_path("coupon_go_test_n1_cashback.db")

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
                 coupon_type="Cash", name="N1 測試券"):
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


def _claim(client, h, coupon_id):
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": coupon_id}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["wallet_item_id"]


def _scan(client, h, vh, wallet_item_id):
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wallet_item_id}, headers=h)
    assert qr.status_code == 200, qr.text
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["duplicate"] is False
    return r.json()


def _redeem(client, h, vh, coupon_id):
    """Claim + present + vendor-scan one coupon; return the scan payload."""
    wid = _claim(client, h, coupon_id)
    return _scan(client, h, vh, wid)


def _make_order(email, paid_amount, amount=None):
    """Insert a package order for the user; return its id."""
    db = TestingSession()
    try:
        user = db.query(models.User).filter(models.User.email == email).first()
        o = models.Order(
            user_id=user.id, order_type="package",
            amount=amount if amount is not None else paid_amount,
            paid_amount=paid_amount, payment_method="fps",
            payment_status="paid" if paid_amount > 0 else "pending")
        db.add(o)
        db.commit()
        return o.id
    finally:
        db.close()


def _attach_order_to_wallet_item(wallet_item_id, order_id):
    db = TestingSession()
    try:
        item = db.query(models.WalletItem).filter(
            models.WalletItem.id == wallet_item_id).first()
        assert item is not None
        order = db.get(models.Order, order_id)
        coupon = db.get(models.Coupon, item.coupon_id)
        assert order is not None and coupon is not None
        # Model a valid package purchase: the order and voucher must refer
        # to the same package under the current paid-voucher checks. The
        # fixture keeps amount and paid_amount distinct to test the basis.
        package = models.Package(
            shop_id=coupon.shop_id, name="N1 套餐", items="[]", person_count=2,
            original_price=order.amount, package_price=order.paid_amount,
            valid_from=coupon.valid_from, valid_to=coupon.valid_to,
            stock_total=1, stock_left=0, status="active")
        db.add(package)
        db.flush()
        coupon.kind = "package"
        coupon.package_id = package.id
        order.package_id = package.id
        order.package_coupon_id = coupon.id
        item.order_id = order.id
        item.source = "purchase"
        db.commit()
    finally:
        db.close()


def _redeem_package(client, h, vh, coupon_id, order_id):
    """Claim a package coupon, link its wallet item to an order, scan."""
    wid = _claim(client, h, coupon_id)
    _attach_order_to_wallet_item(wid, order_id)
    return _scan(client, h, vh, wid)


def _balances(client, h):
    return client.get("/api/v3/cashback", headers=h).json()


def _txns(client, h):
    return client.get("/api/v3/cashback/transactions",
                      headers=h).json()["transactions"]


# ---------- 1. free / threshold-only / priceless coupons earn nothing ----------
def test_free_coupon_with_threshold_earns_nothing(client):
    """Free coupon (no discount_price, min_spend=200): the old min_spend
    fallback credited HK$10 — the刷钱 hole. Now: no transaction."""
    h = _register_and_login(client, "n1-free@example.com")
    vh = _vendor_headers(client)
    c = _make_coupon(shop_id=1, min_spend=200, discount_price=None,
                     name="N1 免費券")
    scan = _redeem(client, h, vh, c)
    assert _txns(client, h) == []
    bal = _balances(client, h)
    assert bal["pending_balance"] == 0.0
    assert bal["withdrawable_balance"] == 0.0
    assert bal["total_earned"] == 0.0
    # The scan itself still succeeded (redemption exists, just no cashback).
    db = TestingSession()
    try:
        assert db.query(models.Redemption).filter(
            models.Redemption.id == scan["redemption_id"]).first() is not None
    finally:
        db.close()


def test_pure_threshold_and_priceless_discount_coupons_earn_nothing(client):
    h = _register_and_login(client, "n1-threshold@example.com")
    vh = _vendor_headers(client)
    # Pure threshold coupon: min_spend only, no price at all.
    c1 = _make_coupon(shop_id=1, min_spend=500, discount_price=None,
                      name="N1 純門檻券")
    _redeem(client, h, vh, c1)
    # Priceless discount coupon: neither price nor threshold.
    c2 = _make_coupon(shop_id=1, min_spend=0, discount_price=None,
                      coupon_type="Discount", name="N1 折扣券無價")
    _redeem(client, h, vh, c2)
    assert _txns(client, h) == []
    assert _balances(client, h)["total_earned"] == 0.0


# ---------- 2. price coupon: basis is discount_price ----------
def test_price_coupon_earns_on_discount_price(client):
    """discount_price=100 x configured dine-in rate 5% = HK$5.00."""
    h = _register_and_login(client, "n1-price@example.com")
    vh = _vendor_headers(client)
    c = _make_coupon(shop_id=1, discount_price=100, coupon_type="Price",
                     name="N1 價格券")
    scan = _redeem(client, h, vh, c)
    txns = _txns(client, h)
    assert len(txns) == 1, txns
    assert txns[0]["redemption_id"] == scan["redemption_id"]
    assert txns[0]["amount"] == 5.0 and txns[0]["rate"] == 0.05
    assert txns[0]["status"] == "pending"
    assert _balances(client, h)["pending_balance"] == 5.0


# ---------- 3. package order: basis is Order.paid_amount ----------
def test_package_order_coupon_earns_on_paid_amount(client):
    """WalletItem.order_id -> Order.paid_amount=298 x 5% = HK$14.90.
    The coupon row itself carries a different discount_price (100) to
    prove the order's real paid amount — not the coupon price — is used.
    Order.amount (應付) is 398; only paid_amount (實付) counts."""
    h = _register_and_login(client, "n1-package@example.com")
    vh = _vendor_headers(client)
    order_id = _make_order("n1-package@example.com", paid_amount=298,
                           amount=398)
    c = _make_coupon(shop_id=1, discount_price=100, coupon_type="Price",
                     name="N1 套餐券")
    scan = _redeem_package(client, h, vh, c, order_id)
    txns = _txns(client, h)
    assert len(txns) == 1, txns
    assert txns[0]["redemption_id"] == scan["redemption_id"]
    assert txns[0]["amount"] == 14.9 and txns[0]["rate"] == 0.05
    assert _balances(client, h)["pending_balance"] == 14.9


def test_package_order_unpaid_earns_nothing(client):
    """Package order with paid_amount=0 (never paid): no cashback, even
    though the coupon row has a discount_price — an unpaid order is not
    a real spend. (Order precedence: order present -> paid_amount rules.)"""
    h = _register_and_login(client, "n1-unpaid@example.com")
    order_id = _make_order("n1-unpaid@example.com", paid_amount=0,
                           amount=298)
    c = _make_coupon(shop_id=1, discount_price=100, coupon_type="Price",
                     name="N1 未支付套餐券")
    wid = _claim(client, h, c)
    _attach_order_to_wallet_item(wid, order_id)
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=h)
    assert qr.status_code == 400
    assert "付款或退款狀態" in qr.json()["detail"]
    assert _txns(client, h) == []
    assert _balances(client, h)["total_earned"] == 0.0


# ---------- 4. no-paid-amount redemption still unlocks prior earnings ----------
def test_no_cashback_redemption_still_unlocks_prior_earnings(client):
    """A free-coupon redemption earns nothing itself, but it is still a
    valid visit at the merchant, so it unlocks an earlier pending
    earning there (the unlock logic must not be skipped)."""
    h = _register_and_login(client, "n1-unlock@example.com")
    vh = _vendor_headers(client)
    c1 = _make_coupon(shop_id=1, discount_price=1200, coupon_type="Price",
                      name="N1 先行價格券")
    first = _redeem(client, h, vh, c1)
    assert _txns(client, h)[0]["status"] == "pending"
    free = _make_coupon(shop_id=1, min_spend=200, discount_price=None,
                        name="N1 免費解鎖券")
    _redeem(client, h, vh, free)
    txns = _txns(client, h)
    assert len(txns) == 1, txns  # the free redemption added no earning
    assert txns[0]["redemption_id"] == first["redemption_id"]
    assert txns[0]["status"] == "withdrawable"
    assert txns[0]["amount"] == 60.0
    bal = _balances(client, h)
    assert bal["withdrawable_balance"] == 60.0
    assert bal["pending_balance"] == 0.0


# ---------- 5. regressions: revoke void + duplicate credit refused ----------
def test_revoke_still_voids_cashback(client):
    """P1-1 regression: revoking a redemption voids its earning."""
    h = _register_and_login(client, "n1-revoke@example.com")
    vh = _vendor_headers(client)
    c = _make_coupon(shop_id=1, discount_price=1200, coupon_type="Price",
                     name="N1 撤銷券")
    scan = _redeem(client, h, vh, c)
    assert len(_txns(client, h)) == 1
    assert _txns(client, h)[0]["amount"] == 60.0
    r = client.post(f"/api/v3/redemptions/{scan['redemption_id']}/revoke",
                    headers=vh)
    assert r.status_code == 200, r.text
    t = _txns(client, h)[0]
    assert t["status"] == "void" and t["void_reason"] == "revoked"
    assert _balances(client, h)["pending_balance"] == 0.0


def test_duplicate_credit_still_rejected(client):
    """FIX-2 regression: one redemption credits at most once — the
    service guard refuses a replay, and the UNIQUE constraint on
    cashback_transactions.redemption_id rejects a second row."""
    from app.cashback_service import CashbackError, record_spend

    h = _register_and_login(client, "n1-dupe@example.com")
    vh = _vendor_headers(client)
    c = _make_coupon(shop_id=1, discount_price=1000, coupon_type="Price",
                     name="N1 重複券")
    scan = _redeem(client, h, vh, c)
    assert len(_txns(client, h)) == 1

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
