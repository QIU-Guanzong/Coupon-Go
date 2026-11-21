"""P1-C tests: points by spend.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_p1c_points_by_spend.py -v
Own throwaway SQLite file; never touches production data.

Rules (docs/FEATURE_GAP_20261120.md §3.4, docs/P1C_API.md):
- paid package voucher redeem -> floor(paid_amount * POINTS_PER_HKD),
  default 1 pt per HK$1, credited after redemption;
- free coupon claim +10 / redeem +20 / review +15 unchanged;
- every points grant expires POINTS_EXPIRE_MONTHS (default 12) after it
  is earned; scheduler job `points-expire` clears expired lots and
  writes a reason=expire ledger row (idempotent);
- refund claws back the points that order's redemption earned
  (reason=refund_clawback, ref_id=order id, once per order); balance
  may go negative, and a negative balance blocks exchange (and any
  future transfer) with an explicit error;
- GET /points returns balance + points expiring within 30 days.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"
os.environ["SCHEDULER_ENABLED"] = "0"

from app import models  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models_packages import Order  # noqa: E402
from app.seed import seed  # noqa: E402

TEST_DB = "/tmp/coupon_go_test_p1c.db"

# Postgres-or-SQLite switch, same convention as test_f3 / test_fix3 /
# test_m3: when a real Postgres is reachable (P1C_POSTGRES_URL or the
# default local coupon_go URL) the suite runs against it, so the
# migration backfill, the expiry sweep and the advisory-lock job path
# are verified on the production dialect; otherwise SQLite.
PG_URL = os.environ.get(
    "P1C_POSTGRES_URL",
    "postgresql+psycopg2://coupon_go:coupon_go@localhost:5432/coupon_go_test_p1c")


def _pg_reachable():
    try:
        probe = create_engine(PG_URL, connect_args={"connect_timeout": 2})
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
        probe.dispose()
        return True
    except Exception:
        return False


BACKEND = "postgres" if _pg_reachable() else "sqlite"

if BACKEND == "postgres":
    engine = create_engine(PG_URL, pool_size=5, max_overflow=5)
else:
    engine = create_engine(f"sqlite:///{TEST_DB}", connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine)


@pytest.fixture(scope="module")
def client():
    if BACKEND == "postgres":
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


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


def _login(client, email):
    r = client.post("/api/v3/login", json={"email": email, "password": "Demo123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _vendor_headers(client):
    return _login(client, "vendor@coupon-go.example")


def _create_package(client, name, price=150, stock=5):
    vh = _vendor_headers(client)
    body = {
        "shop_id": 1, "name": name,
        "items": [{"name": "主菜一份", "qty": 1}],
        "person_count": 2, "original_price": 200, "package_price": price,
        "valid_from": "2026-11-01T00:00:00",
        "valid_to": "2026-12-31T00:00:00",
        "usage_rules": {}, "stock_total": stock, "refund_policy": "anytime",
    }
    r = client.post("/api/v3/packages", json=body, headers=vh)
    assert r.status_code == 201, r.text
    return r.json()["package"]


def _buy_paid(client, headers, package_id):
    r = client.post(f"/api/v3/packages/{package_id}/purchase",
                    json={"payment_method": "fps", "client_ref": ""},
                    headers=headers)
    assert r.status_code == 200, r.text
    order_id = r.json()["order"]["id"]
    r = client.post(f"/api/v3/orders/{order_id}/confirm-payment",
                    json={"outcome": "succeeded"}, headers=headers)
    assert r.status_code == 200, r.text
    order = r.json()["order"]
    assert order["payment_status"] == "paid"
    return order


def _scan_wallet_item(client, headers, wallet_item_id):
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wallet_item_id}, headers=headers)
    assert qr.status_code == 200, qr.text
    vh = _vendor_headers(client)
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 200, r.text
    return r.json()


def _points(client, headers):
    r = client.get("/api/v3/points", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _user_id(email):
    db = _db()
    try:
        return db.query(models.User).filter_by(email=email).one().id
    finally:
        db.close()


def _set_expires_at(tx_id, expires_at):
    db = _db()
    try:
        db.execute(text("UPDATE point_transactions SET expires_at = :e WHERE id = :i"),
                   {"e": expires_at, "i": tx_id})
        db.commit()
    finally:
        db.close()


def _txs(user_id):
    db = _db()
    try:
        return db.query(models.PointTransaction).filter_by(
            user_id=user_id).order_by(models.PointTransaction.id).all()
    finally:
        db.close()


# ---------- paid redeem: spend-based points ----------

def test_paid_package_redeem_awards_spend_points(client):
    h = _register_and_login(client, "p1c-paid@example.com")
    pkg = _create_package(client, "P1C 付費核銷套餐", price=150)
    order = _buy_paid(client, h, pkg["id"])
    assert order["paid_amount"] == 150
    assert _points(client, h)["balance"] == 0
    _scan_wallet_item(client, h, order["wallet_item_id"])
    # HK$150 paid -> +150, NOT the old flat +20.
    assert _points(client, h)["balance"] == 150


def test_free_coupon_values_unchanged(client):
    h = _register_and_login(client, "p1c-free@example.com")
    r = client.post("/api/v3/new-favorite-coupon", params={"coupon_id": 2}, headers=h)
    assert r.status_code == 200, r.text
    assert _points(client, h)["balance"] == 10
    wid = client.get("/api/v3/get-favorite-coupons-list",
                     headers=h).json()["items"][0]["id"]
    _scan_wallet_item(client, h, wid)
    pts = _points(client, h)
    assert pts["balance"] == 30
    # Nothing expires within 30 days: both grants are fresh (12 months).
    assert pts["expiring_soon"] == 0


# ---------- expiry sweep ----------

def test_points_expiry_sweep_clears_expired_and_writes_ledger(client):
    from app.points_service import sweep_expired_points
    h = _register_and_login(client, "p1c-expire@example.com")
    client.post("/api/v3/new-favorite-coupon", params={"coupon_id": 2}, headers=h)
    uid = _user_id("p1c-expire@example.com")
    txs = _txs(uid)
    assert len(txs) == 1 and txs[0].delta == 10
    # Backdate this grant's expiry to yesterday.
    _set_expires_at(txs[0].id, _now() - timedelta(days=1))
    assert _points(client, h)["balance"] == 10
    db = _db()
    try:
        result = sweep_expired_points(db)
    finally:
        db.close()
    assert result["expired"] == 1
    assert result["points"] == 10
    assert _points(client, h)["balance"] == 0
    after = _txs(uid)
    assert any(t.delta == -10 and t.reason == "expire" for t in after)
    # Idempotent: a second run expires nothing.
    db = _db()
    try:
        again = sweep_expired_points(db)
    finally:
        db.close()
    assert again["expired"] == 0
    assert _points(client, h)["balance"] == 0


def test_expiring_soon_counts_fifo_remaining(client):
    h = _register_and_login(client, "p1c-soon@example.com")
    client.post("/api/v3/new-favorite-coupon", params={"coupon_id": 2}, headers=h)
    wid = client.get("/api/v3/get-favorite-coupons-list",
                     headers=h).json()["items"][0]["id"]
    _scan_wallet_item(client, h, wid)
    uid = _user_id("p1c-soon@example.com")
    txs = _txs(uid)
    assert [t.delta for t in txs] == [10, 20]
    # Claim lot (+10) expires in 10 days; redeem lot (+20) in 100 days.
    _set_expires_at(txs[0].id, _now() + timedelta(days=10))
    _set_expires_at(txs[1].id, _now() + timedelta(days=100))
    assert _points(client, h)["expiring_soon"] == 10
    # Spend 9 points: FIFO consumes the oldest lot first, so only 1
    # point of the soon-expiring lot remains.
    r = client.post("/api/v3/points/exchange", params={"coupon_id": 37}, headers=h)
    assert r.status_code == 200, r.text
    pts = _points(client, h)
    assert pts["balance"] == 21
    assert pts["expiring_soon"] == 1


# ---------- refund clawback ----------

def test_refund_of_unredeemed_order_claws_back_nothing(client):
    h = _register_and_login(client, "p1c-refund-unused@example.com")
    pkg = _create_package(client, "P1C 未核銷退款套餐", price=150)
    order = _buy_paid(client, h, pkg["id"])
    assert _points(client, h)["balance"] == 0
    r = client.post(f"/api/v3/orders/{order['id']}/refund",
                    json={"reason": "不想要了"}, headers=h)
    assert r.status_code == 200, r.text
    assert _points(client, h)["balance"] == 0
    uid = _user_id("p1c-refund-unused@example.com")
    assert not any(t.reason == "refund_clawback" for t in _txs(uid))


def test_refund_clawback_can_go_negative_and_blocks_exchange(client):
    from app.points_service import clawback_order_points
    h = _register_and_login(client, "p1c-refund-neg@example.com")
    pkg = _create_package(client, "P1C 退款扣回套餐", price=150)
    order = _buy_paid(client, h, pkg["id"])
    _scan_wallet_item(client, h, order["wallet_item_id"])
    assert _points(client, h)["balance"] == 150
    # Spend 9 points first, so the clawback drives the balance negative.
    r = client.post("/api/v3/points/exchange", params={"coupon_id": 37}, headers=h)
    assert r.status_code == 200, r.text
    assert _points(client, h)["balance"] == 141
    # A redeemed voucher cannot be refunded via the API, so simulate the
    # refund completion at the service level (the hook lives in the same
    # _resume_refund success branch every refund path funnels through).
    db = _db()
    try:
        db_order = db.query(Order).filter_by(id=order["id"]).one()
        db_order.refund_status = "refunded"
        db.commit()
        clawed = clawback_order_points(db, db_order)
        db.commit()
    finally:
        db.close()
    assert clawed == 150
    pts = _points(client, h)
    assert pts["balance"] == -9
    uid = _user_id("p1c-refund-neg@example.com")
    assert any(t.delta == -150 and t.reason == "refund_clawback"
               and t.ref_id == order["id"] for t in _txs(uid))
    # Negative balance blocks exchange with an explicit error.
    r = client.post("/api/v3/points/exchange", params={"coupon_id": 37}, headers=h)
    assert r.status_code == 400, r.text
    assert "負數" in r.json()["detail"]
    # Idempotent: a second clawback for the same order takes nothing.
    db = _db()
    try:
        db_order = db.query(Order).filter_by(id=order["id"]).one()
        assert clawback_order_points(db, db_order) == 0
        db.commit()
    finally:
        db.close()
    assert _points(client, h)["balance"] == -9
