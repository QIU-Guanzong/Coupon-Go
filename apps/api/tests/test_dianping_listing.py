"""Dianping listing (GAP §3) tests: coupon edit / pause / resume / unlist,
package review flow (create -> pending_review -> approve -> active /
scheduled), scheduled listing sweep, bulk coupon creation, templates,
per-user claim limit, sale_price / refund-policy listing calibre,
merchant image upload, admin package review queue.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_dianping_listing.py -v
Own throwaway SQLite file (/tmp/coupon_go_test_listing.db); Postgres run
uses LISTING_TEST_POSTGRES_URL (same pattern as M8_TEST_POSTGRES_URL).
"""
import io
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
from app.models_packages import Package  # noqa: E402
from app.seed import seed  # noqa: E402

TEST_DB = "/tmp/coupon_go_test_listing.db"
PG_URL = os.getenv("LISTING_TEST_POSTGRES_URL")
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


def _coupon_body(**overrides):
    now = _now()
    body = {
        "shop_id": 1, "coupon_type": "Discount", "coupon_name": "上架測試券",
        "percentage": 90,
        "valid_from": (now - timedelta(days=1)).isoformat(),
        "valid_to": (now + timedelta(days=30)).isoformat(),
        "total": 10,
    }
    body.update(overrides)
    return body


def _create_coupon(client, **overrides):
    r = client.post("/api/v3/coupon-previewed", json=_coupon_body(**overrides),
                    headers=_vendor_headers(client))
    assert r.status_code == 200, r.text
    return r.json()["coupon"]


def _approve_coupon(client, coupon_id, approve=True, reason=""):
    return client.post(f"/api/v3/vendor/coupons/{coupon_id}/approve",
                       params={"approve": approve, "reason": reason},
                       headers=_admin_headers(client))


def _package_body(**overrides):
    now = _now()
    body = {
        "shop_id": 1, "name": "上架測試套餐",
        "items": [{"name": "主菜一份", "qty": 1}],
        "person_count": 2, "original_price": 200, "package_price": 150,
        "valid_from": (now - timedelta(days=1)).isoformat(),
        "valid_to": (now + timedelta(days=30)).isoformat(),
        "usage_rules": {"need_booking": True},
        "stock_total": 5, "refund_policy": "anytime",
    }
    body.update(overrides)
    return body


def _create_package(client, **overrides):
    r = client.post("/api/v3/packages", json=_package_body(**overrides),
                    headers=_vendor_headers(client))
    assert r.status_code == 201, r.text
    return r.json()["package"]


def _approve_package(client, package_id, approve=True, reason=""):
    return client.post(f"/api/v3/admin/packages/{package_id}/approve",
                       params={"approve": approve, "reason": reason},
                       headers=_admin_headers(client))


def _sweep():
    from app.listing import sweep_listing_schedule
    db = TestingSession()
    try:
        return sweep_listing_schedule(db)
    finally:
        db.close()


# ---------- P0-6: package review flow ----------

def test_package_create_is_pending_review_not_active(client):
    pkg = _create_package(client, name="待審套餐")
    assert pkg["status"] == "pending_review"
    # Not purchasable before approval.
    h = _register_and_login(client, "listing-pending@example.com")
    r = client.post(f"/api/v3/packages/{pkg['id']}/purchase",
                    json={"payment_method": "fps"}, headers=h)
    assert r.status_code == 400, r.text
    # Not on the public list either.
    r = client.get("/api/v3/packages", params={"status": "active"})
    assert all(p["id"] != pkg["id"] for p in r.json()["packages"])


def test_package_approve_goes_active_and_purchasable(client):
    pkg = _create_package(client, name="審批通過套餐")
    r = _approve_package(client, pkg["id"], approve=True)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"
    assert r.json()["coupon_status"] == "active"
    h = _register_and_login(client, "listing-approved@example.com")
    r = client.post(f"/api/v3/packages/{pkg['id']}/purchase",
                    json={"payment_method": "fps"}, headers=h)
    assert r.status_code == 200, r.text


def test_package_approve_future_valid_from_is_scheduled(client):
    now = _now()
    pkg = _create_package(
        client, name="定時套餐",
        valid_from=(now + timedelta(days=2)).isoformat(),
        valid_to=(now + timedelta(days=30)).isoformat())
    r = _approve_package(client, pkg["id"], approve=True)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "scheduled"
    # Not purchasable while scheduled.
    h = _register_and_login(client, "listing-sched@example.com")
    r = client.post(f"/api/v3/packages/{pkg['id']}/purchase",
                    json={"payment_method": "fps"}, headers=h)
    assert r.status_code == 400, r.text
    # Sweep does nothing before valid_from, then activates once it passes.
    result = _sweep()
    assert result["packages_activated"] >= 0
    db = TestingSession()
    try:
        row = db.query(Package).filter(Package.id == pkg["id"]).first()
        assert row.status == "scheduled"
        row.valid_from = _now() - timedelta(hours=1)
        db.commit()
    finally:
        db.close()
    result = _sweep()
    assert result["packages_activated"] >= 1, result
    db = TestingSession()
    try:
        assert db.query(Package).filter(
            Package.id == pkg["id"]).first().status == "active"
    finally:
        db.close()


def test_package_reject_with_reason_and_resubmit(client):
    pkg = _create_package(client, name="被駁回套餐")
    r = _approve_package(client, pkg["id"], approve=False,
                         reason="價格填錯，請修正")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"
    assert "價格填錯" in (r.json().get("reason") or "")
    # Rejected can resubmit directly, or be edited first (edit -> draft).
    r = client.post(f"/api/v3/vendor/packages/{pkg['id']}/submit",
                    headers=_vendor_headers(client))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending_review"
    r = _approve_package(client, pkg["id"], approve=False, reason="仍不通過")
    assert r.json()["status"] == "rejected"
    r = client.patch(f"/api/v3/vendor/packages/{pkg['id']}",
                     json={"package_price": 120},
                     headers=_vendor_headers(client))
    assert r.status_code == 200, r.text
    assert r.json()["package"]["status"] == "draft"
    assert r.json()["package"]["package_price"] == 120
    r = client.post(f"/api/v3/vendor/packages/{pkg['id']}/submit",
                    headers=_vendor_headers(client))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending_review"
    r = _approve_package(client, pkg["id"], approve=True)
    assert r.json()["status"] == "active"


def test_package_approve_twice_is_400(client):
    pkg = _create_package(client, name="重複審批套餐")
    assert _approve_package(client, pkg["id"], approve=True).status_code == 200
    r = _approve_package(client, pkg["id"], approve=True)
    assert r.status_code == 400, r.text


def test_package_pause_resume_unlist(client):
    pkg = _create_package(client, name="暫停套餐")
    _approve_package(client, pkg["id"], approve=True)
    vh = _vendor_headers(client)
    r = client.post(f"/api/v3/vendor/packages/{pkg['id']}/pause", headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "paused"
    h = _register_and_login(client, "listing-paused@example.com")
    r = client.post(f"/api/v3/packages/{pkg['id']}/purchase",
                    json={"payment_method": "fps"}, headers=h)
    assert r.status_code == 400, r.text
    r = client.post(f"/api/v3/vendor/packages/{pkg['id']}/resume", headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"
    r = client.post(f"/api/v3/vendor/packages/{pkg['id']}/unlist", headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "draft"
    r = client.post(f"/api/v3/vendor/packages/{pkg['id']}/pause", headers=vh)
    assert r.status_code == 400, r.text  # draft cannot be paused


def test_package_edit_active_requires_re_review(client):
    pkg = _create_package(client, name="改價套餐")
    _approve_package(client, pkg["id"], approve=True)
    r = client.patch(f"/api/v3/vendor/packages/{pkg['id']}",
                     json={"package_price": 99},
                     headers=_vendor_headers(client))
    assert r.status_code == 200, r.text
    assert r.json()["package"]["status"] == "pending_review"
    assert r.json()["package"]["package_price"] == 99
    # Off the shelf while under re-review.
    h = _register_and_login(client, "listing-rereview@example.com")
    r = client.post(f"/api/v3/packages/{pkg['id']}/purchase",
                    json={"payment_method": "fps"}, headers=h)
    assert r.status_code == 400, r.text
    assert _approve_package(client, pkg["id"], approve=True).json()["status"] == "active"


def test_package_edit_validation(client):
    pkg = _create_package(client, name="校驗套餐")
    vh = _vendor_headers(client)
    r = client.patch(f"/api/v3/vendor/packages/{pkg['id']}",
                     json={"package_price": 999, "original_price": 200},
                     headers=vh)
    assert r.status_code == 400, r.text  # 套餐價不可高於原價
    r = client.patch(f"/api/v3/vendor/packages/{pkg['id']}",
                     json={"refund_policy": "nope"}, headers=vh)
    assert r.status_code == 400, r.text
    r = client.patch(f"/api/v3/vendor/packages/{pkg['id']}",
                     json={"items": [{"name": "", "qty": 1}]}, headers=vh)
    assert r.status_code == 422, r.text
    r = client.patch(f"/api/v3/vendor/packages/{pkg['id']}",
                     json={"items": [{"name": "主菜", "qty": 0}]}, headers=vh)
    assert r.status_code == 422, r.text


def test_package_create_items_validation(client):
    r = client.post("/api/v3/packages",
                    json=_package_body(name="空菜品套餐",
                                      items=[{"name": "  ", "qty": 1}]),
                    headers=_vendor_headers(client))
    assert r.status_code == 422, r.text
    r = client.post("/api/v3/packages",
                    json=_package_body(name="零份套餐",
                                      items=[{"name": "主菜", "qty": 0}]),
                    headers=_vendor_headers(client))
    assert r.status_code == 422, r.text


def test_admin_package_review_queue(client):
    pkg = _create_package(client, name="待審隊列套餐")
    r = client.get("/api/v3/admin/packages", params={"status": "pending_review"},
                   headers=_admin_headers(client))
    assert r.status_code == 200, r.text
    assert any(p["id"] == pkg["id"] for p in r.json()["packages"])
    h = _register_and_login(client, "listing-queue@example.com")
    r = client.get("/api/v3/admin/packages", params={"status": "pending_review"},
                   headers=h)
    assert r.status_code == 403
    _approve_package(client, pkg["id"], approve=True)
    r = client.get("/api/v3/admin/packages", params={"status": "pending_review"},
                   headers=_admin_headers(client))
    assert all(p["id"] != pkg["id"] for p in r.json()["packages"])


# ---------- P0-6: coupon edit / pause / resume / unlist / submit ----------

def test_coupon_pause_resume_unlist_submit(client):
    c = _create_coupon(client, coupon_name="券上下架測試")
    _approve_coupon(client, c["id"], approve=True)
    vh = _vendor_headers(client)
    r = client.post(f"/api/v3/vendor/coupons/{c['id']}/pause", headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "paused"
    h = _register_and_login(client, "listing-coupon-pause@example.com")
    r = client.post("/api/v3/new-favorite-coupon", params={"coupon_id": c["id"]},
                    headers=h)
    assert r.status_code == 400, r.text  # paused coupon cannot be claimed
    r = client.post(f"/api/v3/vendor/coupons/{c['id']}/resume", headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"
    r = client.post(f"/api/v3/vendor/coupons/{c['id']}/unlist", headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "draft"
    r = client.post(f"/api/v3/vendor/coupons/{c['id']}/pause", headers=vh)
    assert r.status_code == 400, r.text  # draft cannot be paused
    r = client.post(f"/api/v3/vendor/coupons/{c['id']}/submit", headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending_review"


def test_coupon_edit_active_requires_re_review(client):
    c = _create_coupon(client, coupon_name="改價券")
    _approve_coupon(client, c["id"], approve=True)
    r = client.patch(f"/api/v3/vendor/coupons/{c['id']}",
                     json={"percentage": 80, "coupon_name": "改價券 v2"},
                     headers=_vendor_headers(client))
    assert r.status_code == 200, r.text
    assert r.json()["coupon"]["status"] == "pending_review"
    assert r.json()["coupon"]["percentage"] == 80
    h = _register_and_login(client, "listing-coupon-edit@example.com")
    r = client.post("/api/v3/new-favorite-coupon", params={"coupon_id": c["id"]},
                    headers=h)
    assert r.status_code == 400, r.text  # off the shelf while re-reviewing
    r = _approve_coupon(client, c["id"], approve=True)
    assert r.status_code == 200 and r.json()["status"] == "active"


def test_coupon_edit_total_adjusts_remaining(client):
    c = _create_coupon(client, coupon_name="改庫存券", total=5)
    _approve_coupon(client, c["id"], approve=True)
    h = _register_and_login(client, "listing-stock@example.com")
    r = client.post("/api/v3/new-favorite-coupon", params={"coupon_id": c["id"]},
                    headers=h)
    assert r.status_code == 200, r.text  # 1 claimed: remaining 4
    r = client.patch(f"/api/v3/vendor/coupons/{c['id']}",
                     json={"total": 8},
                     headers=_vendor_headers(client))
    assert r.status_code == 200, r.text
    assert r.json()["coupon"]["total"] == 8
    assert r.json()["coupon"]["remaining"] == 7  # 4 + (8 - 5)
    r = client.patch(f"/api/v3/vendor/coupons/{c['id']}",
                     json={"total": 1},
                     headers=_vendor_headers(client))
    assert r.status_code == 200, r.text
    assert r.json()["coupon"]["remaining"] == 0  # floored, never negative


def test_coupon_edit_validation_and_ownership(client):
    c = _create_coupon(client, coupon_name="校驗券")
    vh = _vendor_headers(client)
    r = client.patch(f"/api/v3/vendor/coupons/{c['id']}",
                     json={"valid_to": "2020-01-01T00:00:00"}, headers=vh)
    assert r.status_code == 400, r.text
    r = client.patch(f"/api/v3/vendor/coupons/{c['id']}",
                     json={"refund_policy": "nope"}, headers=vh)
    assert r.status_code == 400, r.text
    r = client.patch(f"/api/v3/vendor/coupons/{c['id']}",
                     json={"per_user_limit": 0}, headers=vh)
    assert r.status_code == 422, r.text
    r = client.patch(f"/api/v3/vendor/coupons/{c['id']}",
                     json={"sale_price": -5}, headers=vh)
    assert r.status_code == 422, r.text
    h = _register_and_login(client, "listing-other@example.com")
    r = client.patch(f"/api/v3/vendor/coupons/{c['id']}",
                     json={"coupon_name": "冒充"}, headers=h)
    assert r.status_code == 403, r.text
    r = client.post(f"/api/v3/vendor/coupons/{c['id']}/pause", headers=h)
    assert r.status_code == 403, r.text


def test_coupon_approve_future_valid_from_is_scheduled(client):
    now = _now()
    c = _create_coupon(client, coupon_name="定時券",
                       valid_from=(now + timedelta(days=2)).isoformat(),
                       valid_to=(now + timedelta(days=30)).isoformat())
    r = _approve_coupon(client, c["id"], approve=True)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "scheduled"
    h = _register_and_login(client, "listing-sched-coupon@example.com")
    r = client.post("/api/v3/new-favorite-coupon", params={"coupon_id": c["id"]},
                    headers=h)
    assert r.status_code == 400, r.text
    # Sweep flips it once valid_from passes.
    db = TestingSession()
    try:
        row = db.query(models.Coupon).filter(models.Coupon.id == c["id"]).first()
        row.valid_from = _now() - timedelta(hours=1)
        db.commit()
    finally:
        db.close()
    result = _sweep()
    assert result["coupons_activated"] >= 1, result
    db = TestingSession()
    try:
        assert db.query(models.Coupon).filter(
            models.Coupon.id == c["id"]).first().status == "active"
    finally:
        db.close()


def test_coupon_reject_reason_and_resubmit(client):
    c = _create_coupon(client, coupon_name="被駁回券")
    r = _approve_coupon(client, c["id"], approve=False, reason="錯別字")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"
    assert "錯別字" in (r.json().get("reason") or "")
    r = client.post(f"/api/v3/vendor/coupons/{c['id']}/submit",
                    headers=_vendor_headers(client))
    assert r.status_code == 200, r.text  # rejected can resubmit directly
    assert r.json()["status"] == "pending_review"


def test_vendor_coupon_list_status_filter(client):
    c = _create_coupon(client, coupon_name="篩選券")
    _approve_coupon(client, c["id"], approve=True)
    vh = _vendor_headers(client)
    client.post(f"/api/v3/vendor/coupons/{c['id']}/pause", headers=vh)
    r = client.get("/api/v3/get-vendor-coupon-list",
                   params={"status": "paused"}, headers=vh)
    assert r.status_code == 200, r.text
    assert any(x["id"] == c["id"] for x in r.json()["coupons"])
    r = client.get("/api/v3/get-vendor-coupon-list",
                   params={"status": "active"}, headers=vh)
    assert all(x["id"] != c["id"] for x in r.json()["coupons"])


# ---------- P1: bulk create + templates ----------

def test_coupon_bulk_create_partial_success(client):
    now = _now()
    items = [
        _coupon_body(coupon_name="批量券 A"),
        _coupon_body(coupon_name="批量券 B", total=3),
        _coupon_body(coupon_name="批量券壞",
                     valid_from=(now + timedelta(days=5)).isoformat(),
                     valid_to=(now + timedelta(days=1)).isoformat()),
    ]
    r = client.post("/api/v3/vendor/coupons/bulk", json={"coupons": items},
                    headers=_vendor_headers(client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["created"]) == 2, body
    assert len(body["failed"]) == 1, body
    assert body["failed"][0]["index"] == 2
    assert all(x["status"] == "pending_review" for x in body["created"])


def test_coupon_bulk_create_caps_at_20(client):
    items = [_coupon_body(coupon_name=f"批量券 {i}") for i in range(21)]
    r = client.post("/api/v3/vendor/coupons/bulk", json={"coupons": items},
                    headers=_vendor_headers(client))
    assert r.status_code == 400, r.text
    r = client.post("/api/v3/vendor/coupons/bulk", json={"coupons": []},
                    headers=_vendor_headers(client))
    assert r.status_code == 400, r.text
    h = _register_and_login(client, "listing-bulk-cust@example.com")
    r = client.post("/api/v3/vendor/coupons/bulk",
                    json={"coupons": [_coupon_body()]}, headers=h)
    assert r.status_code == 403, r.text


def test_coupon_templates(client):
    r = client.get("/api/v3/coupon-templates", headers=_vendor_headers(client))
    assert r.status_code == 200, r.text
    templates = r.json()["templates"]
    assert [t["key"] for t in templates] == ["manjian", "zhekou", "maizeng"]
    by_key = {t["key"]: t for t in templates}
    assert by_key["manjian"]["coupon_type"] == "Cash"
    assert by_key["manjian"]["min_spend"] > 0
    assert by_key["zhekou"]["coupon_type"] == "Discount"
    assert by_key["zhekou"]["percentage"] > 0
    assert by_key["maizeng"]["coupon_type"] == "Gift"
    assert by_key["maizeng"]["gift"] != ""


# ---------- §3.4: per-user limit ----------

def test_coupon_per_user_limit(client):
    c = _create_coupon(client, coupon_name="限領 2 張券", total=10,
                       per_user_limit=2)
    assert c["per_user_limit"] == 2
    _approve_coupon(client, c["id"], approve=True)
    h = _register_and_login(client, "listing-limit@example.com")
    for _ in range(2):
        r = client.post("/api/v3/new-favorite-coupon",
                        params={"coupon_id": c["id"]}, headers=h)
        assert r.status_code == 200, r.text
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": c["id"]}, headers=h)
    assert r.status_code == 409, r.text  # third claim exceeds the limit
    # Another user still has their own allowance.
    h2 = _register_and_login(client, "listing-limit2@example.com")
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": c["id"]}, headers=h2)
    assert r.status_code == 200, r.text


def test_coupon_default_per_user_limit_is_one(client):
    c = _create_coupon(client, coupon_name="默認限領券", total=10)
    assert c["per_user_limit"] == 1
    _approve_coupon(client, c["id"], approve=True)
    h = _register_and_login(client, "listing-limit1@example.com")
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": c["id"]}, headers=h)
    assert r.status_code == 200, r.text
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": c["id"]}, headers=h)
    assert r.status_code == 409, r.text


# ---------- §3.1 calibre (P1-A compatible, no purchase flow here) ----------

def test_coupon_sale_price_field_and_free_claim_guard(client):
    c = _create_coupon(client, coupon_name="付費券", sale_price=50,
                       refund_policy="expiry_only")
    assert c["sale_price"] == 50
    assert c["is_paid"] is True
    assert c["refund_policy"] == "expiry_only"
    _approve_coupon(client, c["id"], approve=True)
    h = _register_and_login(client, "listing-paid@example.com")
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": c["id"]}, headers=h)
    assert r.status_code == 400, r.text  # paid coupons are bought, not claimed
    # No purchase endpoint in this slice: P1-A owns it.
    r = client.post(f"/api/v3/coupons/{c['id']}/purchase", headers=h)
    assert r.status_code in (404, 405), r.text


def test_coupon_free_behaviour_unchanged(client):
    c = _create_coupon(client, coupon_name="免費券")
    assert c["sale_price"] is None
    assert c["is_paid"] is False
    assert c["refund_policy"] == "anytime"
    _approve_coupon(client, c["id"], approve=True)
    h = _register_and_login(client, "listing-free@example.com")
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": c["id"]}, headers=h)
    assert r.status_code == 200, r.text


# ---------- scheduled sweep: expiry + sold out ----------

def test_sweep_expires_and_marks_sold_out(client):
    now = _now()
    c = _create_coupon(client, coupon_name="到期券", total=1)
    _approve_coupon(client, c["id"], approve=True)
    h = _register_and_login(client, "listing-expire@example.com")
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": c["id"]}, headers=h)
    assert r.status_code == 200, r.text  # remaining 1 -> 0
    result = _sweep()
    assert result["coupons_sold_out"] >= 1, result
    db = TestingSession()
    try:
        assert db.query(models.Coupon).filter(
            models.Coupon.id == c["id"]).first().status == "sold_out"
    finally:
        db.close()
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": c["id"]}, headers=h)
    assert r.status_code == 400, r.text

    c2 = _create_coupon(client, coupon_name="過期券")
    _approve_coupon(client, c2["id"], approve=True)
    db = TestingSession()
    try:
        row = db.query(models.Coupon).filter(models.Coupon.id == c2["id"]).first()
        row.valid_to = _now() - timedelta(hours=1)
        db.commit()
    finally:
        db.close()
    result = _sweep()
    assert result["coupons_expired"] >= 1, result
    db = TestingSession()
    try:
        assert db.query(models.Coupon).filter(
            models.Coupon.id == c2["id"]).first().status == "expired"
    finally:
        db.close()
    # Sweep is idempotent: a second run changes nothing.
    result2 = _sweep()
    assert result2["coupons_expired"] == 0, result2
    assert result2["coupons_sold_out"] == 0, result2


def test_claim_before_valid_from_is_rejected(client):
    now = _now()
    c = _create_coupon(client, coupon_name="未到生效日券",
                       valid_from=(now + timedelta(days=2)).isoformat())
    _approve_coupon(client, c["id"], approve=True)
    db = TestingSession()
    try:
        row = db.query(models.Coupon).filter(models.Coupon.id == c["id"]).first()
        assert row.status == "scheduled"
        # Legacy-style row: active but valid_from in the future.
        row.status = "active"
        db.commit()
    finally:
        db.close()
    h = _register_and_login(client, "listing-early@example.com")
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": c["id"]}, headers=h)
    assert r.status_code == 400, r.text
    # And the sweep puts such a row back to scheduled.
    _sweep()
    db = TestingSession()
    try:
        assert db.query(models.Coupon).filter(
            models.Coupon.id == c["id"]).first().status == "scheduled"
    finally:
        db.close()


# ---------- §3.5: merchant image upload ----------

def test_merchant_image_upload(client):
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
           b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0c"
           b"IDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00"
           b"IEND\xaeB`\x82")
    r = client.post("/api/v3/uploads/merchant-images",
                    files={"files": ("cover.png", io.BytesIO(png), "image/png")},
                    headers=_vendor_headers(client))
    assert r.status_code == 201, r.text
    assert r.json()["images"][0].startswith("/uploads/merchant-images/")
    r = client.post("/api/v3/uploads/merchant-images",
                    files={"files": ("anim.gif", io.BytesIO(b"GIF89a"), "image/gif")},
                    headers=_vendor_headers(client))
    assert r.status_code == 400, r.text  # merchant images: jpg/png/webp only
    h = _register_and_login(client, "listing-upload@example.com")
    r = client.post("/api/v3/uploads/merchant-images",
                    files={"files": ("cover.png", io.BytesIO(png), "image/png")},
                    headers=h)
    assert r.status_code == 403, r.text


# ---------- scheduler wiring + migration columns ----------

def test_scheduler_has_listing_job(client):
    from app.scheduler import JOB_NAMES, JOB_RUNNERS, run_all_jobs
    assert "listing-schedule" in JOB_NAMES
    assert "listing-schedule" in JOB_RUNNERS
    result = run_all_jobs(session_factory=TestingSession)
    assert "listing-schedule" in result, result
    assert result["listing-schedule"].get("failed", 0) == 0, result


def test_listing_columns_exist():
    from sqlalchemy import inspect
    cols = {c["name"] for c in inspect(engine).get_columns("coupons")}
    assert "sale_price" in cols
    assert "per_user_limit" in cols


def test_admin_listing_sweep_endpoint(client):
    r = client.post("/api/v3/admin/listing-sweep",
                    headers=_admin_headers(client))
    assert r.status_code == 200, r.text
    assert "coupons_activated" in r.json()
    h = _register_and_login(client, "listing-sweep-cust@example.com")
    r = client.post("/api/v3/admin/listing-sweep", headers=h)
    assert r.status_code == 403, r.text
