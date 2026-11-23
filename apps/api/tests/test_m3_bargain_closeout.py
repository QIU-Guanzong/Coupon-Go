"""M3 tests: bargain close-out self-check (助力幂等 / 金额不超底价 /
库存加锁 / 退款走网关记 ref / 终态文案不含内部用语).

Runs against real Postgres (coupon_go_test_m3) — SELECT ... FOR
UPDATE only exists there; SQLite silently ignores it, so a SQLite
run would not prove the locks. Same backend pattern as
test_fix3_row_locks.py (M2).

Each test pins one M3 invariant at the API layer. The concurrency
tests widen the race window on purpose (sleep inside the
check-then-write section) so a missing lock fails reliably.
"""
import os
import sys
import threading
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import models_bargain  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402
from app.seed_bargain import seed_bargain_campaigns  # noqa: E402

PG_URL = os.environ.get(
    "M3_POSTGRES_URL",
    "postgresql+psycopg2://coupon_go:coupon_go@localhost:5432/coupon_go_test_m3")

def _pg_reachable():
    try:
        probe = create_engine(PG_URL, connect_args={"connect_timeout": 2})
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
        probe.dispose()
        return True
    except Exception:
        return False


# Row locks only exist on Postgres, so these tests prove nothing on
# SQLite. Without a reachable Postgres the module is skipped (with the
# reason printed by pytest -rs) instead of erroring on every test.
if not _pg_reachable():
    pytest.skip(
        f"M3 需要真實 Postgres（未能連接 {PG_URL.split('@')[-1]}）；"
        "設置 M3_POSTGRES_URL 或啟動本地 Postgres 後重跑",
        allow_module_level=True)

engine = create_engine(PG_URL, pool_size=5, max_overflow=5)
TestingSession = sessionmaker(bind=engine)


@pytest.fixture(scope="module")
def client():
    Base.metadata.drop_all(bind=engine)
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
    r = client.post("/api/v3/login", json={"email": email, "password": "Demo123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _run_two(fn):
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
    return results


def _sleeping_wrapper(fn, seconds=0.4):
    def wrapped(*args, **kwargs):
        out = fn(*args, **kwargs)
        time.sleep(seconds)
        return out
    return wrapped


def _make_campaign(client, name, original=200, floor=100, stock=5,
                   helper_limit=10, duration_minutes=1440, lock_stock=True):
    vh = _login(client, "vendor@coupon-go.example")
    r = client.post("/api/v3/bargain-campaigns", json={
        "shop_id": 1, "name": name, "items": "主菜、飲品",
        "person_count": 2, "original_price": original,
        "floor_price": floor, "stock_total": stock,
        "helper_limit": helper_limit, "duration_minutes": duration_minutes,
        "campaign_days": 7, "lock_stock": lock_stock}, headers=vh)
    assert r.status_code == 200, r.text
    return r.json()["campaign"]


def _start(client, headers, campaign_id):
    r = client.post(f"/api/v3/bargain-campaigns/{campaign_id}/start",
                    headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["session"]


def _session(client, session_id):
    return client.get(f"/api/v3/bargain-sessions/{session_id}").json()["session"]


# ---------- 1. 助力幂等：同一用户并发两次助力，只算一次 ----------
def test_m3_concurrent_duplicate_help_same_user_only_one_counts(
        client, monkeypatch):
    """M3-1: the same user helps the same session twice at the same
    time. Exactly one help counts (200); the other is rejected 409
    "您已助力過一次". No second help row, no second cut, helper_count
    stays 1. (Sequential duplicate 409 is already covered by
    test_bargain.test_duplicate_self_and_device_help_blocked; the
    concurrency case is the gap — without the M2 session row lock
    both requests pass the duplicate check before either commits,
    and the second then hits the uq_help_once constraint as a 500.)"""
    import app.routers.bargain as bargain_router
    camp = _make_campaign(client, "M3 併發重複助力", original=200, floor=100)
    owner = _register_and_login(client, "m3-dup-owner@example.com")
    s = _start(client, owner, camp["id"])
    helper = _register_and_login(client, "m3-dup-helper@example.com")
    monkeypatch.setattr(
        bargain_router, "cut_amount",
        _sleeping_wrapper(bargain_router.cut_amount, seconds=0.4))
    results = _run_two(lambda i, tc: tc.post(
        f"/api/v3/bargain-sessions/{s['id']}/help",
        json={"device_id": "m3-dup-dev"}, headers=helper))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 409], \
        f"expected one 200 + one 409, got {statuses}: " \
        f"{[r.text for r in results]}"
    ok = [r for r in results if r.status_code == 200][0].json()
    assert ok["cut_amount"] > 0
    db = TestingSession()
    try:
        rows = db.query(models_bargain.BargainHelp).filter(
            models_bargain.BargainHelp.session_id == s["id"]).all()
        assert len(rows) == 1, f"{len(rows)} help rows stored"
        sess = db.query(models_bargain.BargainSession).filter(
            models_bargain.BargainSession.id == s["id"]).first()
        assert sess.helper_count == 1, f"helper_count={sess.helper_count}"
        assert float(sess.current_price) == pytest.approx(
            200 - float(rows[0].cut_amount))
    finally:
        db.close()


# ---------- 2. 金额分配：余款极小时一刀砍到底价，不低于底价 ----------
def test_m3_sequential_helps_never_below_floor_tiny_remaining(client):
    """M3-2: original 100.50, floor 100 — only HK$0.50 of room. The
    first help must cut exactly 0.50 (MIN_CUT is capped at the
    remaining amount) so the price lands exactly on the floor, never
    below it; a further help is then rejected because the session is
    already success."""
    h = _register_and_login(client, "m3-floor-owner@example.com")
    camp = _make_campaign(client, "M3 微差底價", original=100.50, floor=100,
                          helper_limit=10)
    s = _start(client, h, camp["id"])
    helper = _register_and_login(client, "m3-floor-helper@example.com")
    r = client.post(f"/api/v3/bargain-sessions/{s['id']}/help",
                    json={"device_id": "m3-floor-dev"}, headers=helper)
    assert r.status_code == 200, r.text
    assert r.json()["cut_amount"] == 0.50, r.json()
    s = _session(client, s["id"])
    assert s["current_price"] == 100.0, s
    assert s["current_price"] >= s["floor_price"]
    assert s["status"] == "success"
    helper2 = _register_and_login(client, "m3-floor-helper2@example.com")
    r = client.post(f"/api/v3/bargain-sessions/{s['id']}/help",
                    json={"device_id": "m3-floor-dev2"}, headers=helper2)
    assert r.status_code == 400, r.text


# ---------- 3. 库存扣减加锁 ----------
def test_m3_concurrent_double_purchase_same_session_only_one_succeeds(
        client, monkeypatch):
    """M3-3a: the owner double-clicks purchase on the same session.
    Exactly one purchase succeeds (200); the other is rejected 409
    "已下單生成套餐券". Only one wallet item / package coupon is
    created and only one price_cut refund row exists."""
    import app.routers.bargain as bargain_router
    camp = _make_campaign(client, "M3 併發重複下單", original=200, floor=190,
                          helper_limit=1)
    owner = _register_and_login(client, "m3-dblbuy-owner@example.com")
    s = _start(client, owner, camp["id"])
    helper = _register_and_login(client, "m3-dblbuy-helper@example.com")
    r = client.post(f"/api/v3/bargain-sessions/{s['id']}/help",
                    json={"device_id": "m3-dblbuy-dev"}, headers=helper)
    assert r.status_code == 200, r.text
    assert _session(client, s["id"])["status"] == "success"
    monkeypatch.setattr(
        bargain_router, "_add_refund",
        _sleeping_wrapper(bargain_router._add_refund, seconds=0.4))
    results = _run_two(lambda i, tc: tc.post(
        f"/api/v3/bargain-sessions/{s['id']}/purchase", headers=owner))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 409], \
        f"expected one 200 + one 409, got {statuses}: " \
        f"{[r.text for r in results]}"
    db = TestingSession()
    try:
        sess = db.query(models_bargain.BargainSession).filter(
            models_bargain.BargainSession.id == s["id"]).first()
        assert sess.wallet_item_id is not None
        refunds = db.query(models_bargain.BargainRefund).filter(
            models_bargain.BargainRefund.session_id == s["id"],
            models_bargain.BargainRefund.reason == "price_cut").all()
        assert len(refunds) == 1, f"{len(refunds)} price_cut refunds"
    finally:
        db.close()


def test_m3_sequential_double_purchase_rejected_409(client):
    """M3-3a (sequential): purchasing an already-purchased session
    returns 409, no second wallet item or refund."""
    camp = _make_campaign(client, "M3 順序重複下單", original=200, floor=190,
                          helper_limit=1)
    owner = _register_and_login(client, "m3-seqbuy-owner@example.com")
    s = _start(client, owner, camp["id"])
    helper = _register_and_login(client, "m3-seqbuy-helper@example.com")
    assert client.post(f"/api/v3/bargain-sessions/{s['id']}/help",
                       json={"device_id": "m3-seqbuy-dev"},
                       headers=helper).status_code == 200
    r = client.post(f"/api/v3/bargain-sessions/{s['id']}/purchase",
                    headers=owner)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/v3/bargain-sessions/{s['id']}/purchase",
                    headers=owner)
    assert r.status_code == 409, r.text
    db = TestingSession()
    try:
        sess = db.query(models_bargain.BargainSession).filter(
            models_bargain.BargainSession.id == s["id"]).first()
        assert sess.wallet_item_id is not None
        refunds = db.query(models_bargain.BargainRefund).filter(
            models_bargain.BargainRefund.session_id == s["id"]).all()
        assert len(refunds) == 1, f"{len(refunds)} refunds stored"
    finally:
        db.close()


def test_m3_concurrent_purchase_lock_stock_false_last_unit_only_one_succeeds(
        client, monkeypatch):
    """M3-3b: lock_stock=False campaigns deduct stock at purchase time.
    Two different owners each hold a successful session against the
    last unit; their concurrent purchases must yield exactly one 200
    and one 400 "已售罄", stock_left 0, and only one wallet item."""
    import app.routers.bargain as bargain_router
    camp = _make_campaign(client, "M3 併發搶最後一份", original=200, floor=190,
                          stock=1, helper_limit=1, lock_stock=False)
    owners = [_register_and_login(client, f"m3-lastunit-{n}@example.com")
              for n in ("a", "b")]
    sessions = [_start(client, o, camp["id"]) for o in owners]
    for i, s in enumerate(sessions):
        helper = _register_and_login(
            client, f"m3-lastunit-help{i}@example.com")
        r = client.post(f"/api/v3/bargain-sessions/{s['id']}/help",
                        json={"device_id": f"m3-lastunit-dev{i}"},
                        headers=helper)
        assert r.status_code == 200, r.text
        assert _session(client, s["id"])["status"] == "success"
    monkeypatch.setattr(
        bargain_router, "_add_refund",
        _sleeping_wrapper(bargain_router._add_refund, seconds=0.4))
    results = _run_two(lambda i, tc: tc.post(
        f"/api/v3/bargain-sessions/{sessions[i]['id']}/purchase",
        headers=owners[i]))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 400], \
        f"expected one 200 + one 400, got {statuses}: " \
        f"{[r.text for r in results]}"
    db = TestingSession()
    try:
        camp_row = db.query(models_bargain.BargainCampaign).filter(
            models_bargain.BargainCampaign.id == camp["id"]).first()
        assert camp_row.stock_left == 0, f"stock_left={camp_row.stock_left}"
        bought = [db.query(models_bargain.BargainSession).filter(
            models_bargain.BargainSession.id == s["id"]).first()
            for s in sessions]
        assert sum(1 for x in bought if x.wallet_item_id is not None) == 1
    finally:
        db.close()


# ---------- 4. 退款走网关且记录网关 ref ----------
def test_m3_success_then_expired_full_refund_reason_expired(client):
    """M3-4a: success but never purchased, then the session expires:
    settle moves it to "expired" with ONE full refund (reason
    "expired", amount == paid_amount) through the gateway — status
    succeeded + gateway_ref re_test_… + the user-facing note. This
    is the refund reason no earlier test exercised (timeout-failed
    and price_cut were covered by test_p1_bargain_refund)."""
    camp = _make_campaign(client, "M3 成團未購過期", original=200, floor=190,
                          helper_limit=1)
    owner = _register_and_login(client, "m3-exp-owner@example.com")
    s = _start(client, owner, camp["id"])
    helper = _register_and_login(client, "m3-exp-helper@example.com")
    assert client.post(f"/api/v3/bargain-sessions/{s['id']}/help",
                       json={"device_id": "m3-exp-dev"},
                       headers=helper).status_code == 200
    assert _session(client, s["id"])["status"] == "success"
    db = TestingSession()
    try:
        row = db.query(models_bargain.BargainSession).filter(
            models_bargain.BargainSession.id == s["id"]).first()
        row.expires_at = row.expires_at - timedelta(days=2)
        db.commit()
    finally:
        db.close()
    s = _session(client, s["id"])
    assert s["status"] == "expired", s
    assert len(s["refunds"]) == 1, s["refunds"]
    refund = s["refunds"][0]
    assert refund["reason"] == "expired", refund
    assert refund["amount"] == 200.0, refund
    assert refund["status"] == "succeeded", refund
    assert refund["gateway_ref"].startswith("re_test_"), refund
    assert refund["note"] == "模擬退款，不涉及真實資金", refund


def test_m3_expire_sweep_twice_refunds_once(client):
    """M3-4b: the expire sweep is idempotent — running it twice over
    the same timed-out session creates exactly one refund row (one
    gateway refund, not two) and the second run settles nothing."""
    camp = _make_campaign(client, "M3 重複結算", original=200, floor=100,
                          stock=5, duration_minutes=0)
    owner = _register_and_login(client, "m3-sweep-owner@example.com")
    s = _start(client, owner, camp["id"])
    ah = _login(client, "admin@coupon-go.example")
    r = client.post("/api/v3/bargain/expire-sweep", headers=ah)
    assert r.status_code == 200, r.text
    assert r.json()["settled"] >= 1, r.json()
    r2 = client.post("/api/v3/bargain/expire-sweep", headers=ah)
    assert r2.status_code == 200, r2.text
    assert r2.json()["settled"] == 0, r2.json()
    s = _session(client, s["id"])
    assert s["status"] == "failed"
    assert len(s["refunds"]) == 1, s["refunds"]
    assert s["refunds"][0]["amount"] == 200.0
    assert s["refunds"][0]["gateway_ref"].startswith("re_test_")


# ---------- 5. 终态文案字段：用户可见部分不含内部状态用语 ----------
BANNED_COPY = ("模拟", "模擬", "测试", "測試", "开发", "開發", "内部",
               "內部", "simulated", "test_", "gateway", "Gateway",
               "pending", "succeeded", "failed", "payment_simulated")


def _collect_copy(obj, out):
    """Collect every user-facing prose field (message / note / detail /
    terms) from a response payload. Machine enums (status) and the
    gateway_ref identifier are deliberately NOT prose and are checked
    separately."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("message", "note", "detail", "additional_terms"):
                out.append(str(v))
            else:
                _collect_copy(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_copy(v, out)


def _assert_copy_clean(payload, where):
    texts = []
    _collect_copy(payload, texts)
    assert texts, f"{where}: no user-facing copy found to check"
    for t in texts:
        for bad in BANNED_COPY:
            assert bad not in t, f"{where}: internal wording {bad!r} in {t!r}"


def test_m3_terminal_copy_has_no_internal_wording(client):
    """M3-5: for every terminal state (success / failed / expired)
    plus the purchase and refund-retry responses, all user-facing
    prose (message / note / error detail / coupon terms) contains no
    simulation / test / dev / internal-state wording. The status
    enums (active/success/failed/expired, pending/succeeded/failed)
    stay machine values and are documented in docs/API.md — they are
    not copy. The gateway_ref value (re_test_…) is a gateway
    identifier, not copy, and is asserted separately."""
    # success terminal: help to floor, then purchase
    camp = _make_campaign(client, "M3 終態文案成團", original=200, floor=190,
                          helper_limit=1)
    owner = _register_and_login(client, "m3-copy-owner@example.com")
    s = _start(client, owner, camp["id"])
    helper = _register_and_login(client, "m3-copy-helper@example.com")
    r = client.post(f"/api/v3/bargain-sessions/{s['id']}/help",
                    json={"device_id": "m3-copy-dev"}, headers=helper)
    assert r.status_code == 200, r.text
    _assert_copy_clean(r.json(), "help->success")
    r = client.post(f"/api/v3/bargain-sessions/{s['id']}/purchase",
                    headers=owner)
    assert r.status_code == 200, r.text
    _assert_copy_clean(r.json(), "purchase")
    assert r.json()["refund"]["gateway_ref"].startswith("re_test_")

    # failed terminal: timeout before success
    camp2 = _make_campaign(client, "M3 終態文案失敗", original=200, floor=100,
                           duration_minutes=0)
    owner2 = _register_and_login(client, "m3-copy-owner2@example.com")
    s2 = _start(client, owner2, camp2["id"])
    s2 = _session(client, s2["id"])
    assert s2["status"] == "failed"
    _assert_copy_clean({"session": s2}, "failed session")
    r = client.post(f"/api/v3/bargain-sessions/{s2['id']}/help",
                    json={}, headers=helper)
    assert r.status_code == 400, r.text
    _assert_copy_clean(r.json(), "help on failed session")

    # expired terminal: success but never purchased
    camp3 = _make_campaign(client, "M3 終態文案過期", original=200, floor=190,
                           helper_limit=1)
    owner3 = _register_and_login(client, "m3-copy-owner3@example.com")
    s3 = _start(client, owner3, camp3["id"])
    helper3 = _register_and_login(client, "m3-copy-helper3@example.com")
    assert client.post(f"/api/v3/bargain-sessions/{s3['id']}/help",
                       json={"device_id": "m3-copy-dev3"},
                       headers=helper3).status_code == 200
    db = TestingSession()
    try:
        row = db.query(models_bargain.BargainSession).filter(
            models_bargain.BargainSession.id == s3["id"]).first()
        row.expires_at = row.expires_at - timedelta(days=2)
        db.commit()
    finally:
        db.close()
    s3 = _session(client, s3["id"])
    assert s3["status"] == "expired"
    _assert_copy_clean({"session": s3}, "expired session")

    # no internal-state flag anywhere in the client payloads
    for payload, where in ((r.json(), "purchase"),
                           ({"session": s2}, "failed"),
                           ({"session": s3}, "expired")):
        assert "payment_simulated" not in payload, where
        assert "payment_simulated" not in payload.get("session", {}), where
