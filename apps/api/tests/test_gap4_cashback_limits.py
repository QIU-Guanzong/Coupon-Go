"""Gap 4 tests: GET /cashback returns the withdraw limits from config.

Run from the gap-2 workdir:
  ~/workspace/coupon-go-fullstack/.venv/bin/python -m pytest apps/api/tests/test_gap4_cashback_limits.py -q
Uses an in-memory SQLite DB; never touches production data.
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

from app import config  # noqa: E402
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


def _register_and_login(client, email):
    assert client.post("/api/v3/new-register", json={
        "email": email, "password": "Demo123!", "name": "Test",
        "role": "customer"}).status_code == 200
    code = client.get("/api/v3/_dev/otp", params={"email": email}).json()["code"]
    r = client.post("/api/v3/new-verify-otp", json={
        "email": email, "code": code, "purpose": "register"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_cashback_returns_limits_matching_config(client):
    h = _register_and_login(client, "gap4-alice@example.com")
    r = client.get("/api/v3/cashback", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["min_withdraw_hkd"] == config.CASHBACK_MIN_WITHDRAW_HKD
    assert body["daily_withdraw_limit_hkd"] == config.CASHBACK_DAILY_WITHDRAW_LIMIT_HKD
    # Existing balances still present alongside the new fields.
    assert "withdrawable_balance" in body
    assert "pending_balance" in body


def test_cashback_limits_require_auth(client):
    assert client.get("/api/v3/cashback").status_code == 401
