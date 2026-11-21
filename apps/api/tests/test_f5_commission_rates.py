"""F5 tests: merchant commission-rate source table.

- No configured rate -> settlement uses default 10% and the audit log
  says so explicitly.
- A configured rate is used by generate_settlement.
- An explicitly passed commission_rate must equal the effective rate,
  otherwise generate is rejected with 400.
- POST /admin/commission-rates is finance/admin only (auditor -> 403);
  GET /admin/commission-rates is finance/auditor/admin.

Own throwaway SQLite file; never touches production data.
"""
import os
import sys
from datetime import datetime, timezone

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

from tests.db_isolation import sqlite_test_path  # noqa: E402

TEST_DB = sqlite_test_path("coupon_go_test_f5.db")

engine = create_engine(f"sqlite:///{TEST_DB}", connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine)

PERIOD = datetime.now(timezone.utc).strftime("%Y-%m")
# coupon id -> (shop_id, cash face value); one shop per test so the
# per-(shop, period) settlement uniqueness never collides.
PRICED = {2: (1, 30), 5: (3, 100), 8: (4, 15), 17: (9, 150), 22: (11, 10),
        24: (12, 20)}


@pytest.fixture(scope="module")
def client():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    Base.metadata.create_all(bind=engine)
    db = TestingSession()
    seed(db)
    for coupon_id, (_, amount) in PRICED.items():
        coupon = db.get(models.Coupon, coupon_id)
        coupon.coupon_type = "Price"
        coupon.discount_price = amount
        coupon.cash_amount = None
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


def _admin_headers(client):
    return _login(client, "admin@coupon-go.example")


def _finance_headers(client):
    return _login(client, "finance@coupon-go.example")


def _auditor_headers(client):
    return _login(client, "auditor@coupon-go.example")


def _vendor_headers(client):
    return _login(client, "vendor@coupon-go.example")


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
    db = TestingSession()
    red = (db.query(models.Redemption)
           .filter(models.Redemption.wallet_item_id == wid).first())
    db.close()
    assert red is not None
    return h, wid, red.id


def _generate(client, headers, shop_id, body_extra=None):
    body = {"shop_id": shop_id, "period": PERIOD}
    if body_extra:
        body.update(body_extra)
    return client.post("/api/v3/admin/settlements/generate",
                       json=body, headers=headers)


def _set_rate(client, headers, shop_id, rate):
    return client.post("/api/v3/admin/commission-rates",
                       json={"shop_id": shop_id, "rate": rate},
                       headers=headers)


def _generate_log_for_shop(client, shop_id):
    logs = client.get("/api/v3/admin/audit-logs",
                      headers=_admin_headers(client)).json()["logs"]
    for log in logs:
        if (log["action"] == "settlement.generate"
                and f"shop_id={shop_id}" in (log["detail"] or "")):
            return log
    return None


# ---------- default rate when unconfigured ----------
def test_default_rate_when_unconfigured_and_audit_note(client):
    shop_id, face = PRICED[2]
    _redeem(client, "f5-default@example.com", 2)
    fh = _finance_headers(client)
    # no commission_rate passed at all: default 10% applies
    r = _generate(client, fh, shop_id)
    assert r.status_code == 201, r.text
    s = r.json()["settlement"]
    assert s["commission_rate"] == 0.10
    assert s["commission"] == round(face * 0.10, 2)
    assert s["net_amount"] == round(face * 0.90, 2)
    log = _generate_log_for_shop(client, shop_id)
    assert log is not None
    assert "无商户费率配置，使用默认 10%" in log["detail"]


def test_explicit_default_rate_on_unconfigured_shop_passes(client):
    shop_id, face = PRICED[22]
    _redeem(client, "f5-default-explicit@example.com", 22)
    fh = _finance_headers(client)
    r = _generate(client, fh, shop_id, {"commission_rate": 0.10})
    assert r.status_code == 201, r.text
    assert r.json()["settlement"]["commission_rate"] == 0.10


# ---------- configured rate ----------
def test_configured_rate_is_used(client):
    shop_id, face = PRICED[5]
    fh = _finance_headers(client)
    r = _set_rate(client, fh, shop_id, 0.08)
    assert r.status_code in (200, 201), r.text
    assert r.json()["commission_rate"]["rate"] == 0.08
    _redeem(client, "f5-configured@example.com", 5)
    r = _generate(client, fh, shop_id)
    assert r.status_code == 201, r.text
    s = r.json()["settlement"]
    assert s["commission_rate"] == 0.08
    assert s["commission"] == round(face * 0.08, 2)
    assert s["net_amount"] == round(face * 0.92, 2)
    # no default-rate note when a rate is configured
    log = _generate_log_for_shop(client, shop_id)
    assert log is not None
    assert "无商户费率配置，使用默认 10%" not in log["detail"]


def test_explicit_mismatched_rate_rejected(client):
    shop_id, face = PRICED[8]
    fh = _finance_headers(client)
    r = _set_rate(client, fh, shop_id, 0.08)
    assert r.status_code in (200, 201), r.text
    _redeem(client, "f5-mismatch@example.com", 8)
    # configured 8% but explicitly passing 0% must be rejected
    r = _generate(client, fh, shop_id, {"commission_rate": 0})
    assert r.status_code == 400, r.text
    # ... and so must passing the old default 10%
    r = _generate(client, fh, shop_id, {"commission_rate": 0.10})
    assert r.status_code == 400, r.text
    # no settlement was created for this shop
    r = client.get("/api/v3/admin/settlements",
                   params={"shop_id": shop_id}, headers=fh)
    assert r.status_code == 200 and r.json()["total"] == 0


def test_explicit_matching_rate_accepted(client):
    shop_id, face = PRICED[17]
    fh = _finance_headers(client)
    r = _set_rate(client, fh, shop_id, 0.08)
    assert r.status_code in (200, 201), r.text
    _redeem(client, "f5-match@example.com", 17)
    r = _generate(client, fh, shop_id, {"commission_rate": 0.08})
    assert r.status_code == 201, r.text
    s = r.json()["settlement"]
    assert s["commission_rate"] == 0.08
    assert s["commission"] == round(face * 0.08, 2)


# ---------- permissions ----------
def test_auditor_cannot_set_rate_but_can_read(client):
    shop_id, _face = PRICED[24]
    ah = _auditor_headers(client)
    r = _set_rate(client, ah, shop_id, 0.08)
    assert r.status_code == 403, r.text
    cust = _register_and_login(client, "f5-cust@example.com")
    r = _set_rate(client, cust, shop_id, 0.08)
    assert r.status_code == 403
    # finance sets it, auditor can read it back
    fh = _finance_headers(client)
    r = _set_rate(client, fh, shop_id, 0.08)
    assert r.status_code in (200, 201), r.text
    r = client.get("/api/v3/admin/commission-rates", headers=ah)
    assert r.status_code == 200, r.text
    rows = {row["shop_id"]: row["rate"]
            for row in r.json()["commission_rates"]}
    assert rows.get(shop_id) == 0.08
    # customer cannot read the list either
    r = client.get("/api/v3/admin/commission-rates", headers=cust)
    assert r.status_code == 403


def test_rate_list_and_update(client):
    shop_id, _face = PRICED[24]
    fh = _finance_headers(client)
    r = client.get("/api/v3/admin/commission-rates",
                   params={"shop_id": shop_id}, headers=fh)
    assert r.status_code == 200, r.text
    assert any(row["shop_id"] == shop_id and row["rate"] == 0.08
               for row in r.json()["commission_rates"])
    # updating the same shop replaces the rate (one row per shop)
    r = _set_rate(client, fh, shop_id, 0.05)
    assert r.status_code in (200, 201), r.text
    assert r.json()["commission_rate"]["rate"] == 0.05
    r = client.get("/api/v3/admin/commission-rates",
                   params={"shop_id": shop_id}, headers=fh)
    rows = [row for row in r.json()["commission_rates"]
            if row["shop_id"] == shop_id]
    assert len(rows) == 1 and rows[0]["rate"] == 0.05
    # invalid rates are rejected by validation
    r = _set_rate(client, fh, shop_id, 1.5)
    assert r.status_code == 422, r.text
    r = _set_rate(client, fh, 99999, 0.08)
    assert r.status_code == 404, r.text
