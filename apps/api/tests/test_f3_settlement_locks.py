"""F3 tests: row locks on settlement approve / dispute / resolve.

What this file proves: the three settlement state transitions that FIX-3
left unlocked (``approve`` / ``dispute`` / ``resolve`` in
``app/routers/admin_v2.py``) now hold the settlement row lock while they
re-check the status, exactly like ``pay`` already does since FIX-3 — so
a concurrent ``pay`` can no longer be silently reverted by a ``dispute``
that read the pre-pay status (``paid`` -> ``disputed``), and two
concurrent transitions on one settlement cannot both succeed.

RUN MODE (how the concurrency tests run) — same setup as
``test_fix3_row_locks.py``, read that file's docstring for the full
story:
  * Postgres (default when reachable): real verification —
    ``SELECT ... FOR UPDATE`` only exists on Postgres
    (``F3_POSTGRES_URL``, default
    ``postgresql+psycopg2://coupon_go:coupon_go@localhost:5432/coupon_go_test_f3``).
  * SQLite fallback (only when no Postgres is reachable, or when
    ``FIX3_FORCE_SQLITE=1``): the test engine issues ``BEGIN IMMEDIATE``
    for every transaction, so the two concurrent transactions serialise
    at the DB level. The fallback therefore verifies the locked
    re-check logic, but it does NOT by itself prove that
    ``with_for_update()`` is present — run the Postgres mode for that.
    (In this sandbox no Postgres is reachable, so the concurrency
    tests below pass pre-fix too under the fallback's serialisation;
    the lock itself is pinned by
    ``test_approve_dispute_resolve_issue_select_for_update__lock_present``,
    which spies on ``Query.with_for_update`` through the real HTTP
    path and fails before the fix / passes after it, on either
    backend.)

Concurrency tests are named ``test_concurrent_*__row_lock`` and widen
the race window on purpose (a short sleep inside the check-then-write
section, via monkeypatch on ``admin_v2._log``, which runs after the
status flip and before the commit), so a missing lock fails reliably
instead of by timing luck on Postgres. Assertions are never weakened
for the fallback: exactly one request succeeds and the other is
rejected (400), except approve-vs-dispute, where both serial orders
are legal and the invariant is the final state (always "disputed");
in every case the final stored state is a legal serial end state —
never ``paid`` reverted to ``disputed``, never a committed dispute
overwritten back to ``approved``.

Run:  cd apps/api && ../../.venv/bin/python -m pytest \\
          tests/test_f3_settlement_locks.py -v
Uses its own throwaway DB; never touches production data.
"""
import os
import sys
import threading
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Query, sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import models  # noqa: E402,F401
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models_line_f import Settlement  # noqa: E402,F401
from app.seed import seed  # noqa: E402

PG_URL = os.environ.get(
    "F3_POSTGRES_URL",
    # NOTE: explicit +psycopg2 driver — this venv ships psycopg2-binary
    # only; a bare postgresql:// URL makes SQLAlchemy 2.x look for
    # psycopg (v3) and fail to import.
    "postgresql+psycopg2://coupon_go:coupon_go@localhost:5432/coupon_go_test_f3")
SQLITE_DB = "/tmp/coupon_go_test_f3.db"


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


# ---------- shared helpers (same pattern as test_fix3_row_locks.py) ----------
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


def _generate(client, headers, shop_id, period, rate=0.10):
    r = client.post("/api/v3/admin/settlements/generate", json={
        "shop_id": shop_id, "period": period, "commission_rate": rate},
        headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["settlement"]


def _pending_settlement(client, email, coupon_id, shop_id, period=PERIOD):
    """Redeem one cash coupon and generate its settlement (pending)."""
    _redeem(client, email, coupon_id)
    fh = _finance_headers(client)
    return _generate(client, fh, shop_id, period)


def _approved_settlement(client, email, coupon_id, shop_id, period=PERIOD):
    s = _pending_settlement(client, email, coupon_id, shop_id, period)
    fh = _finance_headers(client)
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/approve",
                    headers=fh)
    assert r.status_code == 200, r.text
    return r.json()["settlement"]


def _disputed_settlement(client, email, coupon_id, shop_id, period=PERIOD):
    s = _pending_settlement(client, email, coupon_id, shop_id, period)
    fh = _finance_headers(client)
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/dispute",
                    json={"reason": "金額有異"}, headers=fh)
    assert r.status_code == 200, r.text
    return r.json()["settlement"]


def _paid_settlement(client, email, coupon_id, shop_id, period=PERIOD):
    s = _approved_settlement(client, email, coupon_id, shop_id, period)
    fh = _finance_headers(client)
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/pay", headers=fh)
    assert r.status_code == 200, r.text
    return r.json()["settlement"]


def _get_status(client, settlement_id):
    fh = _finance_headers(client)
    r = client.get(f"/api/v3/admin/settlements/{settlement_id}", headers=fh)
    assert r.status_code == 200, r.text
    return r.json()["settlement"]["status"]


def _final_status(settlement_id):
    db = TestingSession()
    try:
        row = db.query(Settlement).filter(
            Settlement.id == settlement_id).first()
        assert row is not None
        return row.status
    finally:
        db.close()


# ---------- 1. pay vs dispute: paid must never revert to disputed ----------
def test_concurrent_pay_vs_dispute_paid_never_reverts__row_lock(
        client, monkeypatch):
    import app.routers.admin_v2 as admin_router
    s = _approved_settlement(client, "f3-pay-dispute@example.com", 2, 1)
    fh = _finance_headers(client)
    # _log() runs after the status flip, before the commit — in both
    # pay and dispute — so this widens the race window for both sides.
    monkeypatch.setattr(admin_router, "_log",
                        _sleeping_wrapper(admin_router._log, seconds=0.4))
    pay_url = f"/api/v3/admin/settlements/{s['id']}/pay"
    dispute_url = f"/api/v3/admin/settlements/{s['id']}/dispute"
    results = _run_two(lambda i, tc: tc.post(
        pay_url, headers=fh) if i == 0 else tc.post(
        dispute_url, json={"reason": "金額有異"}, headers=fh))
    pay_r, dispute_r = results
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 400], \
        f"[{BACKEND}] expected exactly one 200 + one 400, got {statuses}: " \
        f"{[r.text for r in results]}"
    final = _final_status(s["id"])
    if pay_r.status_code == 200:
        # Pay won: the settlement is paid, dispute must have been
        # rejected — a paid settlement must never become disputed.
        assert final == "paid", \
            f"[{BACKEND}] pay returned 200 but final status={final}"
    else:
        assert dispute_r.status_code == 200, dispute_r.text
        assert final == "disputed", \
            f"[{BACKEND}] dispute won but final status={final}"


# ---------- 2. concurrent approve: exactly one succeeds ----------
def test_concurrent_approve_only_one_succeeds__row_lock(client, monkeypatch):
    import app.routers.admin_v2 as admin_router
    s = _pending_settlement(client, "f3-approve@example.com", 5, 3)
    fh = _finance_headers(client)
    monkeypatch.setattr(admin_router, "_log",
                        _sleeping_wrapper(admin_router._log, seconds=0.4))
    results = _run_two(lambda i, tc: tc.post(
        f"/api/v3/admin/settlements/{s['id']}/approve", headers=fh))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 400], \
        f"[{BACKEND}] expected exactly one 200 + one 400, got {statuses}: " \
        f"{[r.text for r in results]}"
    assert _final_status(s["id"]) == "approved", \
        f"[{BACKEND}] final status={_final_status(s['id'])}"


# ---------- 3. concurrent dispute: exactly one succeeds ----------
def test_concurrent_dispute_only_one_succeeds__row_lock(client, monkeypatch):
    import app.routers.admin_v2 as admin_router
    s = _pending_settlement(client, "f3-dispute@example.com", 8, 4)
    fh = _finance_headers(client)
    monkeypatch.setattr(admin_router, "_log",
                        _sleeping_wrapper(admin_router._log, seconds=0.4))
    results = _run_two(lambda i, tc: tc.post(
        f"/api/v3/admin/settlements/{s['id']}/dispute",
        json={"reason": "金額有異"}, headers=fh))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 400], \
        f"[{BACKEND}] expected exactly one 200 + one 400, got {statuses}: " \
        f"{[r.text for r in results]}"
    assert _final_status(s["id"]) == "disputed", \
        f"[{BACKEND}] final status={_final_status(s['id'])}"


# ---------- 4. concurrent resolve: exactly one succeeds ----------
def test_concurrent_resolve_only_one_succeeds__row_lock(client, monkeypatch):
    import app.routers.admin_v2 as admin_router
    s = _disputed_settlement(client, "f3-resolve@example.com", 17, 9)
    fh = _finance_headers(client)
    monkeypatch.setattr(admin_router, "_log",
                        _sleeping_wrapper(admin_router._log, seconds=0.4))
    results = _run_two(lambda i, tc: tc.post(
        f"/api/v3/admin/settlements/{s['id']}/resolve",
        json={"decision": "approve", "note": "覆核通過"}, headers=fh))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 400], \
        f"[{BACKEND}] expected exactly one 200 + one 400, got {statuses}: " \
        f"{[r.text for r in results]}"
    assert _final_status(s["id"]) == "approved", \
        f"[{BACKEND}] final status={_final_status(s['id'])}"


# ---------- 5. approve vs dispute on pending: dispute always wins ----------
def test_concurrent_approve_vs_dispute_only_one_wins__row_lock(
        client, monkeypatch):
    import app.routers.admin_v2 as admin_router
    s = _pending_settlement(client, "f3-appr-dispute@example.com", 22, 11)
    fh = _finance_headers(client)
    monkeypatch.setattr(admin_router, "_log",
                        _sleeping_wrapper(admin_router._log, seconds=0.4))
    approve_url = f"/api/v3/admin/settlements/{s['id']}/approve"
    dispute_url = f"/api/v3/admin/settlements/{s['id']}/dispute"
    results = _run_two(lambda i, tc: tc.post(
        approve_url, headers=fh) if i == 0 else tc.post(
        dispute_url, json={"reason": "金額有異"}, headers=fh))
    approve_r, dispute_r = results
    # NOTE: unlike the same-endpoint races above, [200, 400] is NOT the
    # invariant here. Both serial orders are legal and both end in
    # "disputed": approve-then-dispute (dispute reads "approved", which
    # is disputable) lets both succeed, while dispute-then-approve
    # rejects the approve. The invariant is the final state: it must
    # be "disputed" in every interleaving. The illegal pre-fix outcome
    # is final "approved" — the approve overwriting a committed
    # dispute (lost update), which no serial order can produce.
    assert dispute_r.status_code == 200, dispute_r.text
    assert approve_r.status_code in (200, 400), approve_r.text
    final = _final_status(s["id"])
    assert final == "disputed", \
        f"[{BACKEND}] approve={approve_r.status_code} " \
        f"dispute={dispute_r.status_code} but final status={final} " \
        f"(a committed dispute was lost)"


# ---------- 6. serial guard: a paid settlement cannot be disputed ----------
def test_dispute_paid_settlement_rejected_and_stays_paid(client):
    s = _paid_settlement(client, "f3-paid-dispute@example.com", 24, 12)
    fh = _finance_headers(client)
    assert _get_status(client, s["id"]) == "paid"
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/dispute",
                    json={"reason": "金額有異"}, headers=fh)
    assert r.status_code == 400, r.text
    assert "此狀態的結算單不能提出爭議" in r.json()["detail"]
    assert _get_status(client, s["id"]) == "paid"
    assert _final_status(s["id"]) == "paid"


# ---------- 7. serial guards: every transition enforces its own state ----------
def test_settlement_state_guards_sequential(client):
    # A settlement for a past period (no redemptions in it) is enough:
    # these tests are about the state machine, not amounts.
    fh = _finance_headers(client)
    s = _generate(client, fh, 12, "2026-01")
    sid = s["id"]
    assert _get_status(client, sid) == "pending"
    # resolve requires disputed
    r = client.post(f"/api/v3/admin/settlements/{sid}/resolve",
                    json={"decision": "approve", "note": ""}, headers=fh)
    assert r.status_code == 400, r.text
    assert "只有爭議中的結算單可以處理" in r.json()["detail"]
    # dispute from pending -> disputed
    r = client.post(f"/api/v3/admin/settlements/{sid}/dispute",
                    json={"reason": "金額有異"}, headers=fh)
    assert r.status_code == 200, r.text
    assert _get_status(client, sid) == "disputed"
    # dispute again -> 400
    r = client.post(f"/api/v3/admin/settlements/{sid}/dispute",
                    json={"reason": "再議"}, headers=fh)
    assert r.status_code == 400, r.text
    # approve from disputed -> 400
    r = client.post(f"/api/v3/admin/settlements/{sid}/approve", headers=fh)
    assert r.status_code == 400, r.text
    assert "只有待審核的結算單可以確認" in r.json()["detail"]
    # resolve (reopen) -> pending
    r = client.post(f"/api/v3/admin/settlements/{sid}/resolve",
                    json={"decision": "reopen", "note": "退回重審"}, headers=fh)
    assert r.status_code == 200, r.text
    assert _get_status(client, sid) == "pending"
    # approve -> approved; approve again -> 400
    r = client.post(f"/api/v3/admin/settlements/{sid}/approve", headers=fh)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/v3/admin/settlements/{sid}/approve", headers=fh)
    assert r.status_code == 400, r.text
    # pay -> paid; pay again -> 400; resolve on paid -> 400
    r = client.post(f"/api/v3/admin/settlements/{sid}/pay", headers=fh)
    assert r.status_code == 200, r.text
    assert _get_status(client, sid) == "paid"
    r = client.post(f"/api/v3/admin/settlements/{sid}/pay", headers=fh)
    assert r.status_code == 400, r.text
    r = client.post(f"/api/v3/admin/settlements/{sid}/resolve",
                    json={"decision": "approve", "note": ""}, headers=fh)
    assert r.status_code == 400, r.text


# ---------- 8. lock presence: the three endpoints must SELECT ... FOR UPDATE
def test_approve_dispute_resolve_issue_select_for_update__lock_present(
        client, monkeypatch):
    """Pins the F3 mechanism itself through the real HTTP path.

    Why this test exists: on the SQLite fallback the concurrency tests
    above cannot distinguish a locked from an unlocked read (the
    BEGIN IMMEDIATE serialisation does that job instead — see the
    module docstring and test_fix3_row_locks.py). This test therefore
    watches the actual ORM call: ``Query.with_for_update`` (and the
    ``populate_existing`` that must follow it, so the re-check reads
    the locked row instead of a stale identity-map copy) has to fire
    during each of approve / dispute / resolve. It fails before the
    fix and passes after it, on either backend.
    """
    fh = _finance_headers(client)
    orig_wfu = Query.with_for_update
    orig_pop = Query.populate_existing
    wfu_calls = []
    pop_calls = []

    def spy_wfu(self, *args, **kwargs):
        wfu_calls.append(1)
        return orig_wfu(self, *args, **kwargs)

    def spy_pop(self, *args, **kwargs):
        pop_calls.append(1)
        return orig_pop(self, *args, **kwargs)

    monkeypatch.setattr(Query, "with_for_update", spy_wfu)
    monkeypatch.setattr(Query, "populate_existing", spy_pop)

    def _assert_locked(label):
        assert wfu_calls, f"{label}: with_for_update() never called"
        assert pop_calls, f"{label}: populate_existing() never called"
        wfu_calls.clear()
        pop_calls.clear()

    # approve on a pending settlement (shop 1, past period: no
    # redemption needed, the state machine is what matters).
    s = _generate(client, fh, 1, "2026-02")
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/approve",
                    headers=fh)
    assert r.status_code == 200, r.text
    _assert_locked("approve")
    # dispute on a pending settlement.
    s = _generate(client, fh, 1, "2026-03")
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/dispute",
                    json={"reason": "金額有異"}, headers=fh)
    assert r.status_code == 200, r.text
    _assert_locked("dispute")
    # resolve on a disputed settlement.
    s = _generate(client, fh, 1, "2026-04")
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/dispute",
                    json={"reason": "金額有異"}, headers=fh)
    assert r.status_code == 200, r.text
    _assert_locked("dispute (setup for resolve)")
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/resolve",
                    json={"decision": "approve", "note": "覆核通過"},
                    headers=fh)
    assert r.status_code == 200, r.text
    _assert_locked("resolve")
