"""F8/F9 tests: shop-member invite flow + member-list visibility.

F8: adding a shop member is an invite (status=pending) — no immediate
promotion; the invitee confirms it themselves, and only then (if they
are a customer) are they promoted to merchant-admin. Platform roles
(admin/finance/auditor/support/vendor) can never be invited. Removing
the last active membership demotes a merchant-admin back to customer.

F9: list_shop_members is readable by members.read holders AND by the
shop's owner / active members (can_access_shop); other shops stay
invisible.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/ -q
Own throwaway SQLite file; never touches production data.
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

TEST_DB = "/tmp/coupon_go_test_f8f9.db"

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


def _uid(client, headers):
    return client.get("/api/v3/get-profile", headers=headers).json()["id"]


def _role(client, headers):
    return client.get("/api/v3/get-profile", headers=headers).json()["role"]


def _invite(client, shop_id, uid, headers):
    return client.post(f"/api/v3/admin/shops/{shop_id}/members",
                       json={"user_id": uid}, headers=headers)


def _confirm(client, shop_id, member_id, headers):
    return client.post(
        f"/api/v3/admin/shops/{shop_id}/members/{member_id}/confirm",
        headers=headers)


def _invite_and_confirm(client, shop_id, uid, invitee_headers):
    r = _invite(client, shop_id, uid, _vendor_headers(client))
    assert r.status_code == 201, r.text
    member_id = r.json()["member"]["id"]
    r = _confirm(client, shop_id, member_id, invitee_headers)
    assert r.status_code == 200, r.text
    return member_id


# ---------- F8: invite does not promote; confirm does ----------
def test_f8_invite_leaves_customer_unpromoted_and_pending(client):
    vh = _vendor_headers(client)
    h = _register_and_login(client, "f8-stranger@example.com")
    uid = _uid(client, h)
    r = _invite(client, 1, uid, vh)
    assert r.status_code == 201, r.text
    assert r.json()["member"]["status"] == "pending"
    # Still a plain customer: no promotion, no shop powers yet.
    assert _role(client, h) == "customer"
    assert client.get("/api/v3/merchant/shops/1/settlements",
                      headers=h).status_code == 403
    assert client.get("/api/v3/admin/shops/1/members",
                      headers=h).status_code == 403


def test_f8_confirm_activates_and_promotes_customer(client):
    vh = _vendor_headers(client)
    h = _register_and_login(client, "f8-confirm@example.com")
    uid = _uid(client, h)
    r = _invite(client, 1, uid, vh)
    assert r.status_code == 201, r.text
    member_id = r.json()["member"]["id"]
    r = _confirm(client, 1, member_id, h)
    assert r.status_code == 200, r.text
    assert r.json()["member"]["status"] == "active"
    assert _role(client, h) == "merchant-admin"
    assert client.get("/api/v3/merchant/shops/1/settlements",
                      headers=h).status_code == 200
    # Confirming twice is rejected.
    assert _confirm(client, 1, member_id, h).status_code == 400


def test_f8_confirm_by_someone_else_is_forbidden(client):
    vh = _vendor_headers(client)
    h = _register_and_login(client, "f8-invitee@example.com")
    uid = _uid(client, h)
    other = _register_and_login(client, "f8-bystander@example.com")
    r = _invite(client, 1, uid, vh)
    assert r.status_code == 201, r.text
    member_id = r.json()["member"]["id"]
    # The shop owner cannot confirm on the invitee's behalf either.
    assert _confirm(client, 1, member_id, vh).status_code == 403
    assert _confirm(client, 1, member_id, other).status_code == 403
    # Still pending and still a customer.
    assert _role(client, h) == "customer"
    body = client.get("/api/v3/admin/shops/1/members",
                      headers=_login(client, "finance@coupon-go.example")).json()
    row = [m for m in body["members"] if m["user_id"] == uid][0]
    assert row["status"] == "pending"


def test_f8_platform_roles_cannot_be_invited(client):
    vh = _vendor_headers(client)
    for email in ("finance@coupon-go.example", "auditor@coupon-go.example",
                  "admin@coupon-go.example"):
        uid = _uid(client, _login(client, email))
        r = _invite(client, 1, uid, vh)
        assert r.status_code == 400, (email, r.text)
    vendor2 = _register_and_login(client, "f8-vendor2@example.com",
                                  role="vendor")
    assert _invite(client, 1, _uid(client, vendor2), vh).status_code == 400
    # Self-registration only allows customer/vendor, so mint a support
    # user via the admin role-change endpoint instead.
    support = _register_and_login(client, "f8-support@example.com")
    support_id = _uid(client, support)
    ah = _login(client, "admin@coupon-go.example")
    r = client.post(f"/api/v3/admin/users/{support_id}/role",
                    json={"role": "support"}, headers=ah)
    assert r.status_code == 200, r.text
    assert _invite(client, 1, support_id, vh).status_code == 400


def test_f8_invite_merchant_admin_stays_merchant_admin(client):
    # A user who is already merchant-admin (member of shop 1) can be
    # invited to shop 2; confirming keeps the platform role as-is.
    h = _register_and_login(client, "f8-dual@example.com")
    uid = _uid(client, h)
    _invite_and_confirm(client, 1, uid, h)
    assert _role(client, h) == "merchant-admin"
    r = _invite(client, 2, uid, _vendor_headers(client))
    assert r.status_code == 201, r.text
    assert r.json()["member"]["status"] == "pending"
    r = _confirm(client, 2, r.json()["member"]["id"], h)
    assert r.status_code == 200, r.text
    assert r.json()["member"]["status"] == "active"
    assert _role(client, h) == "merchant-admin"


def test_f8_remove_last_membership_demotes_to_customer(client):
    vh = _vendor_headers(client)
    h = _register_and_login(client, "f8-demote@example.com")
    uid = _uid(client, h)
    _invite_and_confirm(client, 1, uid, h)
    assert _role(client, h) == "merchant-admin"
    r = client.delete(f"/api/v3/admin/shops/1/members/{uid}", headers=vh)
    assert r.status_code == 200, r.text
    assert _role(client, h) == "customer"


def test_f8_remove_keeps_role_when_other_membership_remains(client):
    vh = _vendor_headers(client)
    h = _register_and_login(client, "f8-keep@example.com")
    uid = _uid(client, h)
    _invite_and_confirm(client, 1, uid, h)
    _invite_and_confirm(client, 2, uid, h)
    assert _role(client, h) == "merchant-admin"
    r = client.delete(f"/api/v3/admin/shops/1/members/{uid}", headers=vh)
    assert r.status_code == 200, r.text
    # Still an active member of shop 2, so the platform role stays.
    assert _role(client, h) == "merchant-admin"
    assert client.get("/api/v3/merchant/shops/2/settlements",
                      headers=h).status_code == 200
    assert client.get("/api/v3/merchant/shops/1/settlements",
                      headers=h).status_code == 403


# ---------- F9: owner / member can list their own shop's members ----------
def test_f9_owner_lists_own_shop_but_not_other_vendor_shop(client):
    vh = _vendor_headers(client)
    h = _register_and_login(client, "f9-listed@example.com")
    uid = _uid(client, h)
    _invite_and_confirm(client, 1, uid, h)
    r = client.get("/api/v3/admin/shops/1/members", headers=vh)
    assert r.status_code == 200, r.text
    assert any(m["user_id"] == uid for m in r.json()["members"])
    # A vendor who owns no shop cannot list anyone else's members.
    other_vendor = _register_and_login(client, "f9-vendor2@example.com",
                                       role="vendor")
    assert client.get("/api/v3/admin/shops/1/members",
                      headers=other_vendor).status_code == 403


def test_f9_active_member_lists_own_shop_only(client):
    h = _register_and_login(client, "f9-member@example.com")
    uid = _uid(client, h)
    _invite_and_confirm(client, 1, uid, h)
    r = client.get("/api/v3/admin/shops/1/members", headers=h)
    assert r.status_code == 200, r.text
    assert any(m["user_id"] == uid for m in r.json()["members"])
    assert client.get("/api/v3/admin/shops/2/members",
                      headers=h).status_code == 403


def test_f9_unrelated_customer_cannot_list_any_shop(client):
    cust = _register_and_login(client, "f9-cust@example.com")
    assert client.get("/api/v3/admin/shops/1/members",
                      headers=cust).status_code == 403
    assert client.get("/api/v3/admin/shops/2/members",
                      headers=cust).status_code == 403


def test_f9_finance_members_read_still_works(client):
    fh = _login(client, "finance@coupon-go.example")
    r = client.get("/api/v3/admin/shops/1/members", headers=fh)
    assert r.status_code == 200, r.text
    assert "members" in r.json()
