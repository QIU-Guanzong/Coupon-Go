"""M1 tests: background scheduler — expired refunds / bargain settle /
payment-timeout close / cashback sweep must run automatically.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_m1_scheduler.py -v
Uses a temp file SQLite DB so two real `python -m app.scheduler`
subprocesses can run against the same DB (two-instance test).
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"
# The app-level scheduler must NOT start inside TestClient here; these
# tests drive the scheduler explicitly via create_scheduler / run-once.
os.environ["SCHEDULER_ENABLED"] = "0"

from app import config, models  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app import models_bargain, models_cashback  # noqa: E402
from app.models_packages import Order, Payment, Refund  # noqa: E402
from app.seed import seed  # noqa: E402
from app.seed_bargain import seed_bargain_campaigns  # noqa: E402

TEST_DIR = tempfile.mkdtemp(prefix="coupon_go_test_m1_")
TEST_DB = os.path.join(TEST_DIR, "coupon_go_test.db")

engine = create_engine(f"sqlite:///{TEST_DB}",
                       connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine)


@pytest.fixture(scope="module")
def client():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    Base.metadata.create_all(bind=engine)
    db = TestingSession()
    seed(db)
    seed_bargain_campaigns(db)
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


def _register_and_login(client, email):
    assert client.post("/api/v3/new-register", json={
        "email": email, "password": "Demo123!", "name": "Test",
        "role": "customer"}).status_code == 200
    code = client.get("/api/v3/_dev/otp", params={"email": email}).json()["code"]
    r = client.post("/api/v3/new-verify-otp", json={
        "email": email, "code": code, "purpose": "register"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _login(client, email, password="Demo123!"):
    r = client.post("/api/v3/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _vendor_headers(client):
    return _login(client, "vendor@coupon-go.example")


def _admin_headers(client):
    return _login(client, "admin@coupon-go.example")


def _create_package(client, name="M1 套餐", refund_policy="anytime",
                    stock=5, price=150, original=200):
    vh = _vendor_headers(client)
    body = {
        "shop_id": 1, "name": name,
        "items": [{"name": "主菜一份", "qty": 1}],
        "person_count": 2, "original_price": original, "package_price": price,
        "valid_from": "2026-11-01T00:00:00",
        "valid_to": "2026-12-31T00:00:00",
        "usage_rules": {"need_booking": True, "includes": ["加一服務費"]},
        "stock_total": stock, "refund_policy": refund_policy,
    }
    r = client.post("/api/v3/packages", json=body, headers=vh)
    assert r.status_code == 201, r.text
    pkg = r.json()["package"]
    # §3.2: packages enter review on creation; approve before use.
    ah = _admin_headers(client)
    r = client.post(f"/api/v3/admin/packages/{pkg['id']}/approve",
                    params={"approve": True}, headers=ah)
    assert r.status_code == 200, r.text
    r = client.get("/api/v3/package-details", params={"package_id": pkg["id"]})
    assert r.status_code == 200, r.text
    return r.json()["package"]


def _buy_paid(client, headers, package_id):
    r = client.post(f"/api/v3/packages/{package_id}/purchase",
                    json={"payment_method": "fps"}, headers=headers)
    assert r.status_code == 200, r.text
    order_id = r.json()["order"]["id"]
    r = client.post(f"/api/v3/orders/{order_id}/confirm-payment",
                    json={"outcome": "succeeded"}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["order"]


def _buy_pending(client, headers, package_id):
    r = client.post(f"/api/v3/packages/{package_id}/purchase",
                    json={"payment_method": "fps"}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["order"]


def _expire_package_coupon(package_id):
    db = TestingSession()
    try:
        coupon = db.query(models.Coupon).filter(
            models.Coupon.package_id == package_id).first()
        assert coupon is not None
        coupon.valid_to = _now() - timedelta(days=1)
        db.commit()
    finally:
        db.close()


def _make_campaign(client, original=200, floor=100, stock=5,
                   duration_minutes=1440):
    vh = _login(client, "vendor@coupon-go.example")
    r = client.post("/api/v3/bargain-campaigns", json={
        "shop_id": 1, "name": "M1 砍價", "items": "主菜、飲品",
        "person_count": 2, "original_price": original, "floor_price": floor,
        "stock_total": stock, "helper_limit": 10,
        "duration_minutes": duration_minutes, "campaign_days": 7,
        "lock_stock": True}, headers=vh)
    assert r.status_code == 200, r.text
    return r.json()["campaign"]


def _start_session(client, headers, campaign_id):
    r = client.post(f"/api/v3/bargain-campaigns/{campaign_id}/start",
                    headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["session"]


def _expire_bargain_session(session_id):
    db = TestingSession()
    try:
        s = db.query(models_bargain.BargainSession).filter(
            models_bargain.BargainSession.id == session_id).first()
        assert s is not None and s.status == "active"
        s.expires_at = _now() - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()


def _backdate_payment_attempt(order_id, minutes=20):
    db = TestingSession()
    try:
        attempt = (db.query(Payment)
                   .filter(Payment.order_id == order_id)
                   .order_by(Payment.id.desc()).first())
        assert attempt is not None
        attempt.created_at = _now() - timedelta(minutes=minutes)
        db.commit()
    finally:
        db.close()


def _insert_cashback_txn(email, channel="online", amount=50,
                         expected_confirm_at=None, expires_at=None):
    """Direct-DB cashback earning (mirrors test_cashback._insert_legacy_*)."""
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
        t = models_cashback.CashbackTransaction(
            user_id=user.id, merchant_id=1, redemption_id=red.id,
            channel=channel, amount=amount, rate=0.05, status="pending",
            funded_by="merchant_commission",
            expected_confirm_at=expected_confirm_at, expires_at=expires_at)
        db.add(t)
        db.commit()
        return t.id
    finally:
        db.close()


def _txn_status(txn_id):
    db = TestingSession()
    try:
        t = db.query(models_cashback.CashbackTransaction).filter(
            models_cashback.CashbackTransaction.id == txn_id).first()
        return t.status if t else None
    finally:
        db.close()


def _order_status(order_id):
    db = TestingSession()
    try:
        o = db.query(Order).filter(Order.id == order_id).first()
        return (o.payment_status, o.refund_status) if o else (None, None)
    finally:
        db.close()


def _refund_count(order_id):
    db = TestingSession()
    try:
        return db.query(Refund).filter(Refund.order_id == order_id).count()
    finally:
        db.close()


# ---------- M1: run_all_jobs — expired package auto-refund, exactly once ----------
def test_run_all_jobs_refunds_expired_package_once(client):
    from app.scheduler import run_all_jobs
    h = _register_and_login(client, "m1-expire@example.com")
    pkg = _create_package(client, name="M1 過期自動退套餐")
    paid = _buy_paid(client, h, pkg["id"])
    _expire_package_coupon(pkg["id"])

    result = run_all_jobs(session_factory=TestingSession)
    assert result["expired-packages"]["refunded"] >= 1, result
    assert _order_status(paid["id"])[1] == "refunded"
    assert _refund_count(paid["id"]) == 1

    # Second run: idempotent — nothing more refunded, still exactly one row.
    result2 = run_all_jobs(session_factory=TestingSession)
    assert result2["expired-packages"]["refunded"] == 0, result2
    assert _refund_count(paid["id"]) == 1


def test_run_all_jobs_settles_expired_bargain_once(client):
    from app.scheduler import run_all_jobs
    h = _register_and_login(client, "m1-bargain@example.com")
    camp = _make_campaign(client, original=200, floor=100, stock=5)
    s = _start_session(client, h, camp["id"])
    _expire_bargain_session(s["id"])

    result = run_all_jobs(session_factory=TestingSession)
    assert result["bargain-expire"]["settled"] >= 1, result
    db = TestingSession()
    try:
        row = db.query(models_bargain.BargainSession).filter(
            models_bargain.BargainSession.id == s["id"]).first()
        assert row.status == "failed"
        refunds = db.query(models_bargain.BargainRefund).filter(
            models_bargain.BargainRefund.session_id == s["id"]).all()
        assert len(refunds) == 1
        assert refunds[0].reason == "bargain_failed"
        assert refunds[0].status == "succeeded"
    finally:
        db.close()

    result2 = run_all_jobs(session_factory=TestingSession)
    assert result2["bargain-expire"]["settled"] == 0, result2


def test_run_all_jobs_closes_timed_out_pending_order(client):
    from app.scheduler import run_all_jobs
    h = _register_and_login(client, "m1-timeout@example.com")
    pkg = _create_package(client, name="M1 超時關閉套餐", stock=5)
    order = _buy_pending(client, h, pkg["id"])
    assert order["payment_status"] == "pending"
    _backdate_payment_attempt(order["id"], minutes=20)

    result = run_all_jobs(session_factory=TestingSession)
    assert result["payment-timeouts"]["closed"] >= 1, result
    assert _order_status(order["id"])[0] == "timeout"

    # Stock released: package stock back to full.
    r = client.get("/api/v3/package-details", params={"package_id": pkg["id"]})
    assert r.json()["package"]["stock_left"] == 5


def test_run_all_jobs_sweeps_cashback_for_all_users(client):
    from app.scheduler import run_all_jobs
    ha = _register_and_login(client, "m1-cb-a@example.com")
    hb = _register_and_login(client, "m1-cb-b@example.com")
    pkg = _create_package(client, name="M1 返現套餐")
    _buy_paid(client, ha, pkg["id"])
    _buy_paid(client, hb, pkg["id"])
    due = _insert_cashback_txn(
        "m1-cb-a@example.com", channel="online", amount=50,
        expected_confirm_at=_now() - timedelta(days=1))
    not_due = _insert_cashback_txn(
        "m1-cb-b@example.com", channel="online", amount=50,
        expected_confirm_at=_now() + timedelta(days=1))
    expired_dinein = _insert_cashback_txn(
        "m1-cb-a@example.com", channel="dinein", amount=30,
        expires_at=_now() - timedelta(days=1))

    result = run_all_jobs(session_factory=TestingSession)
    assert result["cashback-sweep"]["confirmed"] >= 1, result
    assert result["cashback-sweep"]["expired"] >= 1, result
    assert _txn_status(due) == "withdrawable"
    assert _txn_status(not_due) == "pending"
    assert _txn_status(expired_dinein) == "void"

    result2 = run_all_jobs(session_factory=TestingSession)
    assert result2["cashback-sweep"]["confirmed"] == 0, result2
    assert result2["cashback-sweep"]["expired"] == 0, result2


# ---------- M1: scheduler actually runs on its interval, no manual call ----------
def test_scheduler_runs_automatically_on_interval(client):
    from app.scheduler import create_scheduler
    h = _register_and_login(client, "m1-auto@example.com")
    pkg = _create_package(client, name="M1 自動跑套餐")
    paid = _buy_paid(client, h, pkg["id"])
    _expire_package_coupon(pkg["id"])

    sched = create_scheduler(session_factory=TestingSession,
                             interval_seconds=1)
    sched.start()
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            if _order_status(paid["id"])[1] == "refunded":
                break
            time.sleep(0.5)
        assert _order_status(paid["id"])[1] == "refunded"
        assert _refund_count(paid["id"]) == 1
    finally:
        sched.shutdown(wait=False)


# ---------- M1: two instances run only once ----------
def test_two_instances_run_expired_refund_only_once(client):
    h = _register_and_login(client, "m1-twoinst@example.com")
    pkg = _create_package(client, name="M1 雙實例套餐")
    paid = _buy_paid(client, h, pkg["id"])
    _expire_package_coupon(pkg["id"])

    api_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
    env["COUPON_GO_ENV"] = "dev"

    def _run_once():
        return subprocess.run(
            [sys.executable, "-m", "app.scheduler", "run-once",
             "--job", "expired-packages"],
            cwd=api_dir, env=env, capture_output=True, text=True,
            timeout=120)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(_run_once), pool.submit(_run_once)]
        results = [f.result() for f in futs]
    for r in results:
        assert r.returncode == 0, r.stderr
    refunded = [json.loads(r.stdout)["expired-packages"].get("refunded", 0)
                for r in results]
    assert sum(refunded) == 1, refunded
    assert _refund_count(paid["id"]) == 1


def test_lock_held_by_other_instance_skips_job(client):
    from app.models_scheduler import SchedulerLock
    from app.scheduler import run_expired_packages
    # Simulate another instance holding the row-claim lock right now.
    db = TestingSession()
    try:
        db.add(SchedulerLock(name="expired-packages", owner="other-instance",
                             acquired_at=_now()))
        db.commit()
    finally:
        db.close()
    try:
        db = TestingSession()
        try:
            result = run_expired_packages(db)
        finally:
            db.close()
        assert result.get("skipped") is True, result
    finally:
        db = TestingSession()
        try:
            db.query(SchedulerLock).filter(
                SchedulerLock.name == "expired-packages").delete()
            db.commit()
        finally:
            db.close()


# ---------- M1: one bad item must not block the others ----------
def test_single_item_failure_does_not_block_others(client, monkeypatch):
    from app.routers import packages as pkg_router
    from app.scheduler import run_all_jobs
    h = _register_and_login(client, "m1-failone@example.com")
    pkg = _create_package(client, name="M1 單條失敗套餐", stock=5)
    paid1 = _buy_paid(client, h, pkg["id"])
    paid2 = _buy_paid(client, h, pkg["id"])
    _expire_package_coupon(pkg["id"])

    orig = pkg_router._process_refund

    def _flaky(db, order, reason, **kwargs):
        if order.id == paid1["id"]:
            raise RuntimeError("simulated refund failure")
        return orig(db, order, reason, **kwargs)

    monkeypatch.setattr(pkg_router, "_process_refund", _flaky)
    result = run_all_jobs(session_factory=TestingSession)
    assert result["expired-packages"]["failed"] >= 1, result
    assert result["expired-packages"]["refunded"] >= 1, result
    assert _order_status(paid2["id"])[1] == "refunded"
    assert _order_status(paid1["id"])[1] == "none"
    # Next run retries the failed one.
    monkeypatch.undo()
    result2 = run_all_jobs(session_factory=TestingSession)
    assert result2["expired-packages"]["refunded"] >= 1, result2
    assert _order_status(paid1["id"])[1] == "refunded"


# ---------- M1: manual endpoints share the same logic ----------
def test_manual_endpoints_share_scheduler_logic(client):
    from app.scheduler import run_all_jobs
    h = _register_and_login(client, "m1-shared@example.com")
    pkg = _create_package(client, name="M1 共用邏輯套餐")
    paid = _buy_paid(client, h, pkg["id"])
    _expire_package_coupon(pkg["id"])
    run_all_jobs(session_factory=TestingSession)
    assert _order_status(paid["id"])[1] == "refunded"

    ah = _admin_headers(client)
    r = client.post("/api/v3/orders/process-expired-refunds", headers=ah)
    assert r.status_code == 200, r.text
    assert r.json()["refunded"] == 0
    assert _refund_count(paid["id"]) == 1

    camp = _make_campaign(client, original=200, floor=100, stock=5)
    s = _start_session(client, h, camp["id"])
    _expire_bargain_session(s["id"])
    run_all_jobs(session_factory=TestingSession)
    r = client.post("/api/v3/bargain/expire-sweep", headers=ah)
    assert r.status_code == 200, r.text
    assert r.json()["settled"] == 0


# ---------- M1: non-dev without scheduler config warns loudly, not silently ----------
def test_non_dev_without_scheduler_config_warns(monkeypatch, caplog):
    from app import scheduler
    monkeypatch.setattr(config, "APP_ENV", "prod")
    monkeypatch.setattr(config, "SCHEDULER_ENABLED_RAW", None)
    import logging
    with caplog.at_level(logging.WARNING, logger="app.scheduler"):
        status = scheduler.validate_scheduler_config()
    assert status["enabled"] is False
    assert "SCHEDULER_ENABLED" in status["reason"]
    assert any("SCHEDULER_ENABLED" in rec.message for rec in caplog.records), \
        caplog.text


def test_explicit_scheduler_config_enables_in_non_dev(monkeypatch):
    from app import scheduler
    monkeypatch.setattr(config, "APP_ENV", "prod")
    monkeypatch.setattr(config, "SCHEDULER_ENABLED_RAW", "1")
    status = scheduler.validate_scheduler_config()
    assert status["enabled"] is True


def test_postgres_advisory_lock_branch():
    """Advisory-lock SQL path (no live Postgres here): a stub session
    whose dialect reports postgresql must take the lock via
    pg_try_advisory_lock and release it via pg_advisory_unlock."""
    from app.scheduler import acquire_job_lock

    class _Result:
        def __init__(self, value):
            self._value = value

        def scalar(self):
            return self._value

    class _Conn:
        def __init__(self):
            self.calls = []

        def execution_options(self, **kwargs):
            return self

        def execute(self, stmt, params=None):
            sql = str(stmt)
            self.calls.append(sql)
            if "pg_try_advisory_lock" in sql:
                return _Result(True)
            if "pg_advisory_unlock" in sql:
                return _Result(True)
            return _Result(None)

        def close(self):
            pass

    class _Dialect:
        name = "postgresql"

    class _Bind:
        dialect = _Dialect()

        def __init__(self, conn):
            self.conn = conn

        def connect(self):
            return self.conn

    class _StubSession:
        def __init__(self):
            self.conn = _Conn()
            self.bind = _Bind(self.conn)

        def get_bind(self):
            return self.bind

        def execute(self, stmt, params=None):
            return self.conn.execute(stmt, params)

        def commit(self):
            pass

        def close(self):
            pass

    stub = _StubSession()
    with acquire_job_lock(stub, "expired-packages") as acquired:
        assert acquired is True
    assert any("pg_try_advisory_lock" in c for c in stub.conn.calls)
    assert any("pg_advisory_unlock" in c for c in stub.conn.calls)


def test_run_once_reports_item_failure_to_supervisor(monkeypatch, capsys):
    from app import scheduler

    class FakeSession:
        def close(self):
            pass

    monkeypatch.setattr(scheduler, "SessionLocal", FakeSession)
    monkeypatch.setitem(scheduler.JOB_RUNNERS, "expired-packages", lambda db: {"failed": 1, "refunded": 2})
    assert scheduler.main(["run-once", "--job", "expired-packages"]) == 1
    assert json.loads(capsys.readouterr().out)["expired-packages"]["failed"] == 1
    monkeypatch.setitem(scheduler.JOB_RUNNERS, "expired-packages",
                        lambda db: {"skipped": True, "reason": "locked_by_another_instance"})
    assert scheduler.main(["run-once", "--job", "expired-packages"]) == 0


def test_expired_lease_owner_cannot_release_successor(client):
    from app.models_scheduler import SchedulerLock
    from app.scheduler import _owner, _release_row_claim, _try_row_claim

    first, second = _owner(), _owner()
    assert first != second
    with TestingSession() as one, TestingSession() as two:
        assert _try_row_claim(one, "expired-packages", first, 600)
        row = one.query(SchedulerLock).filter_by(name="expired-packages").one()
        row.acquired_at = _now() - timedelta(seconds=601)
        one.commit()
        assert _try_row_claim(two, "expired-packages", second, 600)
        _release_row_claim(one, "expired-packages", first)
        assert two.query(SchedulerLock).filter_by(name="expired-packages", owner=second).count() == 1
        _release_row_claim(two, "expired-packages", second)


@pytest.mark.skipif(not os.environ.get("M1_POSTGRES_URL"), reason="set M1_POSTGRES_URL for PostgreSQL lock tests")
def test_postgres_job_lock_survives_commits_and_releases_after_failure():
    from sqlalchemy import text
    from app.scheduler import acquire_job_lock

    pg_engine = create_engine(os.environ["M1_POSTGRES_URL"], pool_size=3, max_overflow=0)
    sessions = sessionmaker(bind=pg_engine)
    try:
        with sessions() as job, sessions() as competitor:
            with acquire_job_lock(job, "expired-packages") as acquired:
                assert acquired
                for _ in range(2):
                    job.execute(text("SELECT 1"))
                    job.commit()
                    with acquire_job_lock(competitor, "expired-packages") as other:
                        assert not other
                with pytest.raises(RuntimeError):
                    with acquire_job_lock(competitor, "payment-timeouts") as other:
                        assert other
                        raise RuntimeError("simulated job failure")
            with acquire_job_lock(competitor, "expired-packages") as acquired:
                assert acquired
            with acquire_job_lock(job, "payment-timeouts") as acquired:
                assert acquired
    finally:
        pg_engine.dispose()


def test_expired_refund_retries_after_local_failure_without_duplicating_gateway(client, monkeypatch):
    from app.routers import packages as service
    from app.scheduler import run_expired_packages

    h = _register_and_login(client, "m1-refund-retry@example.com")
    pkg = _create_package(client, name="M1 退款重試", stock=2)
    paid = _buy_paid(client, h, pkg["id"])
    _expire_package_coupon(pkg["id"])
    revision_before = client.get("/api/v3/wallet/revision", headers=h).json()["revision"]
    send = service.refund_payment
    release = service._release_stock
    calls = []
    fail = [True]

    def gateway(ref, amount, *, request_key):
        result = send(ref, amount, request_key=request_key)
        calls.append((request_key, result["gateway_ref"]))
        return result

    def fail_after_gateway(db, order):
        if order.id == paid["id"] and fail[0]:
            fail[0] = False
            raise RuntimeError("lost local commit after gateway success")
        return release(db, order)

    monkeypatch.setattr(service, "refund_payment", gateway)
    monkeypatch.setattr(service, "_release_stock", fail_after_gateway)
    with TestingSession() as db:
        first = run_expired_packages(db)
    assert first["failed"] == 1
    revision_pending = client.get("/api/v3/wallet/revision", headers=h).json()["revision"]
    assert revision_pending != revision_before
    with TestingSession() as db:
        order = db.get(Order, paid["id"])
        voucher = db.query(models.WalletItem).filter_by(order_id=order.id).one()
        assert order.refund_status == "requested" and voucher.status == 3
        assert db.get(models.Package, pkg["id"]).stock_left == 1
        assert db.query(Refund).filter_by(order_id=order.id).count() == 1
    with TestingSession() as db:
        second = run_expired_packages(db)
    assert second["refunded"] == 1
    with TestingSession() as db:
        order = db.get(Order, paid["id"])
        assert order.refund_status == "refunded" and order.net_amount == 0
        assert db.get(models.Package, pkg["id"]).stock_left == 2
        assert db.query(Refund).filter_by(order_id=order.id, status="refunded").count() == 1
    assert len(calls) == 2 and calls[0] == calls[1]
    revision_done = client.get("/api/v3/wallet/revision", headers=h).json()["revision"]
    assert revision_done != revision_pending
    with TestingSession() as db:
        assert run_expired_packages(db)["refunded"] == 0


def test_old_unknown_gateway_result_is_not_resent(client, monkeypatch):
    from app.routers import packages as service
    from app.scheduler import run_expired_packages

    h = _register_and_login(client, "m1-refund-age@example.com")
    pkg = _create_package(client, name="M1 未知退款", stock=1)
    paid = _buy_paid(client, h, pkg["id"])
    _expire_package_coupon(pkg["id"])
    monkeypatch.setattr(service, "refund_payment",
                        lambda *args, **kwargs: (_ for _ in ()).throw(service.GatewayError("timeout")))
    with TestingSession() as db:
        assert run_expired_packages(db)["failed"] == 1
    with TestingSession() as db:
        refund = db.query(Refund).filter_by(order_id=paid["id"]).one()
        refund.created_at = _now() - timedelta(hours=24)
        db.commit()
    def unexpected(*args, **kwargs):
        raise AssertionError("old request must not be submitted again")
    monkeypatch.setattr(service, "refund_payment", unexpected)
    with TestingSession() as db:
        assert run_expired_packages(db)["failed"] >= 1
        assert db.get(Order, paid["id"]).refund_status == "requested"
        assert db.get(models.Package, pkg["id"]).stock_left == 0
        assert db.query(Refund).filter_by(order_id=paid["id"]).count() == 1


@pytest.mark.skipif(not os.environ.get("M1_POSTGRES_URL"), reason="set M1_POSTGRES_URL for PostgreSQL lock tests")
def test_postgres_release_failure_discards_locked_backend(caplog):
    from sqlalchemy import event
    from app.scheduler import acquire_job_lock

    pg_engine = create_engine(os.environ["M1_POSTGRES_URL"], pool_size=2, max_overflow=0)
    sessions = sessionmaker(bind=pg_engine)
    calls = []
    def fail_unlock(conn, cursor, statement, parameters, context, executemany):
        if "pg_advisory_unlock" in statement and not calls:
            calls.append(statement)
            raise RuntimeError("injected lost lock connection")
    event.listen(pg_engine, "before_cursor_execute", fail_unlock)
    try:
        with sessions() as job:
            with acquire_job_lock(job, "expired-packages") as acquired:
                assert acquired
        assert calls
        with sessions() as subsequent:
            with acquire_job_lock(subsequent, "expired-packages") as acquired:
                assert acquired
    finally:
        event.remove(pg_engine, "before_cursor_execute", fail_unlock)
        pg_engine.dispose()


def test_expiry_retry_reconciles_legacy_revocation_cashback_and_settlement(client, monkeypatch):
    from app.models_cashback import CashbackTransaction
    from app.models_line_f import Settlement, SettlementItem, SettlementReversal
    from app.routers import packages as service
    from app.scheduler import run_expired_packages

    user = _register_and_login(client, "m1-legacy-settle@example.com")
    vendor = _vendor_headers(client)
    finance = _login(client, "finance@coupon-go.example")
    pkg = _create_package(client, name="M1 舊版撤銷", stock=2)
    paid = _buy_paid(client, user, pkg["id"])
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": paid["wallet_item_id"]}, headers=user).json()
    scan = client.post("/api/v3/new-scan-coupon", json={"code": qr["code"]}, headers=vendor)
    assert scan.status_code == 200, scan.text
    rid = scan.json()["redemption_id"]
    generated = client.post("/api/v3/admin/settlements/generate", headers=finance, json={
        "shop_id": 1, "period": _now().strftime("%Y-%m")})
    assert generated.status_code == 201, generated.text
    snapshot = generated.json()["settlement"]
    sid = snapshot["id"]
    assert client.post(f"/api/v3/redemptions/{rid}/revoke", headers=vendor).status_code == 200

    # Emulate historical versions that recorded the revoke without reversing
    # either its pending cashback or its already-generated settlement line.
    with TestingSession() as db:
        line = db.query(SettlementItem).filter_by(redemption_id=rid).one()
        statement = db.get(Settlement, sid)
        statement.gross_amount += line.amount
        statement.commission += line.commission
        statement.net_amount += line.amount - line.commission
        statement.redemption_count += 1
        db.delete(db.get(SettlementReversal, line.id))
        earned = db.query(CashbackTransaction).filter_by(redemption_id=rid).one()
        earned.status, earned.void_reason = "pending", None
        db.commit()
    _expire_package_coupon(pkg["id"])
    send = service.refund_payment
    keys = []
    def lost(ref, amount, *, request_key):
        result = send(ref, amount, request_key=request_key)
        keys.append((request_key, result["gateway_ref"]))
        if len(keys) == 1:
            raise service.GatewayError("gateway response lost")
        return result
    monkeypatch.setattr(service, "refund_payment", lost)
    with TestingSession() as db:
        assert run_expired_packages(db)["failed"] >= 1
    with TestingSession() as db:
        assert db.get(Order, paid["id"]).refund_status == "requested"
        assert db.query(CashbackTransaction).filter_by(redemption_id=rid).one().status == "pending"
        assert float(db.get(Settlement, sid).gross_amount) == snapshot["gross_amount"]
        assert db.get(models.Package, pkg["id"]).stock_left == 1
    with TestingSession() as db:
        assert run_expired_packages(db)["refunded"] >= 1
    with TestingSession() as db:
        order = db.get(Order, paid["id"])
        line = db.query(SettlementItem).filter_by(redemption_id=rid).one()
        statement = db.get(Settlement, sid)
        earning = db.query(CashbackTransaction).filter_by(redemption_id=rid).one()
        assert order.refund_status == "refunded"
        assert db.get(models.WalletItem, paid["wallet_item_id"]).status == 3
        assert db.get(models.Package, pkg["id"]).stock_left == 2
        assert (earning.status, earning.void_reason) == ("void", "revoked")
        assert float(statement.gross_amount) == snapshot["gross_amount"] - 150
        assert float(statement.commission) == snapshot["commission"] - 15
        assert float(statement.net_amount) == snapshot["net_amount"] - 135
        assert db.get(SettlementReversal, line.id).amount == -150
        assert db.query(Refund).filter_by(order_id=order.id).count() == 1
    assert len(keys) == 2 and keys[0] == keys[1]
    with TestingSession() as db:
        assert run_expired_packages(db)["refunded"] == 0
        assert db.query(Refund).filter_by(order_id=paid["id"]).count() == 1
