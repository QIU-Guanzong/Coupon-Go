"""P1-B queue-F fixes (MERGE_QUEUE §F / REVIEW §3.3): idempotency keys,
payout concurrency, transfer decoupled from redemption, Pydantic bodies,
configurable Stripe v2 version, negative-balance rule, Decimal cents.

TDD: these tests were written FIRST against the pre-fix code and fail
there; the fix makes them pass. Run:
  cd apps/api && python -m pytest tests/test_p1b_connect_fixes.py -v
Postgres concurrency tests are skipped on SQLite (FOR UPDATE is a
Postgres-only proof) and run via pg-run/p1b_pg_run.py (pgserver).

Uses its own throwaway DB in an independent tmp dir (queue global
rule 4) — never the shared /tmp/coupon_go_test_*.db.
"""
import os
import sys
import threading
import time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import config, connect_gateway, connect_service, models  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models_connect import ConnectLedgerEntry  # noqa: E402
from app.payments_gateway import GatewayError  # noqa: E402
from app.seed import seed  # noqa: E402


def _tmp_sqlite_url(tmp_path_factory):
    d = tmp_path_factory.mktemp("p1b_fixes")
    return f"sqlite:///{d}/coupon_go_test_p1b_fixes.db"


PG_URL = os.environ.get("P1B_PG_URL", "")


def _pg_reachable(url):
    if not url:
        return False
    try:
        probe = create_engine(url, connect_args={"connect_timeout": 2})
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
        probe.dispose()
        return True
    except Exception:
        return False


# BACKEND is decided at import: Postgres only when P1B_PG_URL is set
# and reachable (pg-run script); otherwise an isolated tmp SQLite file.
if _pg_reachable(PG_URL):
    BACKEND = "postgres"
    engine = create_engine(PG_URL, pool_size=5, max_overflow=5)
    _SQLITE_URL = None
else:
    BACKEND = "sqlite"
    _SQLITE_URL = None  # set from tmp_path_factory in the fixture
    engine = None

TestingSession = None


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    global engine, TestingSession, _SQLITE_URL
    connect_gateway._reset_simulator_idempotency()
    if BACKEND == "postgres":
        Base.metadata.drop_all(bind=engine)
    else:
        _SQLITE_URL = _tmp_sqlite_url(tmp_path_factory)
        engine = create_engine(_SQLITE_URL,
                               connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine)
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
    connect_gateway._reset_simulator_idempotency()


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


def _vendor_headers(client):
    r = client.post("/api/v3/login", json={
        "email": "vendor@coupon-go.example", "password": "Demo123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _admin_headers(client):
    # Seed admin (app/seed.py): admin@coupon-go.example / Demo123!.
    # Self-registration as admin is rejected (400), so log in instead.
    r = client.post("/api/v3/login", json={
        "email": "admin@coupon-go.example", "password": "Demo123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _create_package(client, name, stock=5, price=150, original=200,
                    shop_id=1):
    vh = _vendor_headers(client)
    body = {
        "shop_id": shop_id, "name": name,
        "items": [{"name": "主菜一份", "qty": 1}],
        "person_count": 2, "original_price": original, "package_price": price,
        "valid_from": "2026-11-01T00:00:00",
        "valid_to": "2026-12-31T00:00:00",
        "stock_total": stock, "refund_policy": "anytime",
    }
    r = client.post("/api/v3/packages", json=body, headers=vh)
    assert r.status_code == 201, r.text
    return r.json()["package"]


def _buy_paid(client, headers, package_id, method="card"):
    r = client.post(f"/api/v3/packages/{package_id}/purchase",
                    json={"payment_method": method}, headers=headers)
    assert r.status_code == 200, r.text
    order_id = r.json()["order"]["id"]
    r = client.post(f"/api/v3/orders/{order_id}/confirm-payment",
                    json={"outcome": "succeeded"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["order"]["payment_status"] == "paid"
    return r.json()["order"]


def _qr_and_scan(client, user_headers, vh, wallet_item_id):
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wallet_item_id},
                     headers=user_headers)
    assert qr.status_code == 200, qr.text
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 200, r.text
    return r.json()


def _scan_package(client, email, shop_id, price=150):
    """Buy + scan a fresh package on shop_id; returns scan response."""
    h = _register_and_login(client, email)
    vh = _vendor_headers(client)
    pkg = _create_package(client, f"P1-B-F {email}", price=price,
                          shop_id=shop_id)
    paid = _buy_paid(client, h, pkg["id"])
    return _qr_and_scan(client, h, vh, paid["wallet_item_id"]), paid


def _onboard_sync(client, shop_id):
    vh = _vendor_headers(client)
    r = client.post("/api/v3/merchant/connect/onboarding", json={
        "shop_id": shop_id,
        "return_url": "coupon-go://connect/return",
        "refresh_url": "coupon-go://connect/refresh"}, headers=vh)
    assert r.status_code == 200, r.text
    r = client.post("/api/v3/merchant/connect/sync",
                    json={"shop_id": shop_id}, headers=vh)
    assert r.status_code == 200, r.text
    return r.json()


def _balance(client, shop_id):
    vh = _vendor_headers(client)
    r = client.get("/api/v3/merchant/connect/balance",
                   params={"shop_id": shop_id}, headers=vh)
    assert r.status_code == 200, r.text
    return r.json()


def _entry(entry_id):
    with _db() as db:
        row = db.query(ConnectLedgerEntry).filter_by(id=entry_id).first()
        assert row is not None
        return {"id": row.id, "kind": row.kind, "status": row.status,
                "shop_id": row.shop_id, "redemption_id": row.redemption_id,
                "net_amount": row.net_amount, "retry_count": row.retry_count,
                "manual_notified": row.manual_notified,
                "stripe_transfer_id": row.stripe_transfer_id,
                "stripe_payout_id": row.stripe_payout_id,
                "pending_reversal": row.pending_reversal,
                "note": row.note or ""}


def _sweep(batch_size=None):
    with _db() as db:
        out = connect_service.sweep_pending_transfers(db, batch_size=batch_size)
        db.commit()
        return out


# ---------- F1: idempotency keys ----------

def test_gateway_transfer_same_key_returns_same_transfer():
    # Simulator dedupes on the idempotency key: the second call with
    # the same key returns the FIRST result (one Stripe transfer).
    out1 = connect_gateway.create_transfer(
        "acct_test_x", Decimal("135.00"), "coupon-go-redemption-1",
        idempotency_key="transfer-42")
    out2 = connect_gateway.create_transfer(
        "acct_test_x", Decimal("135.00"), "coupon-go-redemption-1",
        idempotency_key="transfer-42")
    assert out1["transfer_id"] == out2["transfer_id"]
    out3 = connect_gateway.create_transfer(
        "acct_test_x", Decimal("135.00"), "coupon-go-redemption-1",
        idempotency_key="transfer-43")
    assert out3["transfer_id"] != out1["transfer_id"]


def test_gateway_payout_and_reversal_same_key_returns_same_result():
    out1 = connect_gateway.create_payout(
        "acct_test_x", Decimal("100.00"), idempotency_key="payout-7")
    out2 = connect_gateway.create_payout(
        "acct_test_x", Decimal("100.00"), idempotency_key="payout-7")
    assert out1["payout_id"] == out2["payout_id"]
    out1 = connect_gateway.reverse_transfer(
        "tr_test_abc", Decimal("135.00"), idempotency_key="reversal-9")
    out2 = connect_gateway.reverse_transfer(
        "tr_test_abc", Decimal("135.00"), idempotency_key="reversal-9")
    assert out1["reversal_id"] == out2["reversal_id"]


def test_gateway_real_path_sends_idempotency_key(monkeypatch):
    import httpx
    from app import connect_gateway as gw

    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_p1b_fix")
    seen = {}

    class _FakeResp:
        status_code = 200
        text = ""

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers") or {}
        seen["data"] = kwargs.get("data") or {}
        return _FakeResp({"id": "tr_real_1", "status": "paid",
                          "amount": 13500, "currency": "hkd"})

    monkeypatch.setattr(httpx, "post", fake_post)
    out = gw.create_transfer("acct_real_1", Decimal("135.00"), "g-1",
                             idempotency_key="transfer-42")
    assert seen["url"] == "https://api.stripe.com/v1/transfers"
    assert seen["headers"].get("Idempotency-Key") == "transfer-42"
    # Cents come from Decimal, never float.
    assert seen["data"]["amount"] == 13500
    assert out["transfer_id"] == "tr_real_1"


def test_gateway_decimal_cents_never_float(monkeypatch):
    import httpx
    from app import connect_gateway as gw

    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_p1b_fix")
    seen = {}

    class _FakeResp:
        status_code = 200
        text = ""

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, **kwargs):
        seen.setdefault("amounts", []).append((kwargs.get("data") or {}).get("amount"))
        return _FakeResp({"id": "tr_x", "status": "paid",
                          "amount": 0, "currency": "hkd"})

    monkeypatch.setattr(httpx, "post", fake_post)
    # 1.005 via float is 100 (100.4999…); Decimal HALF_UP is 101.
    # 0.125 via float rounds half-to-even to 12; Decimal HALF_UP is 13.
    gw.create_transfer("acct_real_1", Decimal("1.005"), "g-2")
    gw.create_transfer("acct_real_1", Decimal("0.125"), "g-3")
    gw.create_transfer("acct_real_1", Decimal("19.99"), "g-4")
    assert seen["amounts"] == [101, 13, 1999]


# ---------- F5: Stripe version configurable ----------

def test_stripe_v2_version_comes_from_config(monkeypatch):
    from app import connect_gateway as gw

    assert config.STRIPE_V2_VERSION == "2026-11-18.preview"
    assert gw._v2_headers("sk_test_x")["Stripe-Version"] == "2026-11-18.preview"
    monkeypatch.setattr(config, "STRIPE_V2_VERSION", "2027-01-01.preview")
    assert gw._v2_headers("sk_test_x")["Stripe-Version"] == "2027-01-01.preview"


# ---------- F3: transfer decoupled from redemption ----------

def test_scan_never_calls_stripe_transfer(client, monkeypatch):
    """Even with a ready account, the scan must not call Stripe: the
    transfer is queued and runs via the scheduler / retry endpoint."""
    _onboard_sync(client, shop_id=4)
    calls = []

    def recording_transfer(*args, **kwargs):
        calls.append((args, kwargs))
        return {"transfer_id": "tr_test_must_not_happen", "status": "paid",
                "amount": 135.0, "currency": "HKD", "simulated": True}

    monkeypatch.setattr(connect_gateway, "create_transfer", recording_transfer)
    scan, _ = _scan_package(client, "p1bf-nosync@example.com", shop_id=4)
    assert scan["connect"]["transfer"] == "pending"
    assert scan["connect"]["net_amount"] == 135.00
    assert calls == []  # the scan must not touch Stripe at all
    monkeypatch.undo()


def test_scan_pending_then_sweep_transfers(client):
    _sweep()  # drain pendings earlier tests left behind
    _onboard_sync(client, shop_id=5)
    scan, _ = _scan_package(client, "p1bf-sweep@example.com", shop_id=5,
                            price=150)
    entry_id = scan["connect"]["entry_id"]
    assert scan["connect"]["transfer"] == "pending"
    assert _entry(entry_id)["status"] == "pending"
    out = _sweep()
    assert out["transferred"] == 1
    assert _entry(entry_id)["status"] == "transferred"
    assert _balance(client, shop_id=5)["available"] == 135.00


def test_sweep_batch_limit(client):
    # Drain any pending entries earlier tests left behind, so the
    # batch counts below are exactly these three.
    _sweep()
    for shop_id in (8, 9, 10):
        _onboard_sync(client, shop_id=shop_id)
    ids = []
    for i, shop_id in enumerate((8, 9, 10)):
        scan, _ = _scan_package(
            client, f"p1bf-batch{i}@example.com", shop_id=shop_id)
        ids.append(scan["connect"]["entry_id"])
    out = _sweep(batch_size=2)
    assert out["transferred"] == 2
    assert out["still_pending"] == 1
    out = _sweep(batch_size=2)
    assert out["transferred"] == 1
    assert all(_entry(e)["status"] == "transferred" for e in ids)


def test_sweep_unknown_transfer_is_not_sent_again(client, monkeypatch):
    """A timeout may mean money moved. Keep its identity for reconciliation."""
    _sweep()
    _onboard_sync(client, shop_id=6)
    scan, _ = _scan_package(client, "p1bf-key@example.com", shop_id=6)
    entry_id = scan["connect"]["entry_id"]
    keys = []

    def uncertain(*args, **kwargs):
        keys.append(kwargs.get("idempotency_key"))
        raise GatewayError("Stripe 超時（可能已執行）")

    monkeypatch.setattr(connect_gateway, "create_transfer", uncertain)
    assert _sweep()["transferred"] == 0
    assert _entry(entry_id)["status"] == "transfer_unknown"
    ah = _admin_headers(client)
    recoveries = client.get("/api/v3/admin/connect/recoveries", headers=ah)
    assert recoveries.status_code == 200
    assert any(row["id"] == entry_id and row["status"] == "transfer_unknown"
               for row in recoveries.json()["needs_manual_transfers"])
    with _db() as db:
        entry = db.get(ConnectLedgerEntry, entry_id)
        entry.status = "transferring"
        db.commit()
    recoveries = client.get("/api/v3/admin/connect/recoveries", headers=ah)
    assert any(row["id"] == entry_id and row["status"] == "transferring"
               for row in recoveries.json()["needs_manual_transfers"])
    with _db() as db:
        entry = db.get(ConnectLedgerEntry, entry_id)
        entry.status = "transfer_unknown"
        db.commit()
    for _ in range(5):
        _sweep()
    assert keys == [f"transfer-{entry_id}"]
    assert _entry(entry_id)["manual_notified"] is True
    with _db() as db:
        notes = db.query(models.Notification).filter_by(
            notification_type="connect_manual_review", target_shop_id=6).all()
    assert len(notes) == 1
    vh = _vendor_headers(client)
    r = client.post(f"/api/v3/merchant/connect/transfers/{entry_id}/retry", headers=vh)
    assert r.status_code == 409
    assert keys == [f"transfer-{entry_id}"]


# ---------- F2: payout concurrency ----------

def test_payout_writes_processing_before_stripe_call(client, monkeypatch):
    """The processing entry (balance already deducted) must be
    committed BEFORE the Stripe call, so a concurrent payout sees it."""
    _onboard_sync(client, shop_id=7)
    scan, _ = _scan_package(client, "p1bf-proc@example.com", shop_id=7)
    _sweep()
    assert _balance(client, shop_id=7)["available"] == 135.00
    seen = {}

    def slow_payout(*args, **kwargs):
        with _db() as db:
            rows = db.query(ConnectLedgerEntry).filter_by(
                shop_id=7, kind="payout").all()
            seen["rows"] = [(r.status, float(r.net_amount)) for r in rows]
        return {"payout_id": "po_test_proc", "status": "paid",
                "amount": 100.0, "currency": "HKD", "simulated": True}

    monkeypatch.setattr(connect_gateway, "create_payout", slow_payout)
    vh = _vendor_headers(client)
    r = client.post("/api/v3/merchant/connect/payouts",
                    json={"shop_id": 7, "amount": 100.00, "request_id": "p1bf-processing-0001"}, headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "paid_out"
    assert seen["rows"] == [("processing", -100.0)]
    monkeypatch.undo()


def test_payout_unknown_keeps_balance_reserved_until_verified(client, monkeypatch):
    _onboard_sync(client, shop_id=12)
    scan, _ = _scan_package(client, "p1bf-pfail@example.com", shop_id=12)
    _sweep()

    def failing_payout(*args, **kwargs):
        raise GatewayError("Stripe 提款失敗")

    monkeypatch.setattr(connect_gateway, "create_payout", failing_payout)
    vh = _vendor_headers(client)
    r = client.post("/api/v3/merchant/connect/payouts",
                    json={"shop_id": 12, "amount": 100.00, "request_id": "p1bf-unknown-00001"}, headers=vh)
    assert r.status_code == 502, r.text
    with _db() as db:
        rows = db.query(ConnectLedgerEntry).filter_by(
            shop_id=12, kind="payout").all()
        assert len(rows) == 1 and rows[0].status == "payout_unknown"
    assert _balance(client, shop_id=12)["available"] == 35.00
    monkeypatch.undo()
    r = client.post("/api/v3/merchant/connect/payouts",
                    json={"shop_id": 12, "amount": 100.00, "request_id": "p1bf-unknown-00001"}, headers=vh)
    assert r.status_code == 200 and r.json()["status"] == "payout_unknown", r.text
    r = client.post("/api/v3/merchant/connect/payouts",
                    json={"shop_id": 12, "amount": 20.00, "request_id": "p1bf-unknown-00002"}, headers=vh)
    assert r.status_code == 409, r.text


@pytest.mark.skipif(BACKEND != "postgres",
                    reason="needs real Postgres SELECT ... FOR UPDATE")
def test_concurrent_payouts_only_one_succeeds(client, monkeypatch):
    """Two concurrent 100.00 payouts against 135.00 available: exactly
    one succeeds, the other is rejected, final available is 35.00."""
    _onboard_sync(client, shop_id=13)
    scan, _ = _scan_package(client, "p1bf-conc@example.com", shop_id=13)
    _sweep()
    assert _balance(client, shop_id=13)["available"] == 135.00

    def slow_payout(*args, **kwargs):
        time.sleep(0.5)  # widen the race window after the 1st commit
        return {"payout_id": "po_test_conc", "status": "paid",
                "amount": 100.0, "currency": "HKD", "simulated": True}

    monkeypatch.setattr(connect_gateway, "create_payout", slow_payout)
    results = {}
    barrier = threading.Barrier(2)

    def attempt(name):
        barrier.wait(timeout=10)
        db = TestingSession()
        try:
            out = connect_service.request_payout(
                db, db.query(models.Shop).filter_by(id=13).one(),
                Decimal("100.00"), f"p1bf-concurrent-{name}-0001")
            db.commit()
            results[name] = ("ok", out["status"])
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            results[name] = ("error", getattr(exc, "status_code", str(exc)))
        finally:
            db.close()

    t1 = threading.Thread(target=attempt, args=("a",))
    t2 = threading.Thread(target=attempt, args=("b",))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)
    statuses = {v[1] for v in results.values()}
    assert sorted(v[0] for v in results.values()) == ["error", "ok"], results
    assert statuses == {409, "paid_out"}, results
    assert _balance(client, shop_id=13)["available"] == 35.00
    with _db() as db:
        rows = db.query(ConnectLedgerEntry).filter_by(
            shop_id=13, kind="payout").all()
        assert len(rows) == 1 and rows[0].status == "paid_out"
    monkeypatch.undo()


@pytest.mark.skipif(BACKEND != "postgres",
                    reason="needs real Postgres SELECT ... FOR UPDATE")
def test_concurrent_retry_same_entry_only_one_transfers(client):
    """Two concurrent retries of the same pending entry: one
    transfers, the other gets 409 — never two Stripe transfers."""
    _onboard_sync(client, shop_id=14)
    scan, _ = _scan_package(client, "p1bf-rconc@example.com", shop_id=14)
    entry_id = scan["connect"]["entry_id"]
    results = {}
    barrier = threading.Barrier(2)

    def attempt(name):
        barrier.wait(timeout=10)
        db = TestingSession()
        try:
            out = connect_service.retry_entry(db, entry_id)
            db.commit()
            results[name] = ("ok", out["status"])
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            results[name] = ("error", getattr(exc, "status_code", str(exc)))
        finally:
            db.close()

    t1 = threading.Thread(target=attempt, args=("a",))
    t2 = threading.Thread(target=attempt, args=("b",))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)
    assert sorted(v[0] for v in results.values()) == ["error", "ok"], results
    assert {v[1] for v in results.values()} == {409, "transferred"}, results
    assert _entry(entry_id)["status"] == "transferred"


# ---------- F6: negative balance / reversal failure ----------

def test_negative_balance_blocks_payout_and_lists_recovery(client):
    _onboard_sync(client, shop_id=15)
    scan, paid = _scan_package(client, "p1bf-neg@example.com", shop_id=15)
    _sweep()
    vh = _vendor_headers(client)
    r = client.post("/api/v3/merchant/connect/payouts",
                    json={"shop_id": 15, "amount": 135.00, "request_id": "p1bf-negative-00001"}, headers=vh)
    assert r.status_code == 200, r.text
    assert _balance(client, shop_id=15)["available"] == 0.00
    # Revoke: the transfer is clawed back after the money was paid out.
    r = client.post(f"/api/v3/redemptions/{scan['redemption_id']}/revoke",
                    headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["connect"]["reason"] == "reversal_queued"
    _sweep()
    bal = _balance(client, shop_id=15)
    assert bal["available"] == -135.00
    # Negative balance stops new payouts.
    r = client.post("/api/v3/merchant/connect/payouts",
                    json={"shop_id": 15, "amount": 10.00, "request_id": "p1bf-negative-00002"}, headers=vh)
    assert r.status_code == 422
    # Admin recovery board lists the shop.
    ah = _admin_headers(client)
    r = client.get("/api/v3/admin/connect/recoveries", headers=ah)
    assert r.status_code == 200, r.text
    neg = r.json()["negative_balances"]
    assert any(n["shop_id"] == 15 and n["available"] == -135.00 for n in neg)
    # Non-admin cannot see the board.
    r = client.get("/api/v3/admin/connect/recoveries", headers=vh)
    assert r.status_code == 403


def test_reversal_failure_counts_retries_and_notifies_admin(client, monkeypatch):
    _onboard_sync(client, shop_id=16)
    scan, _ = _scan_package(client, "p1bf-revfail@example.com", shop_id=16)
    _sweep()
    entry_id = scan["connect"]["entry_id"]

    def failing_reversal(*args, **kwargs):
        raise GatewayError("Stripe 冲回失敗：餘額不足")

    monkeypatch.setattr(connect_gateway, "reverse_transfer", failing_reversal)
    vh = _vendor_headers(client)
    r = client.post(f"/api/v3/redemptions/{scan['redemption_id']}/revoke",
                    headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["connect"]["reversed"] is False
    assert _entry(entry_id)["pending_reversal"] is True
    assert _entry(entry_id)["retry_count"] == 0
    _sweep()
    r = client.post(
        f"/api/v3/merchant/connect/transfers/{entry_id}/retry", headers=vh)
    assert r.status_code == 409, r.text
    row = _entry(entry_id)
    assert row["retry_count"] == 1
    assert row["manual_notified"] is True
    assert row["pending_reversal"] is True
    with _db() as db:
        notes = db.query(models.Notification).filter_by(
            notification_type="connect_manual_review",
            target_shop_id=16).all()
    assert len(notes) == 1
    monkeypatch.undo()


# ---------- F4: Pydantic bodies + roles ----------

def test_payout_body_validation(client):
    vh = _vendor_headers(client)
    # Non-numeric amount -> 422 from Pydantic, not a 500.
    r = client.post("/api/v3/merchant/connect/payouts",
                    json={"shop_id": 5, "amount": "abc", "request_id": "p1bf-invalid-00001"}, headers=vh)
    assert r.status_code == 422
    # Missing shop_id -> 422.
    r = client.post("/api/v3/merchant/connect/payouts",
                    json={"amount": 10.00, "request_id": "p1bf-invalid-00002"}, headers=vh)
    assert r.status_code == 422
    # Negative amount -> 422.
    r = client.post("/api/v3/merchant/connect/payouts",
                    json={"shop_id": 5, "amount": -5, "request_id": "p1bf-invalid-00003"}, headers=vh)
    assert r.status_code == 422
    # A plain customer cannot touch merchant money endpoints.
    ch = _register_and_login(client, "p1bf-cust@example.com")
    r = client.post("/api/v3/merchant/connect/payouts",
                    json={"shop_id": 5, "amount": 10.00, "request_id": "p1bf-invalid-00004"}, headers=ch)
    assert r.status_code == 403


def test_onboarding_body_validation(client):
    vh = _vendor_headers(client)
    r = client.post("/api/v3/merchant/connect/onboarding",
                    json={"return_url": "x"}, headers=vh)
    assert r.status_code == 422
