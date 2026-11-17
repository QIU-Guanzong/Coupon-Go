"""Gap 3 tests: GET /cashback/missing lists the caller's own appeals.

Run from the gap-2 workdir:
  ~/workspace/coupon-go-fullstack/.venv/bin/python -m pytest apps/api/tests/test_gap3_cashback_missing_list.py -q
Uses an in-memory SQLite DB; never touches production data.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import models, models_cashback  # noqa: E402,F401
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


def _submit_appeal(client, h, order_no):
    r = client.post("/api/v3/cashback/missing", json={
        "merchant_id": 1, "order_no": order_no,
        "spent_at": _now().isoformat(), "amount": 300,
        "evidence": "receipt", "note": "未見返現"}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["appeal"]


def _set_appeal_status(appeal_id, status):
    db = TestingSession()
    try:
        a = db.query(models_cashback.CashbackAppeal).filter(
            models_cashback.CashbackAppeal.id == appeal_id).first()
        assert a is not None
        a.status = status
        if status in ("resolved", "rejected"):
            a.resolved_at = _now()
        db.commit()
    finally:
        db.close()


APPEAL_FIELDS = {"id", "merchant_id", "shop_name", "order_no", "spent_at",
                 "amount", "evidence", "note", "status", "status_text",
                 "created_at"}


def test_missing_list_returns_own_appeals_only(client):
    h_alice = _register_and_login(client, "gap3-alice@example.com")
    h_bob = _register_and_login(client, "gap3-bob@example.com")
    mine = _submit_appeal(client, h_alice, "GAP3-ORD-001")
    assert mine["status"] == "submitted"
    # Alice sees her own appeal.
    r = client.get("/api/v3/cashback/missing", headers=h_alice)
    assert r.status_code == 200, r.text
    appeals = r.json()["appeals"]
    assert [a["order_no"] for a in appeals] == ["GAP3-ORD-001"]
    # Bob must not see Alice's appeal.
    r = client.get("/api/v3/cashback/missing", headers=h_bob)
    assert r.status_code == 200, r.text
    assert r.json()["appeals"] == []


def test_missing_list_requires_auth(client):
    assert client.get("/api/v3/cashback/missing").status_code == 401


def test_missing_list_four_statuses_fields_complete(client):
    h = _register_and_login(client, "gap3-carol@example.com")
    ids = {}
    for order_no, status in (("GAP3-SUB", "submitted"),
                             ("GAP3-REV", "under_review"),
                             ("GAP3-RES", "resolved"),
                             ("GAP3-REJ", "rejected")):
        a = _submit_appeal(client, h, order_no)
        ids[order_no] = a["id"]
        if status != "submitted":
            _set_appeal_status(a["id"], status)
    r = client.get("/api/v3/cashback/missing", headers=h)
    assert r.status_code == 200, r.text
    appeals = r.json()["appeals"]
    assert len(appeals) == 4, appeals
    assert {a["status"] for a in appeals} == {
        "submitted", "under_review", "resolved", "rejected"}
    for a in appeals:
        assert APPEAL_FIELDS <= set(a.keys()), a
        assert a["order_no"] in ids
        assert a["amount"] == 300.0
        assert a["merchant_id"] == 1
        assert a["shop_name"] != ""
        assert a["status_text"] != ""
