"""Gap 10 tests: field top-ups — every topped-up field must come from a
real source (model field, related query, or calculation), and must be
null / empty (never invented) when there is no source.

Covered:
- GET /package-details: images, branches, rating, latest_reviews,
  limit_remaining, time_slots, stackable.
- GET /bargain-sessions/{id} (+ /bargain-my-sessions): is_owner,
  already_helped, cut_amount_total, remaining_amount.
- GET /bargain-campaigns: image, helper_count, seconds_left.
- GET /orders[/...]: order_no, paid_at, refund_amount, refundable,
  voucher_redeemed.
- GET /home: distance_km on the home coupon lists.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_gap10_field_topups.py -v
Uses an in-memory SQLite DB (StaticPool) so parallel gap workers do
not share a test DB.
"""
import os
import sys
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import models  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models_line_b import Review, ShopRating  # noqa: E402
from app.seed import seed  # noqa: E402
from app.seed_bargain import seed_bargain_campaigns  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine)

# Shop 1 (櫻花壽司屋) seeded at 22.2783, 114.1747.
SHOP1 = {"lat": 22.2783, "lng": 114.1747}


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(bind=engine)
    db = TestingSession()
    seed(db)
    seed_bargain_campaigns(db)
    # Two branches for shop 1 (package-details branches source).
    db.add(models.Branch(shop_id=1, name="灣仔分店",
                         address="灣仔軒尼詩道 88 號", phone="2123 4567"))
    db.add(models.Branch(shop_id=1, name="銅鑼灣分店",
                         address="銅鑼灣羅素街 8 號", phone="2888 3344"))
    db.commit()
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


def _seed_package(client):
    pkgs = client.get("/api/v3/packages").json()["packages"]
    return [p for p in pkgs if p["name"] == "櫻花壽司雙人套餐"][0]


def _create_package(client, name, shop_id=1, stock=5, price=150,
                    original=200, refund_policy="anytime",
                    usage_rules=None):
    vh = _vendor_headers(client)
    body = {
        "shop_id": shop_id, "name": name,
        "items": [{"name": "主菜一份", "qty": 1}],
        "person_count": 2, "original_price": original,
        "package_price": price,
        "valid_from": "2026-11-01T00:00:00",
        "valid_to": "2026-12-31T00:00:00",
        "usage_rules": usage_rules or {"need_booking": True},
        "stock_total": stock, "refund_policy": refund_policy,
    }
    r = client.post("/api/v3/packages", json=body, headers=vh)
    assert r.status_code == 201, r.text
    return r.json()["package"]


def _details(client, package_id, headers=None):
    r = client.get("/api/v3/package-details",
                   params={"package_id": package_id}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["package"]


def _purchase(client, headers, package_id, client_ref=""):
    r = client.post(f"/api/v3/packages/{package_id}/purchase",
                    json={"payment_method": "fps",
                          "client_ref": client_ref},
                    headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["order"]


def _pay(client, headers, order_id, outcome="succeeded"):
    r = client.post(f"/api/v3/orders/{order_id}/confirm-payment",
                    json={"outcome": outcome}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["order"]


def _buy_paid(client, headers, package_id, client_ref=""):
    order = _purchase(client, headers, package_id, client_ref)
    return _pay(client, headers, order["id"], "succeeded")


def _make_campaign(client, original=200, floor=100, stock=5,
                   helper_limit=10):
    vh = _vendor_headers(client)
    r = client.post("/api/v3/bargain-campaigns", json={
        "shop_id": 1, "name": "Gap10 砍價", "items": "主菜、飲品",
        "person_count": 2, "original_price": original,
        "floor_price": floor, "stock_total": stock,
        "helper_limit": helper_limit, "duration_minutes": 1440,
        "campaign_days": 7, "lock_stock": True}, headers=vh)
    assert r.status_code == 200, r.text
    return r.json()["campaign"]


def _start(client, headers, campaign_id):
    r = client.post(f"/api/v3/bargain-campaigns/{campaign_id}/start",
                    headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["session"]


def _session(client, session_id, headers=None):
    r = client.get(f"/api/v3/bargain-sessions/{session_id}",
                   headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["session"]


# ---------- package-details ----------
def test_package_details_has_all_seven_new_keys(client):
    pkg = _details(client, _seed_package(client)["id"])
    for key in ("images", "branches", "rating", "latest_reviews",
                "limit_remaining", "time_slots", "stackable"):
        assert key in pkg, f"missing {key}"


def test_package_details_images_branches_from_real_rows(client):
    pkg = _details(client, _seed_package(client)["id"])
    # Images come from the package coupon template + shop image
    # (both seeded as assets/images/dummy_image_1.png).
    assert pkg["images"] == ["assets/images/dummy_image_1.png"]
    names = [b["branch_name"] for b in pkg["branches"]]
    assert names == ["灣仔分店", "銅鑼灣分店"]
    assert pkg["branches"][0]["address"] == "灣仔軒尼詩道 88 號"
    assert pkg["branches"][0]["phone"] == "2123 4567"


def test_package_details_rating_and_reviews_null_when_no_source(client):
    pkg = _details(client, _seed_package(client)["id"])
    # Shop 1 has no reviews / ShopRating row: null + empty, never invented.
    assert pkg["rating"] is None
    assert pkg["latest_reviews"] == []


def test_package_details_rating_and_latest_reviews_from_real_rows(client):
    pkg = _create_package(client, "評分套餐", shop_id=2, stock=5)
    db = TestingSession()
    db.add(ShopRating(shop_id=2, overall=4.5, taste=4.0, ambience=5.0,
                      service=4.5, review_count=2))
    db.add(Review(user_id=1, shop_id=2, redemption_id=990001,
                  wallet_item_id=990001, taste=5, ambience=5, service=4,
                  overall=4.67, content="最新好評", status="visible",
                  counts_toward_rating=True,
                  created_at=datetime(2026, 11, 16, 10, 0, 0)))
    db.add(Review(user_id=1, shop_id=2, redemption_id=990002,
                  wallet_item_id=990002, taste=3, ambience=3, service=3,
                  overall=3.0, content="舊評價", status="visible",
                  counts_toward_rating=True,
                  created_at=datetime(2026, 11, 15, 10, 0, 0)))
    db.add(Review(user_id=1, shop_id=2, redemption_id=990003,
                  wallet_item_id=990003, taste=1, ambience=1, service=1,
                  overall=1.0, content="申訴中不應出現", status="disputed",
                  counts_toward_rating=False,
                  created_at=datetime(2026, 11, 17, 10, 0, 0)))
    db.commit()
    db.close()
    got = _details(client, pkg["id"])
    assert got["rating"] is not None
    assert got["rating"]["overall"] == 4.5
    assert got["rating"]["review_count"] == 2
    assert [r["content"] for r in got["latest_reviews"]] == ["最新好評", "舊評價"]
    assert got["latest_reviews"][0]["overall"] == 4.67


def test_package_details_time_slots_and_stackable_from_usage_rules(client):
    pkg = _create_package(
        client, "時段可疊加套餐", stock=5,
        usage_rules={"need_booking": True,
                     "time_slots": ["11:30-14:00", "17:30-21:00"],
                     "stackable": True, "per_user_limit": 3})
    got = _details(client, pkg["id"])
    assert got["time_slots"] == ["11:30-14:00", "17:30-21:00"]
    assert got["stackable"] is True
    # Guest: no per-user limit configured for this caller beyond the
    # cap itself — remaining is min(stock, per_user_limit).
    assert got["limit_remaining"] == 3


def test_package_details_time_slots_stackable_absent_means_empty_null(client):
    pkg = _details(client, _seed_package(client)["id"])
    assert pkg["time_slots"] == []
    assert pkg["stackable"] is None
    # No per-user limit configured: remaining purchasable = stock left.
    assert pkg["limit_remaining"] == pkg["stock_left"] == 50


def test_package_details_limit_remaining_counts_my_paid_orders(client):
    h = _register_and_login(client, "gap10-limit@example.com")
    pkg = _create_package(
        client, "限購套餐", stock=5,
        usage_rules={"need_booking": False, "per_user_limit": 2})
    assert _details(client, pkg["id"], headers=h)["limit_remaining"] == 2
    _buy_paid(client, h, pkg["id"], client_ref="gap10-limit-1")
    assert _details(client, pkg["id"], headers=h)["limit_remaining"] == 1


# ---------- bargain sessions ----------
def test_bargain_session_owner_and_cut_fields(client):
    owner = _register_and_login(client, "gap10-owner@example.com")
    helper = _register_and_login(client, "gap10-helper@example.com")
    campaign = _make_campaign(client, original=200, floor=100)
    s = _start(client, owner, campaign["id"])
    assert s["is_owner"] is True
    assert s["already_helped"] is False
    assert s["cut_amount_total"] == 0.0
    assert s["remaining_amount"] == 100.0
    r = client.post(f"/api/v3/bargain-sessions/{s['id']}/help",
                    json={"device_id": "gap10-dev-1"}, headers=helper)
    assert r.status_code == 200, r.text
    helped = r.json()["session"]
    assert helped["is_owner"] is False
    assert helped["already_helped"] is True
    # First help by a new user: 30% + 10% of the 100 remaining = 40.
    assert helped["cut_amount_total"] == 40.0
    assert helped["current_price"] == 160.0
    assert helped["remaining_amount"] == 60.0
    # Owner re-reading sees the same totals; owner never helped.
    again = _session(client, s["id"], headers=owner)
    assert again["is_owner"] is True
    assert again["already_helped"] is False
    assert again["cut_amount_total"] == 40.0
    assert again["remaining_amount"] == 60.0


def test_bargain_session_guest_sees_null_owner_and_not_helped(client):
    owner = _register_and_login(client, "gap10-guest-owner@example.com")
    campaign = _make_campaign(client, original=200, floor=100)
    s = _start(client, owner, campaign["id"])
    guest = _session(client, s["id"])
    assert guest["is_owner"] is None
    assert guest["already_helped"] is False
    assert guest["cut_amount_total"] == 0.0
    assert guest["remaining_amount"] == 100.0


def test_bargain_my_sessions_marks_owner(client):
    owner = _register_and_login(client, "gap10-mine@example.com")
    campaign = _make_campaign(client, original=200, floor=100)
    s = _start(client, owner, campaign["id"])
    r = client.get("/api/v3/bargain-my-sessions", headers=owner)
    assert r.status_code == 200, r.text
    mine = [x for x in r.json()["sessions"] if x["id"] == s["id"]]
    assert len(mine) == 1
    assert mine[0]["is_owner"] is True
    assert mine[0]["already_helped"] is False


# ---------- bargain campaigns ----------
def test_bargain_campaigns_image_helper_count_seconds_left(client):
    owner = _register_and_login(client, "gap10-camp-owner@example.com")
    h1 = _register_and_login(client, "gap10-camp-h1@example.com")
    h2 = _register_and_login(client, "gap10-camp-h2@example.com")
    campaign = _make_campaign(client, original=200, floor=100)
    listed = client.get("/api/v3/bargain-campaigns").json()["campaigns"]
    row = [c for c in listed if c["id"] == campaign["id"]][0]
    # No package linked: image falls back to the shop image (real row).
    assert row["image"] == "assets/images/dummy_image_1.png"
    assert row["helper_count"] == 0
    assert 0 < row["seconds_left"] <= 7 * 86400
    s = _start(client, owner, campaign["id"])
    client.post(f"/api/v3/bargain-sessions/{s['id']}/help",
                json={"device_id": "gap10-camp-d1"}, headers=h1)
    client.post(f"/api/v3/bargain-sessions/{s['id']}/help",
                json={"device_id": "gap10-camp-d2"}, headers=h2)
    listed = client.get("/api/v3/bargain-campaigns").json()["campaigns"]
    row = [c for c in listed if c["id"] == campaign["id"]][0]
    assert row["helper_count"] == 2
    assert 0 < row["seconds_left"] <= 7 * 86400


# ---------- orders ----------
def test_order_paid_fields(client):
    h = _register_and_login(client, "gap10-order-paid@example.com")
    pkg = _create_package(client, "訂單已付套餐", stock=5)
    paid = _buy_paid(client, h, pkg["id"], client_ref="gap10-paid-1")
    assert paid["order_no"] == f"CG-{paid['id']:08d}"
    assert paid["paid_at"] is not None
    assert paid["refund_amount"] == 0.0
    assert paid["refundable"] is True
    assert paid["voucher_redeemed"] is False
    detail = client.get(f"/api/v3/orders/{paid['id']}", headers=h).json()["order"]
    assert detail["order_no"] == paid["order_no"]
    assert detail["refundable"] is True


def test_order_pending_fields(client):
    h = _register_and_login(client, "gap10-order-pending@example.com")
    pkg = _create_package(client, "訂單待付套餐", stock=5)
    order = _purchase(client, h, pkg["id"], client_ref="gap10-pend-1")
    assert order["order_no"] == f"CG-{order['id']:08d}"
    assert order["paid_at"] is None
    assert order["refund_amount"] == 0.0
    assert order["refundable"] is False
    assert order["voucher_redeemed"] is False


def test_order_refunded_fields(client):
    h = _register_and_login(client, "gap10-order-refund@example.com")
    pkg = _create_package(client, "訂單退款套餐", stock=5)
    paid = _buy_paid(client, h, pkg["id"], client_ref="gap10-ref-1")
    r = client.post(f"/api/v3/orders/{paid['id']}/refund",
                    json={"reason": "行程有變"}, headers=h)
    assert r.status_code == 200, r.text
    order = r.json()["order"]
    assert order["refund_amount"] == 150.0
    assert order["refundable"] is False
    assert order["voucher_redeemed"] is False
    assert order["refund_status"] == "refunded"


def test_order_redeemed_fields(client):
    h = _register_and_login(client, "gap10-order-used@example.com")
    pkg = _create_package(client, "訂單已核銷套餐", stock=5)
    paid = _buy_paid(client, h, pkg["id"], client_ref="gap10-used-1")
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": paid["wallet_item_id"]},
                     headers=h)
    assert qr.status_code == 200, qr.text
    vh = _vendor_headers(client)
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 200, r.text
    detail = client.get(f"/api/v3/orders/{paid['id']}", headers=h).json()["order"]
    assert detail["voucher_redeemed"] is True
    assert detail["refundable"] is False
    assert detail["refund_amount"] == 0.0


def test_order_non_refundable_policy_not_refundable(client):
    h = _register_and_login(client, "gap10-order-noref@example.com")
    pkgs = client.get("/api/v3/packages").json()["packages"]
    special = [p for p in pkgs if p["name"] == "麻辣火鍋四人特價套餐"][0]
    paid = _buy_paid(client, h, special["id"], client_ref="gap10-noref-1")
    assert paid["refundable"] is False
    assert paid["voucher_redeemed"] is False


# ---------- home distance_km ----------
def test_home_distance_km_null_without_location(client):
    body = client.get("/api/v3/home").json()
    for key in ("popular_coupons", "week_coupons", "most_grab",
                "recommended", "limited_time_coupons"):
        assert key in body
        for c in body[key]:
            assert "distance_km" in c
            assert c["distance_km"] is None


def test_home_distance_km_with_location(client):
    body = client.get("/api/v3/home", params={
        "lat": SHOP1["lat"], "lng": SHOP1["lng"]}).json()
    shop1 = [c for c in body["popular_coupons"] if c["shop_id"] == 1]
    assert shop1, "seed shop 1 should have home coupons"
    assert all(c["distance_km"] == 0.0 for c in shop1)
    others = [c for c in body["popular_coupons"] if c["shop_id"] != 1]
    assert others and all(c["distance_km"] is not None
                          and c["distance_km"] > 0 for c in others)
    # Rounded to 2 decimals (Haversine straight-line distance).
    assert all(c["distance_km"] == round(c["distance_km"], 2)
               for c in body["popular_coupons"])
