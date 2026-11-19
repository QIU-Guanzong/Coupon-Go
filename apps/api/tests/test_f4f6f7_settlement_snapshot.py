"""F4 / F6 / F7 tests: settlement generation race, face-value snapshot,
no-settlement report resolution.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/ -v
Own throwaway SQLite file; never touches production data.

F4 (before fix): two concurrent generate_settlement calls for the same
(shop, period) both pass the pre-check; the second insert hits the
uq_settlement_shop_period unique constraint and bubbles up as a 500
(IntegrityError) instead of a 409.
F6 (before fix): _face_value reads the live Coupon row at generate and
reconciliation time, so a price change after redemption drifts the
settlement amount and produces a spurious amount_mismatch.
F7 (before fix): resolve_discrepancy only flips a report from "open" to
"resolved"; a "no_settlement" report whose discrepancies are all resolved
stays "no_settlement" forever.
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
from app.models_line_f import Settlement  # noqa: E402
from app.seed import seed  # noqa: E402

TEST_DB = "/tmp/coupon_go_test_f4f6f7.db"

engine = create_engine(f"sqlite:///{TEST_DB}", connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine)

PERIOD = datetime.now(timezone.utc).strftime("%Y-%m")
# Seed coupons (id -> (shop_id, face value at seed time)); one shop per
# test so the per-(shop, period) settlement uniqueness never collides.
# 1: shop 1, Discount 85% (no price basis -> snapshot 0)
# 4: shop 2, Gift (no price basis -> snapshot 0)
# 5: shop 3, Cash $100
# 8: shop 4, Cash $15
# 10: shop 5, Price $138 (discount_price 138 / regular 188)
# 11: shop 6, Cash $50
# 17: shop 9, Cash $150


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


def _finance_headers(client):
    return _login(client, "finance@coupon-go.example")


def _vendor_headers(client):
    return _login(client, "vendor@coupon-go.example")


def _redeem(client, email, coupon_id):
    """Register, claim coupon, QR, vendor scan. Returns redemption id."""
    h = _register_and_login(client, email)
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": coupon_id}, headers=h)
    assert r.status_code == 200, r.text
    wid = client.get("/api/v3/get-favorite-coupons-list",
                     headers=h).json()["items"][0]["id"]
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=h)
    assert qr.status_code == 200, qr.text
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]},
                    headers=_vendor_headers(client))
    assert r.status_code == 200, r.text
    db = TestingSession()
    red = (db.query(models.Redemption)
           .filter(models.Redemption.wallet_item_id == wid).first())
    db.close()
    assert red is not None
    return red.id


def _generate(client, headers, shop_id):
    return client.post("/api/v3/admin/settlements/generate", json={
        "shop_id": shop_id, "period": PERIOD, "commission_rate": 0.10},
        headers=headers)


def _run_recon(client, headers, shop_id):
    r = client.post("/api/v3/admin/reconciliation/run",
                    json={"shop_id": shop_id, "period": PERIOD},
                    headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["report"]


def _set_coupon_price(coupon_id, **fields):
    db = TestingSession()
    c = db.query(models.Coupon).filter(models.Coupon.id == coupon_id).first()
    for k, v in fields.items():
        setattr(c, k, v)
    db.commit()
    db.close()


def _redemption(rid):
    db = TestingSession()
    red = db.query(models.Redemption).filter(models.Redemption.id == rid).first()
    face = red.face_value if red is not None else None
    db.close()
    return face


# ---------- F4 ----------

def test_f4_sequential_duplicate_generate_is_409(client):
    fh = _finance_headers(client)
    _redeem(client, "f4-a@example.com", coupon_id=5)  # shop 3, Cash $100
    r = _generate(client, fh, 3)
    assert r.status_code == 201, r.text
    r = _generate(client, fh, 3)
    assert r.status_code == 409, r.text


def test_f4_concurrent_duplicate_generate_is_409_not_500(client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    fh = _finance_headers(client)
    _redeem(client, "f4-b@example.com", coupon_id=8)
    barrier = Barrier(2)

    def generate():
        with_client = TestClient(app)
        try:
            barrier.wait(timeout=10)
            return _generate(with_client, fh, 4).status_code
        finally:
            with_client.close()

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(generate) for _ in range(2)]
        assert sorted(f.result(timeout=15) for f in futures) == [201, 409]
    with TestingSession() as db:
        assert db.query(Settlement).filter_by(shop_id=4, period=PERIOD).count() == 1


# ---------- F6 ----------

def test_f6_settlement_uses_snapshot_not_later_price(client):
    fh = _finance_headers(client)
    rid = _redeem(client, "f6-a@example.com", coupon_id=10)  # shop 5, $138
    assert float(_redemption(rid)) == 138, "核銷時應寫入面值快照 138"
    _set_coupon_price(10, discount_price=999, regular_price=1999)
    r = _generate(client, fh, 5)
    assert r.status_code == 201, r.text
    s = r.json()["settlement"]
    assert s["gross_amount"] == 138, s
    assert s["items"][0]["amount"] == 138, s


def test_f6_reconciliation_after_price_change_has_no_mismatch(client):
    # Continues the shop 5 settlement above: change the price again,
    # then reconcile — the snapshot must keep expected == actual.
    fh = _finance_headers(client)
    _set_coupon_price(10, discount_price=555, regular_price=1888)
    rep = _run_recon(client, fh, 5)
    assert rep["discrepancy_count"] == 0, rep
    assert rep["status"] == "resolved", rep


def test_f6_percentage_and_gift_coupons_snapshot_zero(client):
    fh = _finance_headers(client)
    rid_pct = _redeem(client, "f6-b@example.com", coupon_id=1)  # shop 1, 85%
    assert float(_redemption(rid_pct)) == 0
    r = _generate(client, fh, 1)
    assert r.status_code == 201, r.text
    assert r.json()["settlement"]["gross_amount"] == 0, r.text
    rid_gift = _redeem(client, "f6-c@example.com", coupon_id=4)  # shop 2, Gift
    assert float(_redemption(rid_gift)) == 0
    r = _generate(client, fh, 2)
    assert r.status_code == 201, r.text
    assert r.json()["settlement"]["gross_amount"] == 0, r.text


def test_f6_legacy_null_snapshot_falls_back_to_live_price(client):
    # A redemption created before the snapshot column existed has
    # face_value NULL; settlement must fall back to the current coupon
    # price instead of crashing or pricing at 0.
    fh = _finance_headers(client)
    rid = _redeem(client, "f6-d@example.com", coupon_id=11)  # shop 6, Cash $50
    db = TestingSession()
    red = db.query(models.Redemption).filter(models.Redemption.id == rid).first()
    red.face_value = None
    db.commit()
    db.close()
    _set_coupon_price(11, cash_amount=77)
    r = _generate(client, fh, 6)
    assert r.status_code == 201, r.text
    assert r.json()["settlement"]["gross_amount"] == 77, r.text


# ---------- F7 ----------

def test_f7_no_settlement_report_resolves_when_all_discrepancies_done(client):
    fh = _finance_headers(client)
    _redeem(client, "f7-a@example.com", coupon_id=17)  # shop 9, Cash $150
    _redeem(client, "f7-b@example.com", coupon_id=17)
    rep = _run_recon(client, fh, 9)
    assert rep["status"] == "no_settlement", rep
    assert len(rep["discrepancies"]) == 2, rep
    d0, d1 = rep["discrepancies"]
    r = client.post(
        f"/api/v3/admin/reconciliation/discrepancies/{d0['id']}/resolve",
        json={"resolution": "已人工跟進"}, headers=fh)
    assert r.status_code == 200, r.text
    rep = client.get(
        f"/api/v3/admin/reconciliation/reports/{rep['id']}",
        headers=fh).json()["report"]
    assert rep["status"] == "no_settlement", rep  # one still open
    r = client.post(
        f"/api/v3/admin/reconciliation/discrepancies/{d1['id']}/resolve",
        json={"resolution": "已人工跟進"}, headers=fh)
    assert r.status_code == 200, r.text
    rep = client.get(
        f"/api/v3/admin/reconciliation/reports/{rep['id']}",
        headers=fh).json()["report"]
    assert rep["status"] == "resolved", rep
