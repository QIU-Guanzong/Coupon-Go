"""Dianping unify (GAP §5) tests: one shared layer for purchase /
redemption / refund / stats, one error-code / pagination / money
style, one CSV export helper, one audit-log filter set, and the
coupon-expiry scheduler job (§5.6).

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_dianping_unify.py -v
Own throwaway SQLite file (DIANPING5_TEST_DB) or a dedicated
UNIFY_TEST_POSTGRES_URL; never uses DATABASE_URL.
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
from app.models_line_b import AdminAuditLog  # noqa: E402
from app.seed import seed  # noqa: E402

TEST_DB = os.getenv(
    "DIANPING5_TEST_DB",
    os.path.join(os.getenv("TMPDIR", "/tmp"), "coupon_go_test_dianping5.db"))
PG_URL = os.getenv("UNIFY_TEST_POSTGRES_URL")
if PG_URL:
    engine = create_engine(PG_URL)
else:
    engine = create_engine(f"sqlite:///{TEST_DB}",
                           connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture(scope="module")
def client():
    if not PG_URL and os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    if PG_URL:
        Base.metadata.drop_all(bind=engine)
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


def _db():
    return TestingSession()


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


def _points(client, headers):
    return client.get("/api/v3/points", headers=headers).json()["balance"]


def _claim_and_qr(client, headers, coupon_id):
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": coupon_id}, headers=headers)
    assert r.status_code == 200, r.text
    wid = r.json()["wallet_item_id"]
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=headers)
    assert qr.status_code == 200, qr.text
    return wid, qr.json()["code"]


# ---------- 1. one clock ----------
def test_single_clock_everywhere(client):
    from app.timeutil import utcnow
    from app import (cashback_service, models_bargain, models_cashback,
                     models_line_b, models_line_f, models_packages,
                     scheduler)
    from app.routers import (admin, admin_v2, auth, bargain, coupons,
                             home, packages, redemption, reviews, vendor)
    for mod in (models, models_bargain, models_cashback, models_line_b,
                models_line_f, models_packages, cashback_service,
                scheduler, admin, admin_v2, auth, bargain, coupons,
                home, packages, redemption, reviews, vendor):
        assert getattr(mod, "_now", None) is utcnow, mod.__name__


# ---------- 2. pagination / money / vendor shops ----------
def test_paginate_envelope(client):
    from app.shared import paginate
    out = paginate(list(range(45)), 2, 20)
    assert out == {"current_page": 2, "total_pages": 3, "total": 45,
                   "items": list(range(20, 40))}
    out = paginate(list(range(45)), 9, 20)
    assert out["items"] == [] and out["total_pages"] == 3
    out = paginate([], 1)
    assert out == {"current_page": 1, "total_pages": 1, "total": 0,
                   "items": []}
    out = paginate(list(range(5)), 1, 100)
    assert out["total_pages"] == 1 and len(out["items"]) == 5


def test_money_two_decimal_places(client):
    from decimal import Decimal
    from app.shared import money
    assert money(Decimal("10.005")) == 10.01
    assert money(Decimal("138.10")) == 138.1
    assert money(0) == 0.0
    assert money(None) == 0.0
    assert money("12.345") == 12.35


def test_vendor_shop_ids_shared(client):
    from app.shared import vendor_shop_ids
    db = _db()
    try:
        vendor = db.query(models.User).filter_by(
            email="vendor@coupon-go.example").one()
        ids = vendor_shop_ids(db, vendor)
        assert ids and all(isinstance(i, int) for i in ids)
        assert len(ids) == db.query(models.Shop).filter_by(
            vendor_id=vendor.id).count()
        other = db.query(models.User).filter_by(
            email="demo@coupon-go.example").one()
        assert vendor_shop_ids(db, other) == []
    finally:
        db.close()


def test_page_size_param_on_admin_lists(client):
    h = _login(client, "admin@coupon-go.example")
    r = client.get("/api/v3/admin/coupons", params={"page": 1, "page_size": 2},
                   headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    # Seed: 36 coupons + 1 points coupon + 2 package-template coupons.
    assert body["total"] >= 37
    assert body["total_pages"] == (body["total"] + 1) // 2
    assert len(body["coupons"]) == 2
    r = client.get("/api/v3/admin/coupons", params={"page": 1},
                   headers=h)
    assert len(r.json()["coupons"]) == 20


def test_page_size_param_on_wallet_list(client):
    h = _register_and_login(client, "unify-page@example.com")
    _claim_and_qr(client, h, 1)
    r = client.get("/api/v3/get-favorite-coupons-list",
                   params={"page": 1, "page_size": 1}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1 and len(r.json()["items"]) == 1


# ---------- 3. points: one award seam (P1-C changes only award_redeem_points) ----------
def test_points_award_functions(client):
    from app import points as pts
    from app.deps import points_balance
    db = _db()
    try:
        user = db.query(models.User).filter_by(
            email="demo@coupon-go.example").one()
        before = points_balance(db, user.id)
        pts.award_claim_points(db, user.id, 1)
        pts.award_redeem_points(db, user.id, 99)
        pts.award_review_points(db, user.id, 99)
        assert points_balance(db, user.id) == before + 10 + 20 + 15
        row = db.query(models.PointTransaction).filter_by(
            user_id=user.id, reason="redeem", ref_id=99).one()
        assert row.delta == 20
        clawed = pts.clawback_points(db, user.id, "redeem", 99,
                                     revoke_reason="revoke", revoke_ref_id=7)
        assert clawed == 20
        assert points_balance(db, user.id) == before + 10 + 15
        # A second clawback of the same award is the caller's guard
        # (revoke is blocked by revoked_at); the seam just negates the
        # latest award it finds.
    finally:
        db.rollback()
        db.close()


def test_scan_uses_award_seam_and_snapshot(client):
    h = _register_and_login(client, "unify-scan@example.com")
    vh = _login(client, "vendor@coupon-go.example")
    wid, code = _claim_and_qr(client, h, 10)  # Price 點心拼盤 $138
    assert _points(client, h) == 10
    prep = client.post("/api/v3/new-scan-coupon/prepare",
                       json={"code": code}, headers=vh)
    assert prep.json()["can_redeem"] is True
    r = client.post("/api/v3/new-scan-coupon", json={
        "code": code, "prepare_token": prep.json()["prepare_token"],
        "idempotency_key": "unify-scan-1"}, headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["duplicate"] is False
    assert _points(client, h) == 30
    db = _db()
    try:
        red = db.query(models.Redemption).filter_by(id=r.json()["redemption_id"]).one()
        assert float(red.face_value) == 138.0  # settlement_basis snapshot
        item = db.query(models.WalletItem).filter_by(id=wid).one()
        assert item.source == "claim"
    finally:
        db.close()
    # Idempotent replay returns the original redemption.
    r2 = client.post("/api/v3/new-scan-coupon", json={
        "code": code, "idempotency_key": "unify-scan-1"}, headers=vh)
    assert r2.status_code == 200 and r2.json()["duplicate"] is True
    assert r2.json()["redemption_id"] == r.json()["redemption_id"]
    # Revoke claws the +20 back through the same seam.
    rv = client.post(f"/api/v3/redemptions/{r.json()['redemption_id']}/revoke",
                     headers=vh)
    assert rv.status_code == 200, rv.text
    assert _points(client, h) == 10


def test_redeem_item_is_the_single_consume_chain(client):
    from app.routers.coupons import _redeem_item, scan_coupon
    assert callable(_redeem_item)
    assert scan_coupon is not None


# ---------- 4. adopted shared modules: listing + notify ----------
def test_listing_single_source_of_truth(client):
    from app import listing
    from app.models_packages import REFUND_POLICIES as mp_policies
    from app.routers.packages import REFUND_POLICIES as pkg_policies
    assert listing.REFUND_POLICIES is mp_policies is pkg_policies
    assert listing.REFUND_POLICIES == ("anytime", "expiry_only",
                                       "non_refundable")
    now = _now()
    assert listing.live_status(now + timedelta(days=1),
                               now + timedelta(days=30), 5) == "scheduled"
    assert listing.live_status(now - timedelta(days=1),
                               now + timedelta(days=30), 5) == "active"
    assert listing.live_status(now - timedelta(days=2),
                               now - timedelta(days=1), 5) == "expired"
    assert listing.live_status(now - timedelta(days=1),
                               now + timedelta(days=30), 0) == "sold_out"


def test_notify_writes_row(client):
    from app.notify import notify
    db = _db()
    try:
        user = db.query(models.User).filter_by(
            email="demo@coupon-go.example").one()
        n = notify(db, user.id, "測試標題", "測試內容",
                   notification_type="coupon_expiring",
                   target_coupon_id=3, target_shop_id=1)
        db.commit()
        assert n.id is not None
        row = db.query(models.Notification).filter_by(id=n.id).one()
        assert row.notification_type == "coupon_expiring"
        assert row.target_coupon_id == 3 and row.target_shop_id == 1
    finally:
        db.rollback()
        db.close()


def test_issue_wallet_item_single_entry(client):
    from app.shared import issue_wallet_item
    db = _db()
    try:
        user = db.query(models.User).filter_by(
            email="demo@coupon-go.example").one()
        item = issue_wallet_item(db, user.id, 1, "exchange")
        assert item.source == "exchange" and item.status == 1
        assert item.order_id is None
    finally:
        db.rollback()
        db.close()


# ---------- 5. error codes: same message, now with a stable code ----------
def test_error_code_envelope(client):
    h = _register_and_login(client, "unify-err@example.com")
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": 99999}, headers=h)
    assert r.status_code == 404
    assert r.json() == {"detail": "找不到此優惠券", "code": "coupon_not_found"}
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": 2}, headers=h)
    assert r.status_code == 200, r.text
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": 2}, headers=h)
    assert r.status_code == 409
    assert r.json()["code"] == "coupon_already_claimed"
    assert r.json()["detail"] == "您已領取此 Coupon"
    vh = _login(client, "vendor@coupon-go.example")
    r = client.post("/api/v3/new-scan-coupon", json={"code": "000000"},
                    headers=vh)
    assert r.status_code == 400
    assert r.json()["code"] == "qr_invalid"


def test_points_exchange_uses_shared_issue_and_codes(client):
    h = _register_and_login(client, "unify-exchange@example.com")
    # Not enough points yet: 0 < 9.
    r = client.post("/api/v3/points/exchange", params={"coupon_id": 37},
                    headers=h)
    assert r.status_code == 400
    assert r.json()["code"] == "points_insufficient"
    # Earn 10 by claiming a coupon, then exchange the 9-point coupon.
    _claim_and_qr(client, h, 1)
    assert _points(client, h) == 10
    r = client.post("/api/v3/points/exchange", params={"coupon_id": 37},
                    headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["balance"] == 1
    db = _db()
    try:
        user = db.query(models.User).filter_by(
            email="unify-exchange@example.com").one()
        item = db.query(models.WalletItem).filter_by(
            user_id=user.id, coupon_id=37).one()
        assert item.source == "exchange"
    finally:
        db.close()


# ---------- 6. unified CSV export + audit-log filters (§5.9) ----------
def test_csv_export_helper_quotes_fields(client):
    from app.shared import csv_response
    resp = csv_response("test.csv", ["id", "name"],
                        [[1, "a,b"], [2, 'say "hi"']])
    assert resp.media_type == "text/csv; charset=utf-8"
    text = resp.body.decode("utf-8")
    assert text.splitlines()[0] == "id,name"
    assert '"a,b"' in text and '"say ""hi"""' in text


def test_redemption_export_csv_keeps_columns(client):
    h = _register_and_login(client, "unify-csv@example.com")
    vh = _login(client, "vendor@coupon-go.example")
    wid, code = _claim_and_qr(client, h, 10)
    prep = client.post("/api/v3/new-scan-coupon/prepare",
                       json={"code": code}, headers=vh)
    client.post("/api/v3/new-scan-coupon", json={
        "code": code, "prepare_token": prep.json()["prepare_token"],
        "idempotency_key": "unify-csv-1"}, headers=vh)
    r = client.get("/api/v3/vendor/redemptions/export",
                   params={"format": "csv"}, headers=vh)
    assert r.status_code == 200, r.text
    assert "text/csv" in r.headers["content-type"]
    lines = r.text.splitlines()
    assert lines[0] == ("redemption_id,wallet_item_id,coupon_name,shop_name,"
                        "scanned_at,revoked,revoked_at,idempotency_key")
    assert "unify-csv-1" in r.text


def test_settlement_and_order_csv_exports(client):
    h = _login(client, "admin@coupon-go.example")
    r = client.get("/api/v3/admin/settlements",
                   params={"format": "csv"}, headers=h)
    assert r.status_code == 200, r.text
    assert "text/csv" in r.headers["content-type"]
    header = r.text.splitlines()[0]
    assert "id" in header.split(",") and "shop_id" in header.split(",")
    assert "gross_amount" in header.split(",")
    r = client.get("/api/v3/admin/orders",
                   params={"format": "csv"}, headers=h)
    assert r.status_code == 200, r.text
    assert "text/csv" in r.headers["content-type"]
    assert "order_id" in r.text.splitlines()[0]


def test_audit_log_filters(client):
    db = _db()
    try:
        db.add(AdminAuditLog(admin_id=1, action="settlement.generate",
                             target_type="settlement", target_id=5,
                             detail="unify-test"))
        db.add(AdminAuditLog(admin_id=2, action="coupon.approve",
                             target_type="coupon", target_id=6,
                             detail="unify-test"))
        db.commit()
    finally:
        db.close()
    h = _login(client, "admin@coupon-go.example")
    r = client.get("/api/v3/admin/audit-logs", params={"action": "coupon.approve"},
                   headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1
    assert r.json()["logs"][0]["target_id"] == 6
    r = client.get("/api/v3/admin/audit-logs", params={"admin_id": 1},
                   headers=h)
    assert r.json()["total"] == 1
    assert r.json()["logs"][0]["action"] == "settlement.generate"
    r = client.get("/api/v3/admin/audit-logs",
                   params={"target_type": "coupon"}, headers=h)
    assert r.json()["total"] == 1


# ---------- 7. coupon-expiry scheduler job (§5.6) ----------
def _make_coupon(db, shop_id, vendor_id, name, valid_to):
    c = models.Coupon(
        shop_id=shop_id, vendor_id=vendor_id, coupon_type="Discount",
        coupon_name=name, percentage=90, valid_from=_now() - timedelta(days=1),
        valid_to=valid_to, total=10, remaining=10, status="active")
    db.add(c)
    db.flush()
    return c


def test_coupon_expiry_sweep(client):
    h = _register_and_login(client, "unify-expiry@example.com")
    ah = _login(client, "admin@coupon-go.example")
    db = _db()
    try:
        user = db.query(models.User).filter_by(
            email="unify-expiry@example.com").one()
        vendor = db.query(models.User).filter_by(
            email="vendor@coupon-go.example").one()
        shop = db.query(models.Shop).filter_by(vendor_id=vendor.id).first()
        soon = _make_coupon(db, shop.id, vendor.id, "快到期券",
                            _now() + timedelta(days=2))
        db.commit()
        soon_id = soon.id
        user_id = user.id
    finally:
        db.close()
    # Claim the soon-expiring coupon, then sweep: one reminder, no dupes.
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": soon_id}, headers=h)
    assert r.status_code == 200, r.text
    r = client.post("/api/v3/admin/coupon-expiry-sweep", headers=ah)
    assert r.status_code == 200, r.text
    assert r.json()["reminded"] == 1
    r = client.post("/api/v3/admin/coupon-expiry-sweep", headers=ah)
    assert r.json()["reminded"] == 0  # same-day reminder is not repeated
    db = _db()
    try:
        notes = db.query(models.Notification).filter_by(
            user_id=user_id, notification_type="coupon_expiring",
            target_coupon_id=soon_id).all()
        assert len(notes) == 1
        assert "2" in (notes[0].body or "")
        # An already-expired unredeemed item is flipped to expired (3).
        past = _make_coupon(db, 1, 1, "已過期券",
                            _now() - timedelta(hours=1))
        item = models.WalletItem(user_id=user_id, coupon_id=past.id,
                                 status=1, source="claim")
        db.add(item)
        db.commit()
        item_id = item.id
    finally:
        db.close()
    r = client.post("/api/v3/admin/coupon-expiry-sweep", headers=ah)
    assert r.status_code == 200, r.text
    assert r.json()["expired"] == 1
    db = _db()
    try:
        assert db.query(models.WalletItem).filter_by(id=item_id).one().status == 3
    finally:
        db.close()


def test_coupon_expiry_job_registered(client):
    from app.scheduler import JOB_NAMES, JOB_RUNNERS, ADVISORY_LOCK_KEYS
    assert "coupon-expiry" in JOB_NAMES
    assert "coupon-expiry" in JOB_RUNNERS
    assert "coupon-expiry" in ADVISORY_LOCK_KEYS
    assert ADVISORY_LOCK_KEYS["coupon-expiry"] not in (
        2026111701, 2026111702, 2026111703, 2026111704, 2026111705)


def test_scheduler_run_once_coupon_expiry(client):
    from app.scheduler import run_all_jobs
    results = run_all_jobs(session_factory=TestingSession)
    assert "coupon-expiry" in results
    job = results["coupon-expiry"]
    assert job.get("skipped") is None
    assert job.get("failed", 0) == 0
