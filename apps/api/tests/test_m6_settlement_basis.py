"""M6 tests: settlement / reconciliation basis is actual paid amount,
revoked redemptions and refunded orders never settle, and the F6
face-value snapshot uses the same basis.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_m6_settlement_basis.py -v
Own throwaway SQLite file; never touches production data.

Basis (single shared function, used by scan snapshot, settlement
generation and reconciliation expected amounts):
- package redemption (wallet item has order_id) -> Order.paid_amount;
  refund_status == "refunded" -> excluded from settlement entirely.
- price coupon -> discount_price.
- cash coupon -> cash_amount.
- free / gift / percentage-only / pure-threshold coupon -> 0
  (regular_price is NEVER a settlement basis any more: a free coupon
  settling at regular_price was the platform paying the merchant).
- revoked redemptions (revoked_at set) never settle; if a settlement
  line already exists for one, reconciliation reports
  revoked_but_settled.

Migration 0014 backfills Redemption.face_value to the new basis;
settlements already generated keep their old amounts and are only
flagged amount_mismatch by reconciliation (manual handling, never
silently overwritten).
"""
import importlib.util
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
from app.models_line_f import Settlement, SettlementItem  # noqa: E402
from app.models_packages import Order  # noqa: E402
from app.seed import seed  # noqa: E402

TEST_DB = "/tmp/coupon_go_test_m6.db"

engine = create_engine(f"sqlite:///{TEST_DB}", connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine)

PERIOD = datetime.now(timezone.utc).strftime("%Y-%m")
YEAR, MONTH = int(PERIOD[:4]), int(PERIOD[5:7])
PERIOD_START = datetime(YEAR, MONTH, 1)
PERIOD_END = datetime(YEAR + 1, 1, 1) if MONTH == 12 else datetime(YEAR, MONTH + 1, 1)

# Seed coupon ids (id -> shop, type); one shop per test so the
# per-(shop, period) settlement uniqueness never collides.
# 1: shop 1, Discount 85% | 4: shop 2, Gift | 5: shop 3, Cash $100
# 8: shop 4, Cash $15 | 10: shop 5, Price $138 | 11: shop 6, Cash $50
# 13: shop 7, Discount 80% | 14: shop 7, Gift | 17: shop 9, Cash $150
# 20: shop 10, Gift | 26: shop 13, Gift | 30: shop 16, Price $80


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


def _create_package(client, shop_id, price=150, original=200, stock=50):
    vh = _vendor_headers(client)
    body = {
        "shop_id": shop_id, "name": "M6 測試套餐",
        "items": [{"name": "主菜一份", "qty": 1}],
        "person_count": 2, "original_price": original,
        "package_price": price,
        "valid_from": "2026-11-01T00:00:00",
        "valid_to": "2026-12-31T00:00:00",
        "usage_rules": {"need_booking": True},
        "stock_total": stock, "refund_policy": "anytime",
    }
    r = client.post("/api/v3/packages", json=body, headers=vh)
    assert r.status_code == 201, r.text
    return r.json()["package"]


def _buy_paid(client, headers, package_id, quantity=1):
    r = client.post(f"/api/v3/packages/{package_id}/purchase",
                    json={"payment_method": "fps", "quantity": quantity,
                          "client_ref": ""}, headers=headers)
    assert r.status_code == 200, r.text
    order_id = r.json()["order"]["id"]
    r = client.post(f"/api/v3/orders/{order_id}/confirm-payment",
                    json={"outcome": "succeeded"}, headers=headers)
    assert r.status_code == 200, r.text
    order = r.json()["order"]
    assert order["payment_status"] == "paid", order
    return order


def _redeem_package(client, email, shop_id, price=150, quantity=1):
    """Buy a package, pay, scan the voucher. Returns (redemption_id, order)."""
    h = _register_and_login(client, email)
    pkg = _create_package(client, shop_id, price=price)
    order = _buy_paid(client, h, pkg["id"], quantity=quantity)
    wid = order["wallet_item_id"]
    assert wid is not None
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
    return red.id, order


def _revoke(client, redemption_id):
    r = client.post(f"/api/v3/redemptions/{redemption_id}/revoke",
                    headers=_vendor_headers(client))
    assert r.status_code == 200, r.text
    return r.json()


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


def _set_coupon_fields(coupon_id, **fields):
    db = TestingSession()
    c = db.query(models.Coupon).filter(models.Coupon.id == coupon_id).first()
    for k, v in fields.items():
        setattr(c, k, v)
    db.commit()
    db.close()


def _set_order_refund_status(order_id, status):
    db = TestingSession()
    o = db.query(Order).filter(Order.id == order_id).first()
    o.refund_status = status
    db.commit()
    db.close()


def _set_snapshot(redemption_id, value):
    db = TestingSession()
    red = db.query(models.Redemption).filter(
        models.Redemption.id == redemption_id).first()
    red.face_value = value
    db.commit()
    db.close()


def _redemption_snapshot(redemption_id):
    db = TestingSession()
    red = db.query(models.Redemption).filter(
        models.Redemption.id == redemption_id).first()
    face = red.face_value if red is not None else None
    db.close()
    return face


def _run_backfill():
    """Run migration 0014's backfill against this test DB (the test
    database is built with create_all, not alembic, so the migration
    is loaded from its file and executed directly)."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "alembic", "versions", "0014_m6_settlement_basis_backfill.py")
    spec = importlib.util.spec_from_file_location("m6_migration_0014", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with engine.connect() as conn:
        mod.backfill_face_values(conn)
        conn.commit()


# ---------- M6: revoked / refunded exclusions ----------

def test_m6_revoked_redemption_excluded_from_settlement(client):
    fh = _finance_headers(client)
    rid = _redeem(client, "m6-a@example.com", coupon_id=5)  # shop 3, Cash $100
    assert float(_redemption_snapshot(rid)) == 100
    _revoke(client, rid)
    r = _generate(client, fh, 3)
    assert r.status_code == 201, r.text
    s = r.json()["settlement"]
    assert s["redemption_count"] == 0, s
    assert s["gross_amount"] == 0, s
    assert s["items"] == [], s


def test_m6_free_coupon_snapshot_zero_and_settles_zero(client):
    # Gift coupon with a regular_price: old basis settled regular_price
    # (platform paying the merchant for a freebie); new basis is 0.
    fh = _finance_headers(client)
    _set_coupon_fields(4, regular_price=188)  # shop 2, Gift
    rid = _redeem(client, "m6-b@example.com", coupon_id=4)
    assert float(_redemption_snapshot(rid)) == 0, "免費券快照必須為 0"
    r = _generate(client, fh, 2)
    assert r.status_code == 201, r.text
    assert r.json()["settlement"]["gross_amount"] == 0, r.text


def test_m6_discount_only_coupon_snapshot_zero(client):
    # Percentage-only coupon with a regular_price: no price basis -> 0.
    fh = _finance_headers(client)
    _set_coupon_fields(13, regular_price=120)  # shop 7, Discount 80%
    rid = _redeem(client, "m6-c@example.com", coupon_id=13)
    assert float(_redemption_snapshot(rid)) == 0, "純折扣券快照必須為 0"
    r = _generate(client, fh, 7)
    assert r.status_code == 201, r.text
    assert r.json()["settlement"]["gross_amount"] == 0, r.text


def test_m6_refunded_package_order_excluded_from_settlement(client):
    fh = _finance_headers(client)
    rid, order = _redeem_package(client, "m6-d@example.com", shop_id=1,
                                 price=150, quantity=1)
    assert float(_redemption_snapshot(rid)) == 150
    # Order refunded after the redemption (e.g. admin / out-of-band
    # refund): the redemption must drop out of settlement.
    _set_order_refund_status(order["id"], "refunded")
    r = _generate(client, fh, 1)
    assert r.status_code == 201, r.text
    s = r.json()["settlement"]
    assert s["redemption_count"] == 0, s
    assert s["gross_amount"] == 0, s


def test_m6_revoked_after_settlement_shows_revoked_but_settled(client):
    fh = _finance_headers(client)
    rid = _redeem(client, "m6-e@example.com", coupon_id=8)  # shop 4, Cash $15
    r = _generate(client, fh, 4)
    assert r.status_code == 201, r.text
    assert r.json()["settlement"]["gross_amount"] == 15, r.text
    _revoke(client, rid)
    rep = _run_recon(client, fh, 4)
    assert rep["discrepancy_count"] == 1, rep
    d = rep["discrepancies"][0]
    assert d["discrepancy_type"] == "revoked_but_settled", d
    assert d["redemption_id"] == rid, d
    assert d["expected_amount"] == 0, d
    assert d["actual_amount"] == 15, d
    assert rep["status"] == "open", rep


def test_m6_recon_missing_line_expected_uses_settlement_basis(client):
    # Reconciliation expected amounts come from the same basis function
    # as settlement generation (Price $80 -> expected 80, not regular 120).
    fh = _finance_headers(client)
    _redeem(client, "m6-f@example.com", coupon_id=30)  # shop 16, Price $80
    rep = _run_recon(client, fh, 16)
    assert rep["status"] == "no_settlement", rep
    assert len(rep["discrepancies"]) == 1, rep
    d = rep["discrepancies"][0]
    assert d["discrepancy_type"] == "missing_settlement_line", d
    assert d["expected_amount"] == 80, d


# ---------- M6: package snapshot is the paid amount ----------

def test_m6_package_snapshot_equals_order_paid_amount(client):
    fh = _finance_headers(client)
    # 2 x $150 = $300 paid; the old snapshot was discount_price $150.
    rid, order = _redeem_package(client, "m6-g@example.com", shop_id=5,
                                 price=150, quantity=2)
    assert float(order["paid_amount"]) == 300, order
    assert float(_redemption_snapshot(rid)) == 300, \
        "套餐核銷快照必須等於訂單實付 300，不是券面價 150"
    r = _generate(client, fh, 5)
    assert r.status_code == 201, r.text
    assert r.json()["settlement"]["gross_amount"] == 300, r.text


# ---------- M6: migration 0014 backfill ----------

def test_m6_backfill_corrects_free_coupon_snapshot(client):
    fh = _finance_headers(client)
    _set_coupon_fields(20, regular_price=188)  # shop 10, Gift
    rid = _redeem(client, "m6-h@example.com", coupon_id=20)
    # Simulate a pre-M6 snapshot written under the old basis.
    _set_snapshot(rid, 188)
    _run_backfill()
    assert float(_redemption_snapshot(rid)) == 0, "回填後免費券快照為 0"
    r = _generate(client, fh, 10)
    assert r.status_code == 201, r.text
    assert r.json()["settlement"]["gross_amount"] == 0, r.text


def test_m6_backfill_corrects_package_snapshot_to_paid_amount(client):
    fh = _finance_headers(client)
    rid, order = _redeem_package(client, "m6-i@example.com", shop_id=6,
                                 price=150, quantity=2)
    assert float(order["paid_amount"]) == 300, order
    # Simulate a pre-M6 snapshot written under the old basis.
    _set_snapshot(rid, 150)
    _run_backfill()
    assert float(_redemption_snapshot(rid)) == 300, "回填後套餐快照等於實付"
    r = _generate(client, fh, 6)
    assert r.status_code == 201, r.text
    assert r.json()["settlement"]["gross_amount"] == 300, r.text


def test_m6_backfill_fills_legacy_null_snapshot(client):
    fh = _finance_headers(client)
    rid = _redeem(client, "m6-j@example.com", coupon_id=17)  # shop 9, Cash $150
    _set_snapshot(rid, None)
    _run_backfill()
    assert float(_redemption_snapshot(rid)) == 150, "回填後 NULL 快照按新口徑補值"
    r = _generate(client, fh, 9)
    assert r.status_code == 201, r.text
    assert r.json()["settlement"]["gross_amount"] == 150, r.text


def test_m6_settled_period_amount_unchanged_and_flagged(client):
    # A settlement generated pre-M6 (old basis) keeps its amounts;
    # reconciliation flags amount_mismatch for manual handling instead
    # of silently overwriting the settled amounts.
    fh = _finance_headers(client)
    _set_coupon_fields(26, regular_price=188)  # shop 13, Gift
    rid = _redeem(client, "m6-k@example.com", coupon_id=26)
    _set_snapshot(rid, 188)  # pre-M6 snapshot under the old basis
    db = TestingSession()
    settlement = Settlement(
        shop_id=13, period=PERIOD, period_start=PERIOD_START,
        period_end=PERIOD_END, redemption_count=1, gross_amount=188,
        commission_rate=0.10, commission=18.80, net_amount=169.20,
        status="pending", generated_by=1)
    db.add(settlement)
    db.flush()
    db.add(SettlementItem(
        settlement_id=settlement.id, redemption_id=rid, coupon_id=26,
        amount=188, commission=18.80))
    db.commit()
    settlement_id = settlement.id
    db.close()
    _run_backfill()
    assert float(_redemption_snapshot(rid)) == 0, "回填後免費券快照為 0"
    rep = _run_recon(client, fh, 13)
    assert rep["discrepancy_count"] == 1, rep
    d = rep["discrepancies"][0]
    assert d["discrepancy_type"] == "amount_mismatch", d
    assert d["redemption_id"] == rid, d
    assert d["expected_amount"] == 0, d
    assert d["actual_amount"] == 188, d
    r = client.get(f"/api/v3/admin/settlements/{settlement_id}", headers=fh)
    assert r.status_code == 200, r.text
    s = r.json()["settlement"]
    assert s["gross_amount"] == 188, s  # old settled amount untouched
    assert s["items"][0]["amount"] == 188, s
