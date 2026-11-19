"""F2 tests: reconciliation re-run must not delete resolved discrepancies.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/ -v
Own throwaway SQLite file; never touches production data.

Bug (before fix): run_reconciliation deleted ALL discrepancy rows of the
report on re-run, including status == "resolved" rows, so the resolution
trail (resolution / resolved_by / resolved_at) was lost and a still-present
underlying difference came back as a brand-new open row.
Fix: re-run deletes/rebuilds only open rows, keeps resolved rows untouched,
and never regenerates a row whose (discrepancy_type, redemption_id,
settlement_item_id) already has a resolved row.
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

TEST_DB = "/tmp/coupon_go_test_f2.db"

engine = create_engine(f"sqlite:///{TEST_DB}", connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine)

PERIOD = datetime.now(timezone.utc).strftime("%Y-%m")
# Coupon 11: shop 6, Cash $50 (unused by test_line_f.py, which uses
# shops 1/3/4/9/11/12, so the per-(shop, period) settlement stays ours).
SHOP_ID = 6
COUPON_ID = 11
FACE = 50
RESOLUTION = "已人工跟進，補入下期結算單"


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


def _redeem(client, email, coupon_id=COUPON_ID):
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


def _run(client, headers):
    r = client.post("/api/v3/admin/reconciliation/run",
                    json={"shop_id": SHOP_ID, "period": PERIOD},
                    headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["report"]


def _key(d):
    return (d["discrepancy_type"], d["redemption_id"],
            d["settlement_item_id"])


def test_rerun_preserves_resolved_discrepancy(client):
    fh = _finance_headers(client)
    finance_id = client.get("/api/v3/get-profile",
                            headers=fh).json()["id"]
    rid_a = _redeem(client, "f2-a@example.com")
    rid_b = _redeem(client, "f2-b@example.com")

    # First run (no settlement yet): 2 open missing_settlement_line rows.
    rep = _run(client, fh)
    assert rep["status"] == "no_settlement"
    assert rep["discrepancy_count"] == 2
    assert len(rep["discrepancies"]) == 2
    assert all(d["status"] == "open" for d in rep["discrepancies"])
    d_a = next(d for d in rep["discrepancies"]
               if d["redemption_id"] == rid_a)
    assert d_a["discrepancy_type"] == "missing_settlement_line"
    assert d_a["expected_amount"] == FACE

    # Resolve row A via the resolve endpoint.
    r = client.post(
        f"/api/v3/admin/reconciliation/discrepancies/{d_a['id']}/resolve",
        json={"resolution": RESOLUTION}, headers=fh)
    assert r.status_code == 200, r.text
    resolved = r.json()["discrepancy"]
    assert resolved["status"] == "resolved"
    assert resolved["resolution"] == RESOLUTION
    assert resolved["resolved_by"] == finance_id
    assert resolved["resolved_at"] is not None

    # Re-run the same (shop_id, period): the resolved row must survive
    # intact, the still-open row is rebuilt, no duplicate is created,
    # and discrepancy_count counts ALL rows on the report (1 resolved
    # retained + 1 open regenerated = 2).
    rep = _run(client, fh)
    assert rep["discrepancy_count"] == 2, rep
    assert len(rep["discrepancies"]) == 2, rep
    assert len({_key(d) for d in rep["discrepancies"]}) == 2, rep
    kept = next(d for d in rep["discrepancies"]
                if d["redemption_id"] == rid_a)
    assert kept["id"] == d_a["id"], rep
    assert kept["status"] == "resolved", rep
    assert kept["resolution"] == RESOLUTION, rep
    assert kept["resolved_by"] == finance_id, rep
    assert kept["resolved_at"] is not None, rep
    open_rows = [d for d in rep["discrepancies"] if d["status"] == "open"]
    assert len(open_rows) == 1, rep
    assert open_rows[0]["redemption_id"] == rid_b, rep
    assert rep["status"] == "no_settlement", rep

    # A brand-new underlying difference appears: it gets a fresh open
    # row, while the resolved row is still kept (total 3, still no dup).
    rid_c = _redeem(client, "f2-c@example.com")
    rep = _run(client, fh)
    assert rep["discrepancy_count"] == 3, rep
    assert len(rep["discrepancies"]) == 3, rep
    assert len({_key(d) for d in rep["discrepancies"]}) == 3, rep
    kept = next(d for d in rep["discrepancies"]
                if d["redemption_id"] == rid_a)
    assert kept["id"] == d_a["id"] and kept["status"] == "resolved", rep
    assert kept["resolution"] == RESOLUTION, rep
    assert kept["resolved_by"] == finance_id, rep
    assert kept["resolved_at"] is not None, rep
    assert {d["redemption_id"] for d in rep["discrepancies"]
            if d["status"] == "open"} == {rid_b, rid_c}, rep


def test_rerun_after_settlement_keeps_resolved_row(client):
    # NOTE: continues the same shop/period as the test above (shared
    # module DB, same pattern as test_line_f.py): the resolved row for
    # redemption A and the two open rows from the previous test are the
    # starting state. Run the whole file, not this test alone.
    # Continues the same shop/period: generate the settlement covering
    # all three redemptions, then re-run. The underlying differences are
    # gone, but the resolved row must still be kept (count 1, resolved).
    fh = _finance_headers(client)
    finance_id = client.get("/api/v3/get-profile",
                            headers=fh).json()["id"]
    r = client.post("/api/v3/admin/settlements/generate", json={
        "shop_id": SHOP_ID, "period": PERIOD, "commission_rate": 0.10},
        headers=fh)
    assert r.status_code == 201, r.text
    rep = _run(client, fh)
    assert rep["discrepancy_count"] == 1, rep
    assert len(rep["discrepancies"]) == 1, rep
    kept = rep["discrepancies"][0]
    assert kept["status"] == "resolved", rep
    assert kept["resolution"] == RESOLUTION, rep
    assert kept["resolved_by"] == finance_id, rep
    assert kept["resolved_at"] is not None, rep
    assert rep["status"] == "resolved", rep

    # One more redemption after the settlement: fresh open row + the
    # kept resolved row (count 2), report back to open.
    rid_d = _redeem(client, "f2-d@example.com")
    # A current scan posts immediately. Simulate a missing historical
    # posting to exercise the report and manual resolution paths.
    with TestingSession() as db:
        line = db.query(models.SettlementItem).filter_by(redemption_id=rid_d).one()
        statement = db.get(models.Settlement, line.settlement_id)
        statement.redemption_count -= 1
        statement.gross_amount -= line.amount
        statement.commission -= line.commission
        statement.net_amount -= line.amount - line.commission
        db.delete(line)
        db.commit()
    rep = _run(client, fh)
    assert rep["discrepancy_count"] == 2, rep
    assert len(rep["discrepancies"]) == 2, rep
    assert len({_key(d) for d in rep["discrepancies"]}) == 2, rep
    kept = next(d for d in rep["discrepancies"]
                if d["status"] == "resolved")
    assert kept["resolution"] == RESOLUTION, rep
    assert kept["resolved_by"] == finance_id, rep
    assert kept["resolved_at"] is not None, rep
    open_rows = [d for d in rep["discrepancies"] if d["status"] == "open"]
    assert len(open_rows) == 1, rep
    assert open_rows[0]["redemption_id"] == rid_d, rep
    assert open_rows[0]["discrepancy_type"] == "missing_settlement_line", rep
    assert rep["status"] == "open", rep
