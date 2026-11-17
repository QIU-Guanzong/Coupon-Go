"""F10 — GET /admin/users role filter must cover the new roles.

Before the fix, only customer/vendor/admin were filtered; finance /
auditor / support / merchant-admin were silently ignored (all users
returned) and unknown roles were silently ignored too.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_f10_admin_users_role_filter.py -v
Uses an in-memory SQLite DB (StaticPool), same pattern as test_gap1.
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


def _login(client, email):
    r = client.post("/api/v3/login",
                    json={"email": email, "password": "Demo123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _register_and_login(client, email):
    assert client.post("/api/v3/new-register", json={
        "email": email, "password": "Demo123!", "name": "Test",
        "role": "customer"}).status_code == 200
    code = client.get("/api/v3/_dev/otp",
                      params={"email": email}).json()["code"]
    r = client.post("/api/v3/new-verify-otp", json={
        "email": email, "code": code, "purpose": "register"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _roles_of(client, headers, role=None):
    params = {} if role is None else {"role": role}
    r = client.get("/api/v3/admin/users", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_role_finance_filters_only_finance(client):
    ah = _login(client, "admin@coupon-go.example")
    body = _roles_of(client, ah, "finance")
    assert body["total"] == 1
    assert {u["role"] for u in body["users"]} == {"finance"}


def test_role_auditor_and_support_filter(client):
    ah = _login(client, "admin@coupon-go.example")
    for role in ("auditor", "support"):
        body = _roles_of(client, ah, role)
        assert body["total"] == 1
        assert {u["role"] for u in body["users"]} == {role}


def test_role_merchant_admin_filters_only_merchant_admin(client):
    ah = _login(client, "admin@coupon-go.example")
    h = _register_and_login(client, "f10-merchant-admin@example.com")
    uid = client.get("/api/v3/get-profile", headers=h).json()["id"]
    r = client.post(f"/api/v3/admin/users/{uid}/role",
                    json={"role": "merchant-admin"}, headers=ah)
    assert r.status_code == 200, r.text
    body = _roles_of(client, ah, "merchant-admin")
    assert body["total"] == 1
    assert {u["role"] for u in body["users"]} == {"merchant-admin"}
    assert body["users"][0]["email"] == "f10-merchant-admin@example.com"


def test_role_customer_vendor_admin_still_filter(client):
    ah = _login(client, "admin@coupon-go.example")
    for role in ("customer", "vendor", "admin"):
        body = _roles_of(client, ah, role)
        assert body["total"] >= 1
        assert {u["role"] for u in body["users"]} == {role}


def test_unknown_role_returns_400(client):
    ah = _login(client, "admin@coupon-go.example")
    r = client.get("/api/v3/admin/users", params={"role": "boss"},
                   headers=ah)
    assert r.status_code == 400


def test_no_role_returns_all_users(client):
    ah = _login(client, "admin@coupon-go.example")
    body = _roles_of(client, ah)
    # seed (6 users) + the merchant-admin created above
    assert body["total"] >= 7
    assert len({u["role"] for u in body["users"]}) >= 5
