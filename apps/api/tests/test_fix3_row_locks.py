"""FIX-3 tests: row locks on money / stock paths.

What this file proves: every check-then-write on a money or stock path
holds the row lock while it re-checks, so two concurrent requests cannot
both pass the check.

RUN MODE (how the concurrency tests run) — read this before re-running:
  * Postgres (default when reachable): the tests run against a real
    PostgreSQL database (``FIX3_POSTGRES_URL``, default
    ``postgresql://coupon_go:coupon_go@localhost:5432/coupon_go_test_fix3``,
    the same credentials as ``infra/docker-compose.yml`` db). This is the
    real verification: ``SELECT ... FOR UPDATE`` only exists on
    Postgres, and SQLite silently ignores it.
  * SQLite fallback (only when no Postgres is reachable, or when
    ``FIX3_FORCE_SQLITE=1``): the tests run against a throwaway SQLite
    file, and the test engine issues ``BEGIN IMMEDIATE`` for every
    transaction so the two concurrent transactions serialise at the DB
    level — the same serialisation a row lock gives on Postgres. The
    fallback therefore verifies the locked re-check logic and the
    CHECK / UNIQUE backstops, but it does NOT by itself prove that
    ``with_for_update()`` is present: run the Postgres mode (docker
    compose ``db``, or a local ``service postgresql start``) for that.
  * The DB-level backstop tests (``test_*__db_constraint``) run on both
    backends with no serialisation help: a CHECK / UNIQUE violation must
    fire on a direct conflicting write.

Concurrency tests are named ``test_concurrent_*__row_lock`` and widen
the race window on purpose (a short sleep inside the check-then-write
section, via monkeypatch), so a missing lock fails reliably instead of
by timing luck. Assertions are never weakened for the fallback: exactly
one request succeeds, the other is rejected (400/409), and the final
stored state satisfies the invariant (balance / stock / gateway calls /
invoiced total).

Run:  cd apps/api && ~/workspace/coupon-go-fullstack/.venv/bin/python \\
          -m pytest tests/test_fix3_row_locks.py -v
Uses its own throwaway DB; never touches production data.
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import models, models_bargain, models_cashback  # noqa: E402,F401
from app.database import Base, get_db  # noqa: E402
from app.deps import points_balance  # noqa: E402
from app.main import app  # noqa: E402
from app.models_line_f import Invoice, Settlement  # noqa: E402,F401
from app.models_packages import Order, Package, Refund  # noqa: E402,F401
from app.seed import seed  # noqa: E402

PG_URL = os.environ.get(
    "FIX3_POSTGRES_URL",
    # NOTE: explicit +psycopg2 driver — this venv ships psycopg2-binary
    # only; a bare postgresql:// URL makes SQLAlchemy 2.x look for
    # psycopg (v3) and fail to import.
    "postgresql+psycopg2://coupon_go:coupon_go@localhost:5432/coupon_go_test_fix3")
SQLITE_DB = "/tmp/coupon_go_test_fix3.db"


def _pg_reachable():
    if os.environ.get("FIX3_FORCE_SQLITE", "") == "1":
        return False
    try:
        probe = create_engine(PG_URL, connect_args={"connect_timeout": 2})
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
        probe.dispose()
        return True
    except Exception:
        return False


# BACKEND is reported in every concurrency assertion message so a
# failure shows which run mode it ran in.
BACKEND = "postgres" if _pg_reachable() else "sqlite-immediate"

if BACKEND == "postgres":
    engine = create_engine(PG_URL, pool_size=5, max_overflow=5)
else:
    engine = create_engine(
        f"sqlite:///{SQLITE_DB}",
        connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "begin")
    def _begin_immediate(conn):
        # SQLite ignores with_for_update(): serialise whole transactions
        # instead, so the concurrent tests exercise the same locked
        # re-check logic. See the module docstring.
        conn.exec_driver_sql("BEGIN IMMEDIATE")


TestingSession = sessionmaker(bind=engine)

PERIOD = datetime.now(timezone.utc).strftime("%Y-%m")
# Seed cash coupons (id -> (shop_id, cash_amount)); one shop per test so
# the per-(shop, period) settlement uniqueness never collides.
CASH = {2: (1, 30), 5: (3, 100), 8: (4, 15), 17: (9, 150), 22: (11, 10),
        24: (12, 20)}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


# ---------- shared helpers ----------
def _register_and_login(client, email):
    assert client.post("/api/v3/new-register", json={
        "email": email, "password": "Demo123!", "name": "Test",
        "role": "customer"}).status_code == 200
    code = client.get("/api/v3/_dev/otp", params={"email": email}).json()["code"]
    r = client.post("/api/v3/new-verify-otp", json={
        "email": email, "code": code, "purpose": "register"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _login(client, email):
    r = client.post("/api/v3/login", json={
        "email": email, "password": "Demo123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _vendor_headers(client):
    return _login(client, "vendor@coupon-go.example")


def _finance_headers(client):
    return _login(client, "finance@coupon-go.example")


def _run_two(fn):
    """Run fn(thread_index, test_client) in two threads at the same
    time; each thread uses its own TestClient (one client per thread —
    TestClient is not shared across threads). Returns the two results
    in launch order."""
    barrier = threading.Barrier(2)
    results = [None, None]

    def worker(i):
        tc = TestClient(app)
        barrier.wait(timeout=30)
        results[i] = fn(i, tc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert all(t.is_alive() is False for t in threads), "concurrent worker hung"
    return results


def _sleeping_wrapper(fn, seconds=0.4):
    """Widen a check-then-write race window: sleep, then run fn."""
    def wrapped(*args, **kwargs):
        time.sleep(seconds)
        return fn(*args, **kwargs)
    return wrapped


def _redeem(client, email, coupon_id):
    """Register, claim coupon, QR, vendor scan. Returns (headers, wid, rid)."""
    h = _register_and_login(client, email)
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": coupon_id}, headers=h)
    assert r.status_code == 200, r.text
    wid = client.get("/api/v3/get-favorite-coupons-list",
                     headers=h).json()["items"][0]["id"]
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=h)
    assert qr.status_code == 200, qr.text
    vh = _vendor_headers(client)
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 200, r.text
    db = TestingSession()
    red = (db.query(models.Redemption)
           .filter(models.Redemption.wallet_item_id == wid).first())
    db.close()
    assert red is not None
    return h, wid, red.id


def _give_withdrawable(client, email, amount=100):
    """Give the user a withdrawable cashback balance of `amount` by
    inserting one withdrawable transaction against a bare redemption row
    (same pattern as test_cashback._insert_legacy_online_txn — the row
    never went through a scan, so no earning references it yet)."""
    h = _register_and_login(client, email)
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": 1}, headers=h)
    assert r.status_code == 200, r.text
    db = TestingSession()
    try:
        user = db.query(models.User).filter(
            models.User.email == email).first()
        vendor = db.query(models.User).filter(
            models.User.email == "vendor@coupon-go.example").first()
        item = db.query(models.WalletItem).filter(
            models.WalletItem.user_id == user.id).first()
        assert item is not None
        red = models.Redemption(wallet_item_id=item.id,
                                coupon_id=item.coupon_id, shop_id=1,
                                scanned_by_vendor_id=vendor.id)
        db.add(red)
        db.flush()
        db.add(models_cashback.CashbackTransaction(
            user_id=user.id, merchant_id=1, redemption_id=red.id,
            channel="dinein", amount=amount, rate=0.05,
            status="withdrawable", funded_by="merchant_commission"))
        db.commit()
        return h, user.id
    finally:
        db.close()


def _create_package(client, name, stock=1, price=150, original=200):
    vh = _vendor_headers(client)
    body = {
        "shop_id": 1, "name": name,
        "items": [{"name": "主菜一份", "qty": 1}],
        "person_count": 2, "original_price": original, "package_price": price,
        "valid_from": "2026-11-01T00:00:00",
        "valid_to": "2026-12-31T00:00:00",
        "usage_rules": {"need_booking": True},
        "stock_total": stock, "refund_policy": "anytime",
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
    assert r.json()["order"]["payment_status"] == "paid"
    return order_id


def _approved_settlement(client, email, coupon_id, shop_id, rate=0.10):
    """Redeem one cash coupon and generate + approve its settlement.
    Returns the settlement dict."""
    _redeem(client, email, coupon_id)
    fh = _finance_headers(client)
    r = client.post("/api/v3/admin/settlements/generate", json={
        "shop_id": shop_id, "period": PERIOD, "commission_rate": rate},
        headers=fh)
    assert r.status_code == 201, r.text
    s = r.json()["settlement"]
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/approve",
                    headers=fh)
    assert r.status_code == 200, r.text
    return s


def _make_campaign(client, name, stock=1, original=200, floor=100):
    vh = _vendor_headers(client)
    r = client.post("/api/v3/bargain-campaigns", json={
        "shop_id": 1, "name": name, "original_price": original,
        "floor_price": floor, "stock_total": stock, "helper_limit": 5},
        headers=vh)
    assert r.status_code == 200, r.text
    return r.json()["campaign"]


# ---------- 1. concurrent withdraw: exactly one succeeds ----------
def test_concurrent_withdraw_only_one_succeeds__row_lock(client, monkeypatch):
    import app.cashback_service as svc
    h, user_id = _give_withdrawable(client, "fix3-wd@example.com", amount=100)
    # Widen the balance-check -> commit window: money() is called all
    # along the check, the daily-limit sum and the FIFO consume, so a
    # small sleep per call keeps both threads inside the window together.
    monkeypatch.setattr(svc, "money",
                        _sleeping_wrapper(svc.money, seconds=0.05))
    results = _run_two(lambda i, tc: tc.post(
        "/api/v3/cashback/withdraw",
        json={"amount": 60, "method": "fps", "account_name": "Test"},
        headers=h))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 400], \
        f"[{BACKEND}] expected exactly one 200 + one 400, got {statuses}: " \
        f"{[r.text for r in results]}"
    db = TestingSession()
    try:
        rows = db.query(models_cashback.CashbackWithdrawal).filter(
            models_cashback.CashbackWithdrawal.user_id == user_id).all()
        assert len(rows) == 1, f"[{BACKEND}] {len(rows)} withdrawals stored"
        assert float(rows[0].amount) == 60
        bal = svc.balances(db, user_id)
        assert bal["withdrawable_balance"] == 40, bal
    finally:
        db.close()


def test_double_withdraw_sequential_rejected(client):
    h, user_id = _give_withdrawable(client, "fix3-wd-seq@example.com",
                                    amount=100)
    r = client.post("/api/v3/cashback/withdraw",
                    json={"amount": 60, "method": "fps",
                          "account_name": "Test"}, headers=h)
    assert r.status_code == 200, r.text
    r = client.post("/api/v3/cashback/withdraw",
                    json={"amount": 60, "method": "fps",
                          "account_name": "Test"}, headers=h)
    assert r.status_code == 400, r.text  # only 40 left


# ---------- 2. concurrent refund: exactly one gateway refund ----------
def test_concurrent_refund_only_one_gateway_refund__row_lock(client, monkeypatch):
    import app.routers.packages as pkg_router
    h = _register_and_login(client, "fix3-ref@example.com")
    pkg = _create_package(client, "FIX-3 退款併發套餐", stock=5)
    order_id = _buy_paid(client, h, pkg["id"])
    calls = []
    orig = pkg_router.refund_payment

    def counting(ref, amount, currency="HKD"):
        calls.append((ref, amount))
        time.sleep(0.4)  # widen the refund-status check -> gateway window
        return orig(ref, amount)

    monkeypatch.setattr(pkg_router, "refund_payment", counting)
    results = _run_two(lambda i, tc: tc.post(
        f"/api/v3/orders/{order_id}/refund",
        json={"reason": "用户申请退款"}, headers=h))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 400], \
        f"[{BACKEND}] expected exactly one 200 + one 400, got {statuses}: " \
        f"{[r.text for r in results]}"
    assert len(calls) == 1, f"[{BACKEND}] gateway refund called {len(calls)}x"
    db = TestingSession()
    try:
        rows = db.query(Refund).filter(Refund.order_id == order_id).all()
        assert len(rows) == 1, f"[{BACKEND}] {len(rows)} refund rows stored"
        order = db.query(Order).filter(Order.id == order_id).first()
        assert order.refund_status == "refunded"
    finally:
        db.close()


def test_refund_unique_order_reason__db_constraint(client):
    """Backstop: the (order_id, reason) UNIQUE on refunds fires even
    without any lock (both backends, no serialisation help)."""
    h = _register_and_login(client, "fix3-ref-uq@example.com")
    pkg = _create_package(client, "FIX-3 退款唯一套餐", stock=5)
    order_id = _buy_paid(client, h, pkg["id"])
    r = client.post(f"/api/v3/orders/{order_id}/refund",
                    json={"reason": "用户申请退款"}, headers=h)
    assert r.status_code == 200, r.text
    db = TestingSession()
    try:
        user_id = db.query(Order).filter(Order.id == order_id).first().user_id
        db.add(Refund(order_id=order_id, user_id=user_id, amount=150,
                      reason="用户申请退款", status="refunded"))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    finally:
        db.close()


def test_double_refund_sequential_rejected(client):
    h = _register_and_login(client, "fix3-ref-seq@example.com")
    pkg = _create_package(client, "FIX-3 退款順序套餐", stock=5)
    order_id = _buy_paid(client, h, pkg["id"])
    r = client.post(f"/api/v3/orders/{order_id}/refund",
                    json={"reason": "用户申请退款"}, headers=h)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/v3/orders/{order_id}/refund",
                    json={"reason": "用户申请退款"}, headers=h)
    assert r.status_code == 400, r.text


# ---------- 3. concurrent purchase of the last unit: exactly one order ----------
def test_concurrent_purchase_last_unit_only_one_succeeds__row_lock(
        client, monkeypatch):
    import app.routers.packages as pkg_router
    pkg = _create_package(client, "FIX-3 最後一件套餐", stock=1)
    h1 = _register_and_login(client, "fix3-stock-a@example.com")
    h2 = _register_and_login(client, "fix3-stock-b@example.com")
    # Widen the stock-check -> decrement -> flush window.
    monkeypatch.setattr(
        pkg_router, "_sync_coupon_stock",
        _sleeping_wrapper(pkg_router._sync_coupon_stock, seconds=0.4))
    headers = [h1, h2]
    results = _run_two(lambda i, tc: tc.post(
        f"/api/v3/packages/{pkg['id']}/purchase",
        json={"payment_method": "fps", "client_ref": ""},
        headers=headers[i]))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 400], \
        f"[{BACKEND}] expected exactly one 200 + one 400, got {statuses}: " \
        f"{[r.text for r in results]}"
    db = TestingSession()
    try:
        left = db.query(Package).filter(Package.id == pkg["id"]).first()
        assert left.stock_left == 0, f"[{BACKEND}] stock_left={left.stock_left}"
        orders = db.query(Order).filter(
            Order.package_id == pkg["id"],
            Order.payment_status != "cancelled").all()
        assert len(orders) == 1, f"[{BACKEND}] {len(orders)} orders stored"
    finally:
        db.close()


def test_stock_check_cannot_go_negative__db_constraint(client):
    """Backstop: CHECK (stock_left >= 0) fires on a direct conflicting
    write (both backends, no serialisation help)."""
    pkg = _create_package(client, "FIX-3 庫存下限套餐", stock=1)
    db = TestingSession()
    try:
        row = db.query(Package).filter(Package.id == pkg["id"]).first()
        row.stock_left = -1
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    finally:
        db.close()
    db = TestingSession()
    try:
        db.add(models_bargain.BargainCampaign(
            shop_id=1, name="FIX-3 庫存下限活動", original_price=200,
            floor_price=100, stock_total=1, stock_left=-1,
            expires_at=_now() + timedelta(days=7)))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    finally:
        db.close()


# ---------- 3b. concurrent bargain start on the last unit ----------
def test_concurrent_bargain_start_last_unit_only_one_succeeds__row_lock(
        client, monkeypatch):
    import app.routers.bargain as bargain_router
    camp = _make_campaign(client, "FIX-3 最後一件砍價", stock=1)
    h1 = _register_and_login(client, "fix3-bg-a@example.com")
    h2 = _register_and_login(client, "fix3-bg-b@example.com")
    # _now() is called between the stock check and the commit.
    monkeypatch.setattr(
        bargain_router, "_now",
        _sleeping_wrapper(bargain_router._now, seconds=0.2))
    headers = [h1, h2]
    results = _run_two(lambda i, tc: tc.post(
        f"/api/v3/bargain-campaigns/{camp['id']}/start",
        headers=headers[i]))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 400], \
        f"[{BACKEND}] expected exactly one 200 + one 400, got {statuses}: " \
        f"{[r.text for r in results]}"
    db = TestingSession()
    try:
        row = db.query(models_bargain.BargainCampaign).filter(
            models_bargain.BargainCampaign.id == camp["id"]).first()
        assert row.stock_left == 0, f"[{BACKEND}] stock_left={row.stock_left}"
        sessions = db.query(models_bargain.BargainSession).filter(
            models_bargain.BargainSession.campaign_id == camp["id"]).all()
        assert len(sessions) == 1, f"[{BACKEND}] {len(sessions)} sessions"
    finally:
        db.close()


# ---------- 4. concurrent settlement pay: exactly one succeeds ----------
def test_concurrent_pay_settlement_only_one_succeeds__row_lock(
        client, monkeypatch):
    import app.routers.admin_v2 as admin_router
    s = _approved_settlement(client, "fix3-pay@example.com", 2, 1)
    fh = _finance_headers(client)
    # _log() runs after the status flip, before the commit.
    monkeypatch.setattr(admin_router, "_log",
                        _sleeping_wrapper(admin_router._log, seconds=0.4))
    results = _run_two(lambda i, tc: tc.post(
        f"/api/v3/admin/settlements/{s['id']}/pay", headers=fh))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 400], \
        f"[{BACKEND}] expected exactly one 200 + one 400, got {statuses}: " \
        f"{[r.text for r in results]}"
    db = TestingSession()
    try:
        row = db.query(Settlement).filter(Settlement.id == s["id"]).first()
        assert row.status == "paid", f"[{BACKEND}] status={row.status}"
        assert row.paid_at is not None
    finally:
        db.close()


def test_double_pay_settlement_sequential_rejected(client):
    s = _approved_settlement(client, "fix3-pay-seq@example.com", 8, 4)
    fh = _finance_headers(client)
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/pay", headers=fh)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/pay", headers=fh)
    assert r.status_code == 400, r.text


# ---------- 5. concurrent invoices: total never exceeds the net ----------
def test_concurrent_invoices_total_capped_at_net__row_lock(
        client, monkeypatch):
    import app.routers.admin_v2 as admin_router
    shop_id, face = CASH[5]  # cash 100, rate 0.10 -> net 90
    s = _approved_settlement(client, "fix3-inv@example.com", 5, shop_id)
    assert s["net_amount"] == 90, s
    fh = _finance_headers(client)
    # _log() runs after the invoice row is flushed, before the commit.
    monkeypatch.setattr(admin_router, "_log",
                        _sleeping_wrapper(admin_router._log, seconds=0.4))
    body = {"shop_id": shop_id, "settlement_id": s["id"], "amount": 60,
            "title": "測試公司", "tax_id": "12345678"}
    results = _run_two(lambda i, tc: tc.post(
        "/api/v3/admin/invoices", json=body, headers=fh))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [201, 400], \
        f"[{BACKEND}] expected exactly one 201 + one 400, got {statuses}: " \
        f"{[r.text for r in results]}"
    db = TestingSession()
    try:
        rows = db.query(Invoice).filter(
            Invoice.settlement_id == s["id"],
            Invoice.status != "rejected").all()
        total = sum(float(x.amount) for x in rows)
        assert total == 60, f"[{BACKEND}] invoiced total={total}"
        assert total <= 90
    finally:
        db.close()


def test_invoice_cap_sequential_rejected(client):
    shop_id, face = CASH[17]  # cash 150, rate 0.10 -> net 135
    s = _approved_settlement(client, "fix3-inv-seq@example.com", 17, shop_id)
    fh = _finance_headers(client)
    r = client.post("/api/v3/admin/invoices", json={
        "shop_id": shop_id, "settlement_id": s["id"], "amount": 100,
        "title": "測試公司"}, headers=fh)
    assert r.status_code == 201, r.text
    r = client.post("/api/v3/admin/invoices", json={
        "shop_id": shop_id, "settlement_id": s["id"], "amount": 100,
        "title": "測試公司"}, headers=fh)
    assert r.status_code == 400, r.text  # 100 + 100 > net 135


# ---------- 6. concurrent revoke: exactly one revokes, points reversed once ----------
def test_concurrent_revoke_redemption_only_once__row_lock(
        client, monkeypatch):
    import app.routers.redemption as redemption_router
    h, wid, red_id = _redeem(client, "fix3-revoke@example.com", 22)
    vh = _vendor_headers(client)
    db = TestingSession()
    try:
        user_id = db.query(models.WalletItem).filter(
            models.WalletItem.id == wid).first().user_id
        before = points_balance(db, user_id)
    finally:
        db.close()
    assert before == 30, before  # +10 claim, +20 redeem
    # add_points() reverses the +20 after the revoked_at check.
    monkeypatch.setattr(
        redemption_router, "add_points",
        _sleeping_wrapper(redemption_router.add_points, seconds=0.4))
    results = _run_two(lambda i, tc: tc.post(
        f"/api/v3/redemptions/{red_id}/revoke", headers=vh))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 400], \
        f"[{BACKEND}] expected exactly one 200 + one 400, got {statuses}: " \
        f"{[r.text for r in results]}"
    db = TestingSession()
    try:
        red = db.query(models.Redemption).filter(
            models.Redemption.id == red_id).first()
        assert red.revoked_at is not None
        assert points_balance(db, user_id) == before - 20, \
            f"[{BACKEND}] points reversed more than once"
        item = db.query(models.WalletItem).filter(
            models.WalletItem.id == wid).first()
        assert item.status == 1  # coupon returned to unused, once
    finally:
        db.close()


def test_double_revoke_sequential_rejected(client):
    h, wid, red_id = _redeem(client, "fix3-revoke-seq@example.com", 24)
    vh = _vendor_headers(client)
    r = client.post(f"/api/v3/redemptions/{red_id}/revoke", headers=vh)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/v3/redemptions/{red_id}/revoke", headers=vh)
    assert r.status_code == 400, r.text


# ---------- 7. concurrent helps: total cut never pushes price below floor ----------
def test_concurrent_helps_never_below_floor__row_lock(client, monkeypatch):
    """M2: two different people help the same bargain at the same time.

    The headline invariant is the floor: the two cuts together must
    never push current_price below floor_price (cut_amount() already
    caps one cut at the remaining amount, but only a serialised
    read-then-write keeps the SUM capped). The same missing lock also
    shows up as a lost update — helper_count stays 1 and the second
    help's cut is silently overwritten — so the test pins the
    accounting too: helper_count == 2 and the stored cuts sum to the
    price drop actually recorded.
    """
    import app.routers.bargain as bargain_router
    camp = _make_campaign(client, "M2 併發助力不超底價", stock=5,
                          original=200, floor=100)
    owner = _register_and_login(client, "m2-help-owner@example.com")
    r = client.post(f"/api/v3/bargain-campaigns/{camp['id']}/start",
                    headers=owner)
    assert r.status_code == 200, r.text
    sid = r.json()["session"]["id"]
    h1 = _register_and_login(client, "m2-help-a@example.com")
    h2 = _register_and_login(client, "m2-help-b@example.com")
    # cut_amount() runs between the price/helper_count read and the
    # commit — sleep there to widen the race window.
    monkeypatch.setattr(
        bargain_router, "cut_amount",
        _sleeping_wrapper(bargain_router.cut_amount, seconds=0.4))
    headers = [h1, h2]
    results = _run_two(lambda i, tc: tc.post(
        f"/api/v3/bargain-sessions/{sid}/help", json={},
        headers=headers[i]))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 200], \
        f"[{BACKEND}] expected both helps to succeed, got {statuses}: " \
        f"{[r.text for r in results]}"
    cuts = [float(r.json()["cut_amount"]) for r in results]
    db = TestingSession()
    try:
        s = db.query(models_bargain.BargainSession).filter(
            models_bargain.BargainSession.id == sid).first()
        assert float(s.current_price) >= 100, \
            f"[{BACKEND}] price cut below floor: {s.current_price}"
        assert s.helper_count == 2, \
            f"[{BACKEND}] helper_count={s.helper_count}, one help lost"
        rows = db.query(models_bargain.BargainHelp).filter(
            models_bargain.BargainHelp.session_id == sid).all()
        assert len(rows) == 2, f"[{BACKEND}] {len(rows)} help rows stored"
        total_cut = sum(float(x.cut_amount) for x in rows)
        assert total_cut == pytest.approx(200 - float(s.current_price)), \
            f"[{BACKEND}] recorded cuts {total_cut} != price drop " \
            f"{200 - float(s.current_price)} (lost update)"
        assert total_cut == pytest.approx(sum(cuts)), \
            f"[{BACKEND}] recorded cuts {total_cut} != returned cuts {cuts}"
    finally:
        db.close()


# ---------- 8. concurrent scans of the same code: only one redeems ----------
def test_concurrent_scan_same_code_only_one_succeeds__row_lock(
        client, monkeypatch):
    """M2: two vendor scans of the same QR code at the same time.

    Exactly one scan succeeds (200); the other is rejected (400) and
    no second redemption row, second +20 points or second used_count
    is recorded. Pre-fix both scans read status 1 before either
    commits, so both succeed.
    """
    import app.routers.coupons as coupons_router
    h = _register_and_login(client, "m2-scan@example.com")
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": 22}, headers=h)
    assert r.status_code == 200, r.text
    wid = client.get("/api/v3/get-favorite-coupons-list",
                     headers=h).json()["items"][0]["id"]
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=h)
    assert qr.status_code == 200, qr.text
    code = qr.json()["code"]
    vh = _vendor_headers(client)
    db = TestingSession()
    try:
        user_id = db.query(models.WalletItem).filter(
            models.WalletItem.id == wid).first().user_id
        before = points_balance(db, user_id)
    finally:
        db.close()
    assert before == 10, before  # +10 claim only, not yet redeemed
    # add_points() runs after the status check, before the commit —
    # sleep there to widen the race window.
    monkeypatch.setattr(
        coupons_router, "add_points",
        _sleeping_wrapper(coupons_router.add_points, seconds=0.4))
    results = _run_two(lambda i, tc: tc.post(
        "/api/v3/new-scan-coupon", json={"code": code}, headers=vh))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 400], \
        f"[{BACKEND}] expected exactly one 200 + one 400, got {statuses}: " \
        f"{[r.text for r in results]}"
    db = TestingSession()
    try:
        reds = db.query(models.Redemption).filter(
            models.Redemption.wallet_item_id == wid).all()
        assert len(reds) == 1, f"[{BACKEND}] {len(reds)} redemptions stored"
        item = db.query(models.WalletItem).filter(
            models.WalletItem.id == wid).first()
        assert item.status == 2, f"[{BACKEND}] item status={item.status}"
        assert points_balance(db, user_id) == before + 20, \
            f"[{BACKEND}] redeem points credited more than once"
        coupon = db.query(models.Coupon).filter(
            models.Coupon.id == item.coupon_id).first()
        assert coupon.used_count == 1, \
            f"[{BACKEND}] used_count={coupon.used_count}"
    finally:
        db.close()
