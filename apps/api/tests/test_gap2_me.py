"""Gap 2 — GET /me (current user's role + registered name).

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_gap2_me.py -v
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


def _register_and_login(client, email, name, role="customer"):
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


def test_me_customer_returns_role_and_registered_name(client):
    h = _register_and_login(client, "gap2-customer@example.com",
                            name="陳小明", role="customer")
    r = client.get("/api/v3/me", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["role"] == "customer"
    assert body["registered_name"] == "陳小明"


def test_me_vendor_returns_role_and_registered_name(client):
    h = _register_and_login(client, "gap2-vendor@example.com",
                            name="王老闆", role="vendor")
    r = client.get("/api/v3/me", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["role"] == "vendor"
    assert body["registered_name"] == "王老闆"


def test_me_admin_returns_role_and_registered_name(client):
    r = client.get("/api/v3/me",
                   headers=_login(client, "admin@coupon-go.example"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["role"] == "admin"
    assert body["registered_name"] == "Admin"


def test_me_unauthenticated_401(client):
    assert client.get("/api/v3/me").status_code == 401


def test_me_registered_name_is_the_name_given_at_registration(client):
    # A different name at registration must come back verbatim; no
    # invented default is acceptable.
    h = _register_and_login(client, "gap2-name@example.com",
                            name="李小花", role="customer")
    r = client.get("/api/v3/me", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["registered_name"] == "李小花"
    assert r.json()["name"] == "李小花"
