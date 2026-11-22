"""Gap 1 — GET /admin/orders (admin order list).

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_gap1_admin_orders.py -v
Uses an in-memory SQLite DB (StaticPool), same pattern as test_packages.py.
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine)


@pytest.fixture(scope="module")
def client():
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


def _register_and_login(client, email, name="Test", role="customer"):
    assert client.post("/api/v3/new-register", json={
        "email": email, "password": "Demo123!", "name": name,
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


def _create_package(client, shop_id, name, price=150, stock=5):
    r = client.post("/api/v3/packages", json={
        "shop_id": shop_id, "name": name,
        "items": [{"name": "主菜一份", "qty": 1}],
        "person_count": 2, "original_price": price + 50, "package_price": price,
        "valid_from": "2026-11-01T00:00:00",
        "valid_to": "2026-12-31T00:00:00",
        "usage_rules": {"need_booking": True},
        "stock_total": stock, "refund_policy": "anytime",
    }, headers=_vendor_headers(client))
    assert r.status_code == 201, r.text
    pkg = r.json()["package"]
    # Support both optional manual review and default auto-approval.
    if pkg["status"] == "pending_review":
        ah = _admin_headers(client)
        r = client.post(f"/api/v3/admin/packages/{pkg['id']}/approve",
                        params={"approve": True}, headers=ah)
        assert r.status_code == 200, r.text
    r = client.get("/api/v3/package-details", params={"package_id": pkg["id"]})
    assert r.status_code == 200, r.text
    return r.json()["package"]


def _buy(client, headers, package_id, pay=True):
    r = client.post(f"/api/v3/packages/{package_id}/purchase",
                    json={"payment_method": "fps"}, headers=headers)
    assert r.status_code == 200, r.text
    order = r.json()["order"]
    if pay:
        r = client.post(f"/api/v3/orders/{order['id']}/confirm-payment",
                        json={"outcome": "succeeded"}, headers=headers)
        assert r.status_code == 200, r.text
        order = r.json()["order"]
    return order


@pytest.fixture(scope="module")
def orders(client):
    """3 orders: 2 paid in shop 1, 1 pending in shop 2."""
    buyer_a = _register_and_login(client, "gap1-a@example.com", name="買家 A")
    buyer_b = _register_and_login(client, "gap1-b@example.com", name="買家 B")
    pkg1 = _create_package(client, 1, "Gap1 店一雙人套餐", price=150)
    pkg2 = _create_package(client, 2, "Gap1 店二雙人套餐", price=200)
    o1 = _buy(client, buyer_a, pkg1["id"], pay=True)
    o2 = _buy(client, buyer_a, pkg1["id"], pay=True)
    o3 = _buy(client, buyer_b, pkg2["id"], pay=False)
    return {"paid_shop1": [o1["id"], o2["id"]], "pending_shop2": o3["id"],
            "buyer_a": buyer_a, "buyer_b": buyer_b}


@pytest.fixture(scope="module")
def merchant_admin_headers(client):
    """Customer added as staff of shop 1 -> becomes merchant-admin."""
    h = _register_and_login(client, "gap1-staff@example.com", name="店員")
    me = client.get("/api/v3/get-profile", headers=h).json()
    r = client.post("/api/v3/admin/shops/1/members",
                    json={"user_id": me["id"]}, headers=_vendor_headers(client))
    assert r.status_code == 201, r.text
    # F8: invite is pending until the invitee confirms it.
    r = client.post(
        f"/api/v3/admin/shops/1/members/{r.json()['member']['id']}/confirm",
        headers=h)
    assert r.status_code == 200, r.text
    # Role changed after the first token was issued: re-login for new role.
    return _login(client, "gap1-staff@example.com")


def test_finance_sees_all_orders(client, orders):
    r = client.get("/api/v3/admin/orders",
                   headers=_login(client, "finance@coupon-go.example"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    ids = {o["id"] for o in body["orders"]}
    assert set(orders["paid_shop1"]) | {orders["pending_shop2"]} <= ids


def test_auditor_sees_all_orders(client, orders):
    r = client.get("/api/v3/admin/orders",
                   headers=_login(client, "auditor@coupon-go.example"))
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 3


def test_admin_sees_all_orders(client, orders):
    r = client.get("/api/v3/admin/orders",
                   headers=_login(client, "admin@coupon-go.example"))
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 3


def test_order_fields_match_user_side(client, orders):
    r = client.get("/api/v3/admin/orders",
                   headers=_login(client, "finance@coupon-go.example"))
    assert r.status_code == 200, r.text
    o = r.json()["orders"][0]
    # Field names consistent with user-side GET /orders (order_out) ...
    for field in ("id", "order_id", "user_id", "amount", "payment_status",
                  "created_at", "shop_name", "package_name"):
        assert field in o, f"missing field {field}"
    # ... plus the admin extras the task requires.
    assert "status" in o and o["status"] == o["payment_status"]
    assert "shop_id" in o
    assert "user_email" in o or "user_name" in o


def test_merchant_admin_sees_only_own_shop(client, orders,
                                           merchant_admin_headers):
    r = client.get("/api/v3/admin/orders", headers=merchant_admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert {o["id"] for o in body["orders"]} == set(orders["paid_shop1"])
    assert all(o["shop_id"] == 1 for o in body["orders"])


def test_customer_forbidden(client, orders):
    r = client.get("/api/v3/admin/orders", headers=orders["buyer_a"])
    assert r.status_code == 403, r.text


def test_vendor_forbidden(client):
    r = client.get("/api/v3/admin/orders", headers=_vendor_headers(client))
    assert r.status_code == 403, r.text


def test_unauthenticated_401(client):
    assert client.get("/api/v3/admin/orders").status_code == 401


def test_status_filter(client, orders):
    fh = _login(client, "finance@coupon-go.example")
    r = client.get("/api/v3/admin/orders", params={"status": "paid"}, headers=fh)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2
    assert all(o["payment_status"] == "paid" for o in r.json()["orders"])
    r = client.get("/api/v3/admin/orders", params={"status": "pending"},
                   headers=fh)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1
    assert r.json()["orders"][0]["id"] == orders["pending_shop2"]


def test_pagination(client, orders):
    fh = _login(client, "finance@coupon-go.example")
    r = client.get("/api/v3/admin/orders",
                   params={"page": 1, "page_size": 2}, headers=fh)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3 and body["total_pages"] == 2
    assert len(body["orders"]) == 2
    r2 = client.get("/api/v3/admin/orders",
                    params={"page": 2, "page_size": 2}, headers=fh)
    assert r2.status_code == 200, r2.text
    assert len(r2.json()["orders"]) == 1
    assert r2.json()["orders"][0]["id"] not in {o["id"] for o in body["orders"]}
