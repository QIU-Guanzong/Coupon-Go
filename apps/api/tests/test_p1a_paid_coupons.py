"""P1-A (付费券） tests: merchant prices a coupon -> user buys it through
the shared package order / payment / refund chain -> voucher in Q Wallet.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_p1a_paid_coupons.py -v
RUN MODE — same pattern as test_fix3_row_locks.py:
  * Postgres (default when reachable): ``P1A_POSTGRES_URL``, default
    ``postgresql+psycopg2://coupon_go:coupon_go@localhost:5432/coupon_go_test_p1a``.
    This is the real verification: ``SELECT ... FOR UPDATE`` only
    exists on Postgres, and SQLite silently ignores it.
  * SQLite fallback (only when no Postgres is reachable, or when
    ``P1A_FORCE_SQLITE=1``): a throwaway SQLite file with
    ``BEGIN IMMEDIATE`` per transaction, so concurrent tests serialise
    at the DB level — same serialisation a row lock gives on Postgres.
"""
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import models  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402
from tests.db_isolation import sqlite_test_path  # noqa: E402

PG_URL = os.environ.get(
    "P1A_POSTGRES_URL",
    "postgresql+psycopg2://coupon_go:coupon_go@localhost:5432/coupon_go_test_p1a")
SQLITE_DB = sqlite_test_path("coupon_go_test_p1a.db")


def _pg_reachable():
    if os.environ.get("P1A_FORCE_SQLITE", "") == "1":
        return False
    try:
        probe = create_engine(PG_URL, connect_args={"connect_timeout": 2})
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
        probe.dispose()
        return True
    except Exception:
        return False


BACKEND = "postgres" if _pg_reachable() else "sqlite-immediate"

if BACKEND == "postgres":
    engine = create_engine(PG_URL, pool_size=5, max_overflow=5)
else:
    engine = create_engine(
        f"sqlite:///{SQLITE_DB}",
        connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "begin")
    def _begin_immediate(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")

TestingSession = sessionmaker(bind=engine, autoflush=False)


@pytest.fixture(scope="module")
def client():
    if BACKEND == "postgres":
        Base.metadata.drop_all(bind=engine)
    elif os.path.exists(SQLITE_DB):
        os.remove(SQLITE_DB)
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


def _run_two(fn):
    """Run fn(thread_index, test_client) in two threads at the same
    time; each thread uses its own TestClient. Returns the two results
    in launch order."""
    barrier = threading.Barrier(2)
    results = [None, None]
    exceptions = [None, None]

    def worker(i):
        try:
            tc = TestClient(app)
            barrier.wait(timeout=30)
            results[i] = fn(i, tc)
        except BaseException as exc:
            exceptions[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert all(t.is_alive() is False for t in threads), "concurrent worker hung"
    for exc in exceptions:
        if exc is not None:
            raise exc
    return results


def _sleeping_wrapper(fn, seconds=0.4):
    """Widen a check-then-write race window: sleep, then run fn."""
    def wrapped(*args, **kwargs):
        time.sleep(seconds)
        return fn(*args, **kwargs)
    return wrapped


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


def _create_coupon(client, name="午市 8 折券", sale_price=20, refund_policy="anytime",
                   total=5, discount_price=80, regular_price=100):
    """Vendor creates a coupon (pending_review) and an admin approves it."""
    vh = _vendor_headers(client)
    body = {
        "shop_id": 1, "coupon_type": "Price", "coupon_name": name,
        "discount_price": discount_price, "regular_price": regular_price,
        "valid_from": "2026-11-01T00:00:00",
        "valid_to": "2026-12-31T00:00:00",
        "total": total, "sale_price": sale_price,
        "refund_policy": refund_policy,
    }
    r = client.post("/api/v3/coupon-previewed", json=body, headers=vh)
    assert r.status_code == 200, r.text
    coupon = r.json()["coupon"]
    assert coupon["sale_price"] == sale_price
    ah = _admin_headers(client)
    r = client.post(f"/api/v3/vendor/coupons/{coupon['id']}/approve",
                    params={"approve": True}, headers=ah)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"
    return coupon


def _remaining(client, coupon_id):
    db = TestingSession()
    c = db.get(models.Coupon, coupon_id)
    remaining = c.remaining
    db.close()
    return remaining


def _purchase(client, headers, coupon_id, method="fps", client_ref="",
              quantity=None):
    body = {"payment_method": method, "client_ref": client_ref}
    if quantity is not None:
        body["quantity"] = quantity
    r = client.post(f"/api/v3/coupons/{coupon_id}/purchase",
                    json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _pay(client, headers, order_id, outcome="succeeded", payment_id=None):
    body = {"outcome": outcome}
    if payment_id is not None:
        body["payment_id"] = payment_id
    r = client.post(f"/api/v3/orders/{order_id}/confirm-payment",
                    json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["order"]


def _buy_paid(client, headers, coupon_id, method="fps", client_ref=""):
    bought = _purchase(client, headers, coupon_id, method, client_ref)
    order = _pay(client, headers, bought["order"]["id"], "succeeded")
    assert order["payment_status"] == "paid"
    return bought, order


# ---------- merchant pricing ----------
def test_vendor_create_paid_coupon_and_free_coupon_default(client):
    paid = _create_coupon(client, name="定價付費券", sale_price=20)
    assert paid["sale_price"] == 20
    assert paid["is_paid"] is True
    assert paid["refund_policy"] == "anytime"
    free = _create_coupon(client, name="免費券預設", sale_price=None)
    assert free["sale_price"] is None
    assert free["is_paid"] is False


def test_vendor_create_paid_coupon_bad_price_or_policy(client):
    vh = _vendor_headers(client)
    base = {"shop_id": 1, "coupon_type": "Price", "coupon_name": "壞價券",
            "valid_from": "2026-11-01T00:00:00",
            "valid_to": "2026-12-31T00:00:00", "total": 5}
    r = client.post("/api/v3/coupon-previewed",
                    json={**base, "sale_price": -5}, headers=vh)
    assert r.status_code == 422
    r = client.post("/api/v3/coupon-previewed",
                    json={**base, "sale_price": 10, "refund_policy": "never"},
                    headers=vh)
    # Bad refund_policy is a 400 by the same convention as POST /packages.
    assert r.status_code == 400


# ---------- free claim guard ----------
def test_paid_coupon_cannot_be_claimed_free(client):
    coupon = _create_coupon(client, name="不可免費領付費券", sale_price=20)
    h = _register_and_login(client, "p1a-claim@example.com")
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": coupon["id"]}, headers=h)
    assert r.status_code == 400
    assert "購買" in r.text


def test_free_coupon_claim_still_works_and_cannot_be_purchased(client):
    coupon = _create_coupon(client, name="免費券仍可領", sale_price=None)
    h = _register_and_login(client, "p1a-free@example.com")
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": coupon["id"]}, headers=h)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/v3/coupons/{coupon['id']}/purchase",
                    json={"payment_method": "fps"}, headers=h)
    assert r.status_code == 400
    assert "領取" in r.text


# ---------- purchase -> pay -> wallet ----------
def test_purchase_pay_voucher_wallet_chain(client):
    h = _register_and_login(client, "p1a-buyer@example.com")
    coupon = _create_coupon(client, name="購買鏈路付費券", sale_price=20, total=5)
    before = _remaining(client, coupon["id"])
    bought = _purchase(client, h, coupon["id"], method="fps",
                       client_ref="p1a-chain-001")
    order = bought["order"]
    assert order["order_type"] == "coupon"
    assert order["coupon_id"] == coupon["id"]
    assert order["coupon_name"] == "購買鏈路付費券"
    assert order["payment_status"] == "pending"
    assert order["amount"] == 20 and order["payment_method"] == "fps"
    assert bought["payment"]["status"] == "requires_payment"
    assert bought["payment"]["gateway_ref"] == ""
    assert bought["gateway"] == "stripe"
    assert _remaining(client, coupon["id"]) == before - 1  # stock reserved

    paid = _pay(client, h, order["id"], "succeeded")
    assert paid["payment_status"] == "paid"
    assert paid["paid_amount"] == 20
    assert paid["settlement_status"] == "pending"
    assert paid["net_amount"] == 20 and paid["commission_amount"] == 0
    assert paid["wallet_item_id"] is not None

    wallet = client.get("/api/v3/get-favorite-coupons-list",
                        params={"status": 1}, headers=h).json()
    mine = [i for i in wallet["items"] if i["id"] == paid["wallet_item_id"]]
    assert len(mine) == 1
    assert mine[0]["source"] == "purchase"
    assert mine[0]["order_id"] == paid["id"]
    assert mine[0]["coupon_id"] == coupon["id"]
    assert mine[0]["status"] == 1

    detail = client.get(f"/api/v3/orders/{paid['id']}", headers=h).json()["order"]
    assert detail["coupon_name"] == "購買鏈路付費券"
    assert len(detail["payments"]) == 1


def test_purchase_quantity_gt_1_rejected(client):
    h = _register_and_login(client, "p1a-qty@example.com")
    coupon = _create_coupon(client, name="限購一張券", sale_price=20)
    r = client.post(f"/api/v3/coupons/{coupon['id']}/purchase",
                    json={"payment_method": "fps", "quantity": 2}, headers=h)
    assert r.status_code == 400


def test_purchase_idempotent_client_ref(client):
    h = _register_and_login(client, "p1a-idem@example.com")
    coupon = _create_coupon(client, name="冪等付費券", sale_price=20, total=5)
    before = _remaining(client, coupon["id"])
    first = _purchase(client, h, coupon["id"], client_ref="p1a-idem-001")
    second = _purchase(client, h, coupon["id"], client_ref="p1a-idem-001")
    assert first["order"]["id"] == second["order"]["id"]
    assert _remaining(client, coupon["id"]) == before - 1  # reserved once


def test_purchase_sold_out_and_package_template_blocked(client):
    h = _register_and_login(client, "p1a-soldout@example.com")
    coupon = _create_coupon(client, name="只剩一張券", sale_price=20, total=1)
    _purchase(client, h, coupon["id"], client_ref="p1a-sold-001")
    r = client.post(f"/api/v3/coupons/{coupon['id']}/purchase",
                    json={"payment_method": "fps"}, headers=h)
    assert r.status_code == 400  # remaining 0
    # A package voucher template (kind='package') is bought via packages, not here.
    db = TestingSession()
    template = (db.query(models.Coupon)
                .filter(models.Coupon.kind == "package").first())
    db.close()
    assert template is not None
    r = client.post(f"/api/v3/coupons/{template.id}/purchase",
                    json={"payment_method": "fps"}, headers=h)
    assert r.status_code == 400


# ---------- redeem via scan ----------
def test_purchased_coupon_redeem_via_scan(client):
    h = _register_and_login(client, "p1a-redeem@example.com")
    coupon = _create_coupon(client, name="可核銷付費券", sale_price=20)
    _, paid = _buy_paid(client, h, coupon["id"])
    wid = paid["wallet_item_id"]
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=h)
    assert qr.status_code == 200, qr.text
    vh = _vendor_headers(client)
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 200, r.text
    wallet = client.get("/api/v3/get-favorite-coupons-list",
                        params={"status": 2}, headers=h).json()
    assert any(i["id"] == wid for i in wallet["items"])
    # Settlement basis of the redemption is the amount actually paid.
    db = TestingSession()
    red = (db.query(models.Redemption)
           .filter(models.Redemption.wallet_item_id == wid).first())
    assert red is not None and float(red.face_value) == 20
    db.close()


# ---------- failure / timeout / retry / cancel ----------
def test_payment_failure_retry_then_success(client):
    h = _register_and_login(client, "p1a-retry@example.com")
    coupon = _create_coupon(client, name="重試付費券", sale_price=20, total=5)
    before = _remaining(client, coupon["id"])
    bought = _purchase(client, h, coupon["id"], method="card")
    order = _pay(client, h, bought["order"]["id"], "failed")
    assert order["payment_status"] == "failed"
    assert _remaining(client, coupon["id"]) == before  # stock released
    r = client.post(f"/api/v3/orders/{order['id']}/retry-payment",
                    json={"payment_method": "fps"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["order"]["payment_status"] == "pending"
    assert _remaining(client, coupon["id"]) == before - 1
    paid = _pay(client, h, order["id"], "succeeded")
    assert paid["payment_status"] == "paid"
    assert paid["wallet_item_id"] is not None
    detail = client.get(f"/api/v3/orders/{order['id']}",
                        headers=h).json()["order"]
    assert len(detail["payments"]) == 2


def test_payment_timeout_releases_stock_on_read(client):
    h = _register_and_login(client, "p1a-timeout@example.com")
    coupon = _create_coupon(client, name="逾時付費券", sale_price=20, total=5)
    before = _remaining(client, coupon["id"])
    bought = _purchase(client, h, coupon["id"])
    # Age the open attempt past PAYMENT_TIMEOUT_MINUTES, then read the order:
    # the lazy timeout must fire and release the reservation.
    from app.models_packages import Payment
    db = TestingSession()
    attempt = (db.query(Payment)
               .filter(Payment.order_id == bought["order"]["id"]).first())
    attempt.created_at = (datetime.now(timezone.utc).replace(tzinfo=None)
                          - timedelta(minutes=16))
    db.commit()
    db.close()
    order = client.get(f"/api/v3/orders/{bought['order']['id']}",
                       headers=h).json()["order"]
    assert order["payment_status"] == "timeout"
    assert _remaining(client, coupon["id"]) == before


def test_payment_timeout_outcome_and_cancel(client):
    h = _register_and_login(client, "p1a-timeout2@example.com")
    coupon = _create_coupon(client, name="逾時取消付費券", sale_price=20, total=5)
    before = _remaining(client, coupon["id"])
    bought = _purchase(client, h, coupon["id"])
    order = _pay(client, h, bought["order"]["id"], "timeout")
    assert order["payment_status"] == "timeout"
    assert _remaining(client, coupon["id"]) == before
    r = client.post(f"/api/v3/orders/{order['id']}/cancel", headers=h)
    assert r.status_code == 400  # only pending orders cancel

    bought2 = _purchase(client, h, coupon["id"], client_ref="p1a-cancel-001")
    assert _remaining(client, coupon["id"]) == before - 1
    r = client.post(f"/api/v3/orders/{bought2['order']['id']}/cancel", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["order"]["payment_status"] == "cancelled"
    assert _remaining(client, coupon["id"]) == before


# ---------- webhook idempotency ----------
def test_webhook_succeeded_issues_voucher_and_replay_ignored(client):
    h = _register_and_login(client, "p1a-webhook@example.com")
    coupon = _create_coupon(client, name="Webhook 付費券", sale_price=20)
    bought = _purchase(client, h, coupon["id"], method="fps")
    from app.models_packages import Payment
    db = TestingSession()
    pay = (db.query(Payment)
           .filter(Payment.order_id == bought["order"]["id"]).first())
    ref = pay.gateway_ref
    db.close()
    assert ref.startswith("pi_test_")
    payload = {"type": "payment_intent.succeeded",
               "data": {"object": {"id": ref}}}
    r = client.post("/api/v3/payments/webhook", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "succeeded"
    r2 = client.post("/api/v3/payments/webhook", json=payload)
    assert r2.status_code == 200, r2.text
    assert r2.json().get("ignored") == "order already settled"
    order = client.get(f"/api/v3/orders/{bought['order']['id']}",
                       headers=h).json()["order"]
    assert order["payment_status"] == "paid"
    assert order["wallet_item_id"] is not None
    db = TestingSession()
    items = (db.query(models.WalletItem)
             .filter(models.WalletItem.order_id == bought["order"]["id"]).all())
    db.close()
    assert len(items) == 1  # exactly one voucher despite the replay


# ---------- concurrency / backstops ----------
def test_concurrent_purchase_last_unit_only_one_succeeds__row_lock(client, monkeypatch):
    import app.routers.packages as pkg_router
    coupon = _create_coupon(client, name="併發最後一張券", sale_price=20, total=1)
    h1 = _register_and_login(client, "p1a-race-a@example.com")
    h2 = _register_and_login(client, "p1a-race-b@example.com")
    # Widen the stock-check -> decrement window.
    monkeypatch.setattr(
        pkg_router, "_check_coupon_reservation",
        _sleeping_wrapper(pkg_router._check_coupon_reservation, seconds=0.4))
    headers = [h1, h2]
    results = _run_two(lambda i, tc: tc.post(
        f"/api/v3/coupons/{coupon['id']}/purchase",
        json={"payment_method": "fps", "client_ref": ""},
        headers=headers[i]))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 400], \
        f"[{BACKEND}] expected exactly one 200 + one 400, got {statuses}: " \
        f"{[r.text for r in results]}"
    db = TestingSession()
    try:
        row = db.get(models.Coupon, coupon["id"])
        assert row.remaining == 0, f"[{BACKEND}] remaining={row.remaining}"
        from app.models_packages import Order
        orders = db.query(Order).filter(
            Order.coupon_id == coupon["id"],
            Order.payment_status != "cancelled").all()
        assert len(orders) == 1, f"[{BACKEND}] {len(orders)} orders stored"
    finally:
        db.close()


def test_remaining_cannot_go_negative__db_constraint(client):
    """Backstop: CHECK (remaining >= 0) fires on a direct conflicting
    write (both backends, no serialisation help)."""
    coupon = _create_coupon(client, name="庫存下限付費券", sale_price=20, total=1)
    db = TestingSession()
    try:
        row = db.get(models.Coupon, coupon["id"])
        row.remaining = -1
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    finally:
        db.close()


# ---------- refunds ----------
def test_refund_unused_voids_voucher_and_restores_stock(client):
    h = _register_and_login(client, "p1a-refund@example.com")
    coupon = _create_coupon(client, name="退款付費券", sale_price=20, total=5)
    _, paid = _buy_paid(client, h, coupon["id"])
    wid = paid["wallet_item_id"]
    before = _remaining(client, coupon["id"])
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=h)
    assert qr.status_code == 200
    r = client.post(f"/api/v3/orders/{paid['id']}/refund",
                    json={"reason": "行程有變"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["refund"]["status"] == "refunded"
    assert r.json()["refund"]["gateway_ref"].startswith("re_test_")
    assert r.json()["order"]["refund_status"] == "refunded"
    assert r.json()["order"]["settlement_status"] == "void"
    wallet = client.get("/api/v3/get-favorite-coupons-list",
                        params={"status": 3}, headers=h).json()
    assert any(i["id"] == wid for i in wallet["items"])  # 券已作廢
    assert _remaining(client, coupon["id"]) == before + 1
    vh = _vendor_headers(client)
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 400  # refunded voucher can never be redeemed
    r = client.post(f"/api/v3/orders/{paid['id']}/refund",
                    json={"reason": "再退一次"}, headers=h)
    assert r.status_code == 400


def test_refund_via_coupon_path(client):
    h = _register_and_login(client, "p1a-refund2@example.com")
    coupon = _create_coupon(client, name="按券退款", sale_price=20)
    _, paid = _buy_paid(client, h, coupon["id"])
    r = client.post(f"/api/v3/coupons/{coupon['id']}/refund",
                    json={"reason": "不想去了"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["order"]["id"] == paid["id"]
    assert r.json()["order"]["refund_status"] == "refunded"


def test_non_refundable_paid_coupon_refund_blocked(client):
    h = _register_and_login(client, "p1a-norefund@example.com")
    coupon = _create_coupon(client, name="特價不退券", sale_price=20,
                            refund_policy="non_refundable")
    _, paid = _buy_paid(client, h, coupon["id"])
    r = client.post(f"/api/v3/orders/{paid['id']}/refund",
                    json={"reason": "想退"}, headers=h)
    assert r.status_code == 400
    assert "不設退款" in r.text


def test_expiry_only_manual_refund_blocked_then_auto_refunded(client):
    h = _register_and_login(client, "p1a-expireonly@example.com")
    coupon = _create_coupon(client, name="僅過期退付費券", sale_price=20,
                            refund_policy="expiry_only")
    _, paid = _buy_paid(client, h, coupon["id"])
    r = client.post(f"/api/v3/orders/{paid['id']}/refund",
                    json={"reason": "未過期想退"}, headers=h)
    assert r.status_code == 400
    db = TestingSession()
    c = db.get(models.Coupon, coupon["id"])
    c.valid_to = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    db.commit()
    db.close()
    ah = _admin_headers(client)
    r = client.post("/api/v3/orders/process-expired-refunds", headers=ah)
    assert r.status_code == 200
    order = client.get(f"/api/v3/orders/{paid['id']}",
                       headers=h).json()["order"]
    assert order["refund_status"] == "refunded"


def test_refund_used_voucher_blocked(client):
    h = _register_and_login(client, "p1a-usedrefund@example.com")
    coupon = _create_coupon(client, name="已用退款攔截", sale_price=20)
    _, paid = _buy_paid(client, h, coupon["id"])
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": paid["wallet_item_id"]},
                     headers=h).json()
    vh = _vendor_headers(client)
    assert client.post("/api/v3/new-scan-coupon",
                       json={"code": qr["code"]}, headers=vh).status_code == 200
    r = client.post(f"/api/v3/orders/{paid['id']}/refund",
                    json={"reason": "已用還想退"}, headers=h)
    assert r.status_code == 400


def test_expired_paid_coupon_auto_refund(client):
    h = _register_and_login(client, "p1a-expire@example.com")
    coupon = _create_coupon(client, name="過期自動退付費券", sale_price=20,
                            refund_policy="anytime")
    _, paid = _buy_paid(client, h, coupon["id"])
    db = TestingSession()
    c = db.get(models.Coupon, coupon["id"])
    c.valid_to = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    db.commit()
    db.close()
    ah = _admin_headers(client)
    r = client.post("/api/v3/orders/process-expired-refunds", headers=ah)
    assert r.status_code == 200, r.text
    assert r.json()["refunded"] >= 1
    order = client.get(f"/api/v3/orders/{paid['id']}",
                       headers=h).json()["order"]
    assert order["refund_status"] == "refunded"
    wallet = client.get("/api/v3/get-favorite-coupons-list",
                        params={"status": 3}, headers=h).json()
    assert any(i["id"] == paid["wallet_item_id"] for i in wallet["items"])


def test_settlement_basis_refunded_paid_coupon_is_none(client):
    """M6 rule via the shared function: a refunded paid-coupon order
    never settles, even though the wallet item still links the order."""
    from decimal import Decimal
    from app.settlement_basis import settlement_basis
    h = _register_and_login(client, "p1a-basis@example.com")
    coupon = _create_coupon(client, name="結算口徑付費券", sale_price=20)
    _, paid = _buy_paid(client, h, coupon["id"])
    db = TestingSession()
    item = db.get(models.WalletItem, paid["wallet_item_id"])
    assert settlement_basis(db, item.coupon, wallet_item=item) == Decimal("20")
    db.close()
    assert client.post(f"/api/v3/orders/{paid['id']}/refund",
                       json={"reason": "退"}, headers=h).status_code == 200
    db = TestingSession()
    item = db.get(models.WalletItem, paid["wallet_item_id"])
    assert settlement_basis(db, item.coupon, wallet_item=item) is None
    db.close()


@pytest.mark.parametrize("phase", ["pending", "failed", "timeout", "paid", "refunded"])
def test_paid_coupon_order_prevents_rewriting_its_terms(client, phase):
    h = _register_and_login(client, f"p1a-terms-{phase}@example.com")
    coupon = _create_coupon(client, name=f"已訂購條款 {phase}", sale_price=20, total=5)
    oid = _purchase(client, h, coupon["id"])["order"]["id"]
    if phase in ("failed", "timeout"):
        _pay(client, h, oid, phase)
    elif phase in ("paid", "refunded"):
        _pay(client, h, oid)
        if phase == "refunded":
            assert client.post(f"/api/v3/orders/{oid}/refund", json={}, headers=h).status_code == 200
    edited = client.patch(f"/api/v3/vendor/coupons/{coupon['id']}",
                          json={"refund_policy": "non_refundable", "discount_price": 1},
                          headers=_vendor_headers(client))
    assert edited.status_code == 409, edited.text
    with TestingSession() as db:
        row = db.get(models.Coupon, coupon["id"])
        assert row.refund_policy == "anytime" and row.discount_price == 80
        assert db.get(models.Order, oid).amount == 20


def test_paid_coupon_remains_redeemable_when_merchant_pauses_listing(client):
    h = _register_and_login(client, "p1a-paused-purchased@example.com")
    coupon = _create_coupon(client, name="已售券暫停上架", sale_price=20)
    _, paid = _buy_paid(client, h, coupon["id"])
    paused = client.post(f"/api/v3/vendor/coupons/{coupon['id']}/pause", headers=_vendor_headers(client))
    assert paused.status_code == 200, paused.text
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": paid["wallet_item_id"]}, headers=h)
    assert qr.status_code == 200, qr.text
    scan = client.post("/api/v3/new-scan-coupon", json={"code": qr.json()["code"]},
                       headers=_vendor_headers(client))
    assert scan.status_code == 200, scan.text


def test_coupon_edit_cannot_race_past_paid_order_reservation(client):
    h = _register_and_login(client, "p1a-edit-race@example.com")
    coupon = _create_coupon(client, name="訂購編輯競態", sale_price=20)
    vh = _vendor_headers(client)
    def submit(index, tc):
        if index == 0:
            return tc.post(f"/api/v3/coupons/{coupon['id']}/purchase",
                           json={"payment_method": "fps"}, headers=h)
        return tc.patch(f"/api/v3/vendor/coupons/{coupon['id']}",
                        json={"refund_policy": "non_refundable"}, headers=vh)
    purchase, edit = _run_two(submit)
    if purchase.status_code == 200:
        assert edit.status_code == 409, edit.text
        with TestingSession() as db:
            assert db.get(models.Coupon, coupon["id"]).refund_policy == "anytime"
    else:
        assert purchase.status_code == 400 and edit.status_code == 200, (purchase.text, edit.text)
        with TestingSession() as db:
            assert db.get(models.Coupon, coupon["id"]).refund_policy == "non_refundable"


def test_paid_coupon_cancel_then_late_payment_refunded_once_without_restocking_twice(client):
    from app.models_packages import LatePaymentRefund, Payment
    h = _register_and_login(client, "p1a-cancel-late-success@example.com")
    coupon = _create_coupon(client, name="取消後遲到付款", sale_price=20, total=5)
    oid = _purchase(client, h, coupon["id"])["order"]["id"]
    with TestingSession() as db:
        gateway_ref = db.query(Payment).filter_by(order_id=oid).one().gateway_ref
    assert client.post(f"/api/v3/orders/{oid}/cancel", headers=h).status_code == 200
    payload = {"type": "payment_intent.succeeded", "data": {"object": {"id": gateway_ref}}}
    for _ in range(2):
        response = client.post("/api/v3/payments/webhook", json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["late_payment_refund"]["status"] == "refunded"
    with TestingSession() as db:
        assert db.get(models.Coupon, coupon["id"]).remaining == 5
        assert db.get(models.Order, oid).payment_status == "cancelled"
        assert db.query(models.WalletItem).filter_by(order_id=oid).count() == 0
        assert db.query(LatePaymentRefund).filter_by(order_id=oid, status="refunded").count() == 1


def test_sold_out_paid_coupon_becomes_sellable_again_after_refund(client):
    h = _register_and_login(client, "p1a-refund-reactivate@example.com")
    coupon = _create_coupon(client, name="退款恢復發售", sale_price=20, total=1)
    _, paid = _buy_paid(client, h, coupon["id"])
    with TestingSession() as db:
        row = db.get(models.Coupon, coupon["id"])
        assert row.remaining == 0 and row.status == "sold_out"
    response = client.post(f"/api/v3/orders/{paid['id']}/refund", json={}, headers=h)
    assert response.status_code == 200, response.text
    with TestingSession() as db:
        row = db.get(models.Coupon, coupon["id"])
        assert row.remaining == 1 and row.status == "active"
