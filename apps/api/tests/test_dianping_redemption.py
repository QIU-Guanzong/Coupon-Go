"""Dianping gap §4 tests: redemption recovery / over-window revoke / batch.

Covers GAP_ANALYSIS_20261121.md §4:
* 4.1 (P0-7) code lookup + supplementary (after-the-fact) scan — the
  customer never presents a code; the merchant finds the unused item by
  phone / order id and consumes it through the SAME consume chain as
  scan_coupon, with an AdminAuditLog entry for the paper trail.
* 4.2 (P1) over-window force-revoke by admin / support with a mandatory
  reason + audit log; the normal 10-minute revoke still refuses.
* 4.3 (P1) batch scan — several wallet items consumed in one call, one
  transaction, all-or-nothing, one idempotency key for the batch.
* 4.4 (P1) redemption method (scan/manual/supplementary/batch) recorded
  and visible in the vendor export, with date / coupon / revoked filters.
* 4.5 (P2, small) export filters.

Run:  cd ~/workspace/coupon-go-dianping-4 && ./run_tests.sh tests/test_dianping_redemption.py -v
Uses its own throwaway DB (DIANPING4_TEST_DB, default under the block's
workdir .pytest-tmp) so parallel blocks do not share /tmp test DBs.
Postgres mode: set DIANPING4_POSTGRES_URL (default
postgresql+psycopg2://coupon_go:coupon_go@localhost:5432/coupon_go_test_dianping4;
the database must exist) — the module then runs against real
PostgreSQL, where SELECT ... FOR UPDATE actually locks.
"""
import os
import json
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import models  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models_line_b import AdminAuditLog  # noqa: E402
from app.seed import seed  # noqa: E402

from tests.db_isolation import sqlite_test_path
TEST_DB = sqlite_test_path("redemption-recovery.db")
PG_URL = os.environ.get("DIANPING4_POSTGRES_URL", "")


def _pg_reachable():
    if os.environ.get("DIANPING4_FORCE_SQLITE", "") == "1":
        return False
    if not PG_URL:
        return False
    from sqlalchemy.engine import make_url
    parsed = make_url(PG_URL)
    assert parsed.get_backend_name() == 'postgresql' and not parsed.password
    assert parsed.host in (None, '', '127.0.0.1', 'localhost', '::1')
    assert (parsed.database or '').startswith('coupon_redemption_test_')
    try:
        from sqlalchemy import text as _text
        probe = create_engine(PG_URL, connect_args={"connect_timeout": 2})
        with probe.connect() as conn:
            conn.execute(_text("SELECT 1"))
        probe.dispose()
        return True
    except Exception as exc:
        raise RuntimeError("Explicit isolated PostgreSQL unavailable") from exc


BACKEND = "postgres" if _pg_reachable() else "sqlite"

if BACKEND == "postgres":
    engine = create_engine(PG_URL, pool_size=5, max_overflow=5)
else:
    engine = create_engine(f"sqlite:///{TEST_DB}",
                           connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine)


@pytest.fixture(scope="module")
def client():
    if BACKEND == "postgres":
        Base.metadata.drop_all(bind=engine)
    else:
        os.makedirs(os.path.dirname(TEST_DB), exist_ok=True)
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
    engine.dispose()


def _db():
    return TestingSession()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _register_and_login(client, email, phone=None, role="customer"):
    assert client.post("/api/v3/new-register", json={
        "email": email, "password": "Demo123!", "name": "Test",
        "role": role}).status_code == 200
    code = client.get("/api/v3/_dev/otp", params={"email": email}).json()["code"]
    r = client.post("/api/v3/new-verify-otp", json={
        "email": email, "code": code, "purpose": "register"})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    if phone:
        db = _db()
        user = db.query(models.User).filter_by(email=email).one()
        user.phone = phone
        db.commit()
        db.close()
    return headers


def _login(client, email):
    r = client.post("/api/v3/login", json={"email": email, "password": "Demo123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _vendor_headers(client):
    return _login(client, "vendor@coupon-go.example")


def _admin_headers(client):
    return _login(client, "admin@coupon-go.example")


def _support_headers(client):
    return _login(client, "support@coupon-go.example")


def _claim(client, headers, coupon_id):
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": coupon_id}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["wallet_item_id"]


def _claim_and_qr(client, headers, coupon_id):
    wid = _claim(client, headers, coupon_id)
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=headers)
    assert qr.status_code == 200, qr.text
    return wid, qr.json()["code"]


def _points(client, headers):
    return client.get("/api/v3/points", headers=headers).json()["balance"]


def _scan(client, vh, code, **extra):
    body = {"code": code}
    body.update(extra)
    return client.post("/api/v3/new-scan-coupon", json=body, headers=vh)


def _backdate_redemption(redemption_id, minutes):
    db = _db()
    red = db.query(models.Redemption).filter_by(id=redemption_id).one()
    red.scanned_at = _now() - timedelta(minutes=minutes)
    db.commit()
    db.close()


def _audit_entries(action):
    db = _db()
    rows = db.query(AdminAuditLog).filter_by(action=action).all()
    db.close()
    return rows


# ---------- 4.1 lookup ----------

def test_lookup_by_phone_finds_unused_item_without_leaking_code_or_phone(client):
    h = _register_and_login(client, "dp4-lookup@example.com", phone="13800138000")
    vh = _vendor_headers(client)
    wid = _claim(client, h, 2)  # claimed, never presented: no live code
    r = client.get("/api/v3/merchant/redemptions/lookup",
                   params={"phone": "13800138000"}, headers=vh)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["wallet_item_id"] for i in items] == [wid]
    item = items[0]
    assert item["customer_phone_last4"] == "8000"
    assert "13800138000" not in r.text  # full phone never returned
    assert "code" not in item  # live code never exposed to the merchant


def test_lookup_requires_at_least_one_param(client):
    vh = _vendor_headers(client)
    r = client.get("/api/v3/merchant/redemptions/lookup", headers=vh)
    assert r.status_code == 400, r.text


def test_lookup_by_phone_returns_empty_for_unknown_phone(client):
    vh = _vendor_headers(client)
    r = client.get("/api/v3/merchant/redemptions/lookup",
                   params={"phone": "13999999999"}, headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["items"] == []


def test_lookup_by_order_id_finds_package_item_with_quantity(client):
    h = _register_and_login(client, "dp4-orderlookup@example.com",
                            phone="13800138001")
    vh = _vendor_headers(client)
    r = client.post("/api/v3/packages/1/purchase", headers=h,
                    json={"payment_method": "fps", "quantity": 2})
    assert r.status_code == 200, r.text
    order_id = r.json()["order"]["id"]
    pay = client.post(f"/api/v3/orders/{order_id}/demo-payment", headers=h)
    assert pay.status_code == 200, pay.text
    r = client.get("/api/v3/merchant/redemptions/lookup",
                   params={"order_id": order_id}, headers=vh)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1  # one WalletItem per package order, whatever qty
    assert items[0]["order_id"] == order_id
    assert items[0]["package_quantity"] == 2


# ---------- 4.1 supplementary scan ----------

def test_supplementary_scan_consumes_without_code_and_logs_audit(client):
    h = _register_and_login(client, "dp4-supp@example.com", phone="13800138002")
    vh = _vendor_headers(client)
    wid = _claim(client, h, 3)  # never presented: no code, no prepare token
    before = len(_audit_entries("redemption.supplementary_scan"))
    r = client.post("/api/v3/merchant/redemptions/supplementary-scan",
                    json={"wallet_item_id": wid,
                          "idempotency_key": "dp4-supp-1",
                          "reason": "顧客離店後發現未核銷"},
                    headers=vh)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["wallet_item_id"] == wid
    assert body["method"] == "supplementary"
    assert _points(client, h) == 30  # +10 claim, +20 redeem — same hook
    db = _db()
    assert db.query(models.WalletItem).filter_by(id=wid).one().status == 2
    red = db.query(models.Redemption).filter_by(id=body["redemption_id"]).one()
    assert red.method == "supplementary"
    db.close()
    after = _audit_entries("redemption.supplementary_scan")
    assert len(after) == before + 1
    assert str(wid) in (after[-1].detail or "")


def test_supplementary_scan_idempotent_replay(client):
    h = _register_and_login(client, "dp4-suppreplay@example.com")
    vh = _vendor_headers(client)
    wid = _claim(client, h, 4)
    body = {"wallet_item_id": wid, "idempotency_key": "dp4-supp-replay-1"}
    first = client.post("/api/v3/merchant/redemptions/supplementary-scan",
                        json=body, headers=vh)
    assert first.status_code == 200, first.text
    second = client.post("/api/v3/merchant/redemptions/supplementary-scan",
                         json=body, headers=vh)
    assert second.status_code == 200, second.text
    assert second.json()["duplicate"] is True
    assert second.json()["redemption_id"] == first.json()["redemption_id"]


def test_supplementary_scan_twice_without_key_rejected(client):
    h = _register_and_login(client, "dp4-supp2x@example.com")
    vh = _vendor_headers(client)
    wid = _claim(client, h, 5)
    r = client.post("/api/v3/merchant/redemptions/supplementary-scan",
                    json={"wallet_item_id": wid}, headers=vh)
    assert r.status_code == 200, r.text
    r = client.post("/api/v3/merchant/redemptions/supplementary-scan",
                    json={"wallet_item_id": wid}, headers=vh)
    assert r.status_code == 400, r.text


def test_supplementary_scan_wrong_customer_role_forbidden(client):
    h = _register_and_login(client, "dp4-supp403@example.com")
    wid = _claim(client, h, 6)
    r = client.post("/api/v3/merchant/redemptions/supplementary-scan",
                    json={"wallet_item_id": wid}, headers=h)
    assert r.status_code == 403, r.text


# ---------- 4.2 force revoke ----------

def test_force_review_allows_staff_to_verify_before_and_after_revoke(client):
    customer = _register_and_login(client, "dp4-force-review@example.com")
    vendor = _vendor_headers(client)
    admin = _admin_headers(client)
    support = _support_headers(client)
    _, code = _claim_and_qr(client, customer, 2)
    scanned = _scan(client, vendor, code, idempotency_key="dp4-force-review")
    assert scanned.status_code == 200, scanned.text
    rid = scanned.json()["redemption_id"]
    path = f"/api/v3/admin/redemptions/{rid}/force-review"
    for headers in (admin, support):
        result = client.get(path, headers=headers)
        assert result.status_code == 200, result.text
        assert result.json()["redemption_id"] == rid
        assert result.json()["coupon_name"]
        assert result.json()["shop_name"]
        assert result.json()["revoked"] is False
    for headers in (customer, vendor):
        assert client.get(path, headers=headers).status_code == 403
    assert client.get("/api/v3/admin/redemptions/999999999/force-review", headers=admin).status_code == 404
    revoked = client.post(f"/api/v3/admin/redemptions/{rid}/force-revoke",
                          json={"reason": "核對後撤銷"}, headers=support)
    assert revoked.status_code == 200, revoked.text
    assert client.get(path, headers=support).json()["revoked"] is True


def test_force_revoke_over_window_by_admin_with_reason_and_audit(client):
    h = _register_and_login(client, "dp4-force@example.com")
    vh = _vendor_headers(client)
    ah = _admin_headers(client)
    _, code = _claim_and_qr(client, h, 2)
    r = _scan(client, vh, code, idempotency_key="dp4-force-1")
    assert r.status_code == 200, r.text
    rid = r.json()["redemption_id"]
    _backdate_redemption(rid, 30)  # 30 min ago: past the 10-min window
    normal = client.post(f"/api/v3/redemptions/{rid}/revoke", headers=vh)
    assert normal.status_code == 400, normal.text
    before = len(_audit_entries("redemption.force_revoke"))
    forced = client.post(f"/api/v3/admin/redemptions/{rid}/force-revoke",
                         json={"reason": "掃錯碼，顧客投訴"}, headers=ah)
    assert forced.status_code == 200, forced.text
    assert forced.json()["forced"] is True
    assert forced.json()["minutes_over_window"] >= 20
    assert _points(client, h) == 10  # +20 redeem points clawed back
    after = _audit_entries("redemption.force_revoke")
    assert len(after) == before + 1
    assert "掃錯碼" in (after[-1].detail or "")
    assert after[-1].target_id == rid


def test_force_revoke_requires_reason_and_admin_or_support(client):
    h = _register_and_login(client, "dp4-force422@example.com")
    vh = _vendor_headers(client)
    ah = _admin_headers(client)
    sh = _support_headers(client)
    _, code = _claim_and_qr(client, h, 3)
    r = _scan(client, vh, code, idempotency_key="dp4-force-422-1")
    rid = r.json()["redemption_id"]
    _backdate_redemption(rid, 30)
    no_reason = client.post(f"/api/v3/admin/redemptions/{rid}/force-revoke",
                            json={}, headers=ah)
    assert no_reason.status_code in (400, 422), no_reason.text
    vendor_try = client.post(f"/api/v3/admin/redemptions/{rid}/force-revoke",
                             json={"reason": "x"}, headers=vh)
    assert vendor_try.status_code == 403, vendor_try.text
    ok = client.post(f"/api/v3/admin/redemptions/{rid}/force-revoke",
                     json={"reason": "客服代處理"}, headers=sh)
    assert ok.status_code == 200, ok.text


def test_force_revoke_already_revoked_rejected(client):
    h = _register_and_login(client, "dp4-force2x@example.com")
    vh = _vendor_headers(client)
    ah = _admin_headers(client)
    _, code = _claim_and_qr(client, h, 4)
    r = _scan(client, vh, code, idempotency_key="dp4-force-2x-1")
    rid = r.json()["redemption_id"]
    _backdate_redemption(rid, 30)
    first = client.post(f"/api/v3/admin/redemptions/{rid}/force-revoke",
                        json={"reason": "第一次"}, headers=ah)
    assert first.status_code == 200, first.text
    second = client.post(f"/api/v3/admin/redemptions/{rid}/force-revoke",
                         json={"reason": "第二次"}, headers=ah)
    assert second.status_code == 400, second.text


# ---------- 4.3 batch scan ----------

def test_batch_redeems_multiple_items_in_one_call(client):
    h = _register_and_login(client, "dp4-batch@example.com")
    vh = _vendor_headers(client)
    _, code1 = _claim_and_qr(client, h, 2)
    _, code2 = _claim_and_qr(client, h, 3)
    r = client.post("/api/v3/new-scan-coupon/batch",
                    json={"codes": [code1, code2],
                          "idempotency_key": "dp4-batch-1"}, headers=vh)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["redeemed_count"] == 2
    assert body["duplicate_count"] == 0
    assert all(res["duplicate"] is False for res in body["results"])
    assert _points(client, h) == 60  # 2x (+10 claim, +20 redeem)


def test_batch_with_prepare_tokens(client):
    h = _register_and_login(client, "dp4-batchprep@example.com")
    vh = _vendor_headers(client)
    _, code1 = _claim_and_qr(client, h, 5)
    _, code2 = _claim_and_qr(client, h, 6)
    tokens = []
    for code in (code1, code2):
        prep = client.post("/api/v3/new-scan-coupon/prepare",
                           json={"code": code}, headers=vh)
        assert prep.status_code == 200, prep.text
        tokens.append(prep.json()["prepare_token"])
    r = client.post("/api/v3/new-scan-coupon/batch",
                    json={"codes": [code1, code2], "prepare_tokens": tokens,
                          "idempotency_key": "dp4-batch-prep-1"}, headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["redeemed_count"] == 2


def test_batch_atomic_rollback_on_one_bad_code(client):
    h = _register_and_login(client, "dp4-batchatomic@example.com")
    vh = _vendor_headers(client)
    wid, code1 = _claim_and_qr(client, h, 2)
    r = client.post("/api/v3/new-scan-coupon/batch",
                    json={"codes": [code1, "000000"],
                          "idempotency_key": "dp4-batch-atomic-1"}, headers=vh)
    assert r.status_code == 400, r.text
    assert r.json()["results"][0]["status"] == "ok"
    assert r.json()["results"][1]["status"] == "error"
    db = _db()
    assert db.query(models.WalletItem).filter_by(id=wid).one().status == 1
    assert db.query(models.Redemption).filter_by(wallet_item_id=wid).first() is None
    assert db.query(models.RedemptionBatch).filter_by(request_key="dp4-batch-atomic-1").first() is None
    db.close()


def test_batch_replay_returns_duplicates_without_new_redemptions(client):
    h = _register_and_login(client, "dp4-batchreplay@example.com")
    vh = _vendor_headers(client)
    _, code1 = _claim_and_qr(client, h, 7)
    _, code2 = _claim_and_qr(client, h, 8)
    body = {"codes": [code1, code2], "idempotency_key": "dp4-batch-replay-1"}
    first = client.post("/api/v3/new-scan-coupon/batch", json=body, headers=vh)
    assert first.status_code == 200, first.text
    second = client.post("/api/v3/new-scan-coupon/batch", json=body, headers=vh)
    assert second.status_code == 200, second.text
    assert second.json()["redeemed_count"] == 0
    assert second.json()["duplicate_count"] == 2
    db = _db()
    batch = db.query(models.RedemptionBatch).filter_by(request_key="dp4-batch-replay-1").one()
    ids = json.loads(batch.redemption_ids)
    assert len(ids) == 2
    assert db.query(models.Redemption).filter(models.Redemption.id.in_(ids)).count() == 2
    db.close()


def test_batch_requires_idempotency_key_and_caps_at_20(client):
    vh = _vendor_headers(client)
    r = client.post("/api/v3/new-scan-coupon/batch",
                    json={"codes": ["123456"]}, headers=vh)
    assert r.status_code == 422, r.text
    r = client.post("/api/v3/new-scan-coupon/batch",
                    json={"codes": ["123456"] * 21,
                          "idempotency_key": "dp4-batch-cap-1"}, headers=vh)
    assert r.status_code == 422, r.text


# ---------- 4.4 method recorded + 4.5 export filters ----------

def test_scan_method_recorded_and_export_filters(client):
    h = _register_and_login(client, "dp4-method@example.com")
    vh = _vendor_headers(client)
    _, code = _claim_and_qr(client, h, 9)
    r = _scan(client, vh, code, idempotency_key="dp4-method-1", method="manual")
    assert r.status_code == 200, r.text
    rid = r.json()["redemption_id"]
    exp = client.get("/api/v3/vendor/redemptions/export",
                     params={"method": "manual"}, headers=vh)
    assert exp.status_code == 200, exp.text
    recs = [rec for rec in exp.json()["records"]
            if rec["redemption_id"] == rid]
    assert len(recs) == 1
    assert recs[0]["method"] == "manual"
    assert recs[0]["scanned_by_name"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    exp2 = client.get("/api/v3/vendor/redemptions/export",
                      params={"from": today, "to": today}, headers=vh)
    assert exp2.status_code == 200, exp2.text
    assert any(rec["redemption_id"] == rid
               for rec in exp2.json()["records"])
    exp3 = client.get("/api/v3/vendor/redemptions/export",
                      params={"from": "2000-01-01", "to": "2000-01-02"},
                      headers=vh)
    assert exp3.status_code == 200, exp3.text
    assert all(rec["redemption_id"] != rid
               for rec in exp3.json()["records"])


def test_batch_reusing_key_with_different_request_is_rejected(client):
    h = _register_and_login(client, 'dp4-batch-change@example.com')
    vendor = _vendor_headers(client)
    _, first = _claim_and_qr(client, h, 2)
    second_wallet, second = _claim_and_qr(client, h, 3)
    url = '/api/v3/new-scan-coupon/batch'
    assert client.post(url, json={'codes': [first], 'idempotency_key': 'same-key'}, headers=vendor).status_code == 200
    changed = client.post(url, json={'codes': [second], 'idempotency_key': 'same-key'}, headers=vendor)
    assert changed.status_code == 409
    with _db() as db:
        assert db.get(models.WalletItem, second_wallet).status == 1
        assert not db.query(models.Redemption).filter_by(wallet_item_id=second_wallet).first()


def test_batch_failure_after_first_consume_rolls_back_everything(client, monkeypatch):
    from app.routers import redemption as router
    h = _register_and_login(client, 'dp4-batch-rollback@example.com')
    vendor = _vendor_headers(client)
    w1, c1 = _claim_and_qr(client, h, 4)
    w2, c2 = _claim_and_qr(client, h, 5)
    original = router._redeem_item
    def fail_second(db, item, *args, **kwargs):
        if item.id == w2:
            raise HTTPException(400, 'simulated consume failure')
        return original(db, item, *args, **kwargs)
    monkeypatch.setattr(router, '_redeem_item', fail_second)
    response = client.post('/api/v3/new-scan-coupon/batch',
                           json={'codes': [c1, c2], 'idempotency_key': 'batch-rollback'}, headers=vendor)
    assert response.status_code == 400
    with _db() as db:
        assert [db.get(models.WalletItem, wid).status for wid in (w1, w2)] == [1, 1]
        assert db.query(models.Redemption).filter(models.Redemption.wallet_item_id.in_((w1, w2))).count() == 0
        assert db.query(models.RedemptionBatch).filter_by(request_key='batch-rollback').count() == 0


def test_batch_replay_after_revoke_requires_new_presented_code(client):
    h = _register_and_login(client, 'dp4-batch-revoked@example.com')
    vendor = _vendor_headers(client)
    _, code = _claim_and_qr(client, h, 6)
    body = {'codes': [code], 'idempotency_key': 'batch-then-revoke'}
    first = client.post('/api/v3/new-scan-coupon/batch', json=body, headers=vendor)
    assert first.status_code == 200, first.text
    red_id = first.json()['results'][0]['redemption_id']
    assert client.post(f'/api/v3/redemptions/{red_id}/revoke', headers=vendor).status_code == 200
    assert client.post('/api/v3/new-scan-coupon/batch', json=body, headers=vendor).status_code == 409


@pytest.mark.skipif(BACKEND != 'postgres', reason='requires PostgreSQL row locks')
def test_batch_and_single_scan_race_never_partially_consume_batch(client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    h = _register_and_login(client, 'dp4-batch-race@example.com')
    vendor = _vendor_headers(client)
    w1, c1 = _claim_and_qr(client, h, 7)
    w2, c2 = _claim_and_qr(client, h, 8)
    barrier = Barrier(2)
    def batch():
        with TestClient(app) as other:
            barrier.wait(timeout=10)
            return other.post('/api/v3/new-scan-coupon/batch',
                              json={'codes': [c1, c2], 'idempotency_key': 'batch-race'}, headers=vendor)
    def single():
        with TestClient(app) as other:
            barrier.wait(timeout=10)
            return other.post('/api/v3/new-scan-coupon', json={'code': c1}, headers=vendor)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(batch), pool.submit(single)]
        b, s = [future.result(timeout=30) for future in futures]
    assert (b.status_code, s.status_code) in ((200, 400), (400, 200)), (b.text, s.text)
    with _db() as db:
        states = [db.get(models.WalletItem, wid).status for wid in (w1, w2)]
        assert states == ([2, 2] if b.status_code == 200 else [2, 1])
        assert db.query(models.Redemption).filter(models.Redemption.wallet_item_id.in_((w1, w2))).count() == states.count(2)


@pytest.mark.skipif(BACKEND != 'postgres', reason='requires PostgreSQL row locks')
def test_same_batch_concurrent_replay_is_one_atomic_result(client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    h = _register_and_login(client, 'dp4-batch-idempotent-race@example.com')
    vendor = _vendor_headers(client)
    w1, c1 = _claim_and_qr(client, h, 9)
    w2, c2 = _claim_and_qr(client, h, 10)
    body = {'codes': [c1, c2], 'idempotency_key': 'batch-same-race'}
    barrier = Barrier(2)
    def submit():
        with TestClient(app) as other:
            barrier.wait(timeout=10)
            return other.post('/api/v3/new-scan-coupon/batch', json=body, headers=vendor)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit), pool.submit(submit)]
        responses = [future.result(timeout=30) for future in futures]
    assert [response.status_code for response in responses] == [200, 200]
    assert sorted(response.json()['redeemed_count'] for response in responses) == [0, 2]
    assert sorted(response.json()['duplicate_count'] for response in responses) == [0, 2]
    with _db() as db:
        assert db.query(models.Redemption).filter(models.Redemption.wallet_item_id.in_((w1, w2))).count() == 2
        assert db.query(models.RedemptionBatch).filter_by(request_key='batch-same-race').count() == 1


def test_batch_accepts_long_key_and_replays_without_duplicate_entries(client):
    h = _register_and_login(client, 'dp4-batch-long-key@example.com')
    vendor = _vendor_headers(client)
    wid, code = _claim_and_qr(client, h, 11)
    body = {'codes': [code], 'idempotency_key': 'k' * 80}
    first = client.post('/api/v3/new-scan-coupon/batch', json=body, headers=vendor)
    second = client.post('/api/v3/new-scan-coupon/batch', json=body, headers=vendor)
    assert first.status_code == second.status_code == 200
    assert second.json()['duplicate_count'] == 1
    with _db() as db:
        assert db.query(models.Redemption).filter_by(wallet_item_id=wid).count() == 1


def test_batch_rejects_unimplemented_branch_selection(client):
    """Until branch support lands, a selection must never be silently ignored."""
    vendor = _vendor_headers(client)
    response = client.post('/api/v3/new-scan-coupon/batch',
        json={'codes': ['123456'], 'idempotency_key': 'branch-not-yet', 'branch_ids': [1]}, headers=vendor)
    assert response.status_code == 422
