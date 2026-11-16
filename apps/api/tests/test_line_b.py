"""Line B tests: reviews v1 (F6) + admin v1 (F4).

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/ -v
Own throwaway SQLite file; never touches production data. The v0.1
legacy POST /review chain is still covered by tests/test_api.py.
"""
import os
import sys

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
from app.seed import seed  # noqa: E402

TEST_DB = "/tmp/coupon_go_test_line_b.db"

engine = create_engine(f"sqlite:///{TEST_DB}", connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine)


@pytest.fixture(scope="module")
def client():
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


def _admin_headers(client):
    return _login(client, "admin@coupon-go.example")


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
    rid = client.get("/api/v3/get-user-record-list",
                     headers=h).json()["records"][0]["id"]
    return h, wid, rid


def _review_body(wid, **over):
    body = {"wallet_item_id": wid, "taste": 5, "ambience": 4, "service": 3,
            "content": "好食，服務唔錯", "tags": ["壽司"], "claim_points": True}
    body.update(over)
    return body


# ---------- reviews ----------
def test_review_unredeemed_rejected(client):
    h = _register_and_login(client, "rev-unredeemed@example.com")
    assert client.post("/api/v3/new-favorite-coupon",
                       params={"coupon_id": 2}, headers=h).status_code == 200
    wid = client.get("/api/v3/get-favorite-coupons-list",
                     headers=h).json()["items"][0]["id"]
    r = client.post("/api/v3/reviews", json=_review_body(wid), headers=h)
    assert r.status_code == 400  # not redeemed yet


def test_review_points_do_not_move_rating(client):
    h, wid, rid = _redeem(client, "rev-points@example.com", 2)
    assert client.get("/api/v3/points", headers=h).json()["balance"] == 30
    r = client.post("/api/v3/reviews", json=_review_body(wid), headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["review"]["points_given"] is True
    assert r.json()["review"]["counts_toward_rating"] is False
    assert r.json()["balance"] == 45  # +15 points still given
    # ...but the rating is untouched: no counted review exists.
    rating = client.get("/api/v3/shops/1/rating").json()
    assert rating["review_count"] == 0 and rating["overall"] is None
    listed = client.get("/api/v3/shops/1/reviews").json()
    assert listed["total"] == 1
    assert listed["reviews"][0]["counts_toward_rating"] is False


def test_review_without_points_counts_toward_rating(client):
    h, wid, rid = _redeem(client, "rev-rated@example.com", 3)
    r = client.post("/api/v3/reviews",
                    json=_review_body(wid, claim_points=False), headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["review"]["points_given"] is False
    assert r.json()["review"]["counts_toward_rating"] is True
    assert r.json()["review"]["overall"] == 4.0  # (5+4+3)/3
    assert r.json()["balance"] == 30  # no points for a counted review
    # coupon 3 belongs to shop 2 (蜀香麻辣火鍋城)
    rating = client.get("/api/v3/shops/2/rating").json()
    assert rating["review_count"] == 1
    assert rating["overall"] == 4.0
    assert rating["taste"] == 5.0 and rating["ambience"] == 4.0
    assert rating["service"] == 3.0


def test_duplicate_review_blocked(client):
    h, wid, rid = _redeem(client, "rev-dup@example.com", 4)
    r = client.post("/api/v3/reviews", json=_review_body(wid), headers=h)
    assert r.status_code == 200, r.text
    r = client.post("/api/v3/reviews", json=_review_body(wid), headers=h)
    assert r.status_code == 400  # one review per redemption
    r = client.post("/api/v3/reviews",
                    json={"redemption_id": rid, "taste": 5, "ambience": 5,
                          "service": 5}, headers=h)
    assert r.status_code == 400  # same redemption via redemption_id, too
    # legacy /review is also blocked once the wallet item is reviewed
    r = client.post("/api/v3/review", params={"wallet_item_id": wid}, headers=h)
    assert r.status_code == 400


def test_review_validation(client):
    h, wid, rid = _redeem(client, "rev-valid@example.com", 5)
    bad = _review_body(wid, taste=6)
    assert client.post("/api/v3/reviews", json=bad, headers=h).status_code == 422
    r = client.post("/api/v3/reviews",
                    json={"redemption_id": 999999, "taste": 5, "ambience": 5,
                          "service": 5}, headers=h)
    assert r.status_code == 404
    assert client.get("/api/v3/shops/999999/rating").status_code == 404


def test_review_reply_and_dispute(client):
    h, wid, rid = _redeem(client, "rev-dispute@example.com", 6)
    r = client.post("/api/v3/reviews",
                    json=_review_body(wid, claim_points=False), headers=h)
    assert r.status_code == 200, r.text
    review_id = r.json()["review"]["id"]
    shop_id = r.json()["review"]["shop_id"]
    assert client.get(f"/api/v3/shops/{shop_id}/rating").json()["review_count"] == 1
    # a random customer may not reply; the shop vendor may
    other = _register_and_login(client, "rev-other@example.com")
    assert client.post(f"/api/v3/reviews/{review_id}/reply",
                       json={"reply": "hi"}, headers=other).status_code == 403
    vh = _vendor_headers(client)
    r = client.post(f"/api/v3/reviews/{review_id}/reply",
                    json={"reply": "多謝光臨！"}, headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["review"]["reply_text"] == "多謝光臨！"
    # author disputes -> review leaves the aggregate
    r = client.post(f"/api/v3/reviews/{review_id}/dispute",
                    json={"reason": "測試申訴"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["review"]["status"] == "disputed"
    assert client.get(f"/api/v3/shops/{shop_id}/rating").json()["review_count"] == 0
    assert client.get(f"/api/v3/shops/{shop_id}/reviews").json()["total"] == 0


# ---------- admin v1 ----------
def _application_body(**over):
    body = {"shop_name": "測試茶餐廳", "contact_name": "陳先生",
            "phone": "9123 4567", "address": "旺角彌敦道 1 號",
            "business_license": "BR12345678", "id_card": "HKID****1234",
            "shop_photos": ["shop-front.jpg"],
            "industry_license": "食牌 FE20260001"}
    body.update(over)
    return body


def test_merchant_application_flow(client):
    h = _register_and_login(client, "applicant@example.com")
    r = client.post("/api/v3/admin/merchant-applications",
                    json=_application_body(), headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["application"]["status"] == "pending"
    app_id = r.json()["application"]["id"]
    # second pending application is blocked
    r = client.post("/api/v3/admin/merchant-applications",
                    json=_application_body(shop_name="第二間"), headers=h)
    assert r.status_code == 400
    # non-admin cannot list; admin can
    assert client.get("/api/v3/admin/merchant-applications",
                      headers=h).status_code == 403
    ah = _admin_headers(client)
    r = client.get("/api/v3/admin/merchant-applications", headers=ah)
    assert r.status_code == 200 and r.json()["total"] >= 1
    # reject without a reason is refused
    r = client.post(f"/api/v3/admin/merchant-applications/{app_id}/review",
                    json={"approve": False, "reject_reason": ""}, headers=ah)
    assert r.status_code == 400
    r = client.post(f"/api/v3/admin/merchant-applications/{app_id}/review",
                    json={"approve": True}, headers=ah)
    assert r.status_code == 200, r.text
    assert r.json()["application"]["status"] == "approved"
    # approval promotes the applicant to vendor
    assert client.get("/api/v3/get-profile",
                      headers=h).json()["role"] == "vendor"
    # re-review of a decided application is blocked
    r = client.post(f"/api/v3/admin/merchant-applications/{app_id}/review",
                    json={"approve": True}, headers=ah)
    assert r.status_code == 400
    # audit log records the decision
    logs = client.get("/api/v3/admin/audit-logs", headers=ah).json()
    assert any(l["action"] == "merchant_application.approve"
               and l["target_id"] == app_id for l in logs["logs"])


def test_merchant_application_reject_and_resubmit(client):
    h = _register_and_login(client, "applicant2@example.com")
    ah = _admin_headers(client)
    r = client.post("/api/v3/admin/merchant-applications",
                    json=_application_body(shop_name="被拒小店"), headers=h)
    app_id = r.json()["application"]["id"]
    r = client.post(f"/api/v3/admin/merchant-applications/{app_id}/review",
                    json={"approve": False, "reject_reason": "牌照文件不清"},
                    headers=ah)
    assert r.status_code == 200, r.text
    assert r.json()["application"]["reject_reason"] == "牌照文件不清"
    # rejected applicants may submit a new application
    r = client.post("/api/v3/admin/merchant-applications",
                    json=_application_body(shop_name="被拒小店（重提）"), headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["application"]["status"] == "pending"


def test_admin_dashboard_readonly(client):
    ah = _admin_headers(client)
    h = _register_and_login(client, "dashboard-user@example.com")
    assert client.get("/api/v3/admin/dashboard").status_code == 401
    assert client.get("/api/v3/admin/dashboard", headers=h).status_code == 403
    r = client.get("/api/v3/admin/dashboard", headers=ah)
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("total_users", "total_shops", "total_coupons",
                "coupons_by_status", "wallet_by_status", "total_redemptions",
                "total_reviews", "pending_applications", "pending_coupons"):
        assert key in body, key
    assert body["total_shops"] == 20 and body["total_coupons"] >= 36
    assert client.get("/api/v3/admin/shops", headers=ah).status_code == 200
    assert client.get("/api/v3/admin/coupons", headers=ah).status_code == 200
    assert client.get("/api/v3/admin/coupons",
                      params={"status": "active"}, headers=ah).status_code == 200
    r = client.get("/api/v3/admin/redemptions", headers=ah)
    assert r.status_code == 200 and r.json()["total"] >= 1
    r = client.get("/api/v3/admin/redemptions",
                   params={"format": "csv"}, headers=ah)
    assert r.status_code == 200
    assert "coupon_name" in r.text and "shop_name" in r.text
    assert client.get("/api/v3/admin/users", headers=ah).status_code == 200
    assert client.get("/api/v3/admin/users",
                      params={"role": "vendor"}, headers=ah).status_code == 200
    assert client.get("/api/v3/admin/audit-logs", headers=ah).status_code == 200
    # dashboard is read-only: no write endpoint exists on these resources
    assert client.post("/api/v3/admin/shops", headers=ah).status_code in (404, 405)
