"""M6 tests: settlement / reconciliation basis is actual paid amount,
revoked redemptions and refunded orders never settle, and the F6
face-value snapshot uses the same basis.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_m6_settlement_basis.py -v
Own throwaway SQLite file or a dedicated M8_TEST_POSTGRES_URL; never uses DATABASE_URL.

Basis (single shared function, used by scan snapshot, settlement
generation and reconciliation expected amounts):
- package redemption (wallet item has order_id) -> Order.paid_amount;
  refund_status == "refunded" -> excluded from settlement entirely.
- price coupon -> discount_price.
- cash coupon -> 0, also when an old positive snapshot exists (M8).
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
from datetime import datetime, timedelta, timezone

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
from app.models_line_f import Settlement, SettlementItem, SettlementTransfer  # noqa: E402
from app.models_packages import Order  # noqa: E402
from app.seed import seed  # noqa: E402

TEST_DB = os.getenv("M6_TEST_DB", "/tmp/coupon_go_test_m6.db")
PG_URL = os.getenv("M8_TEST_POSTGRES_URL")
if PG_URL:
    from sqlalchemy.engine import make_url
    parts = make_url(PG_URL)
    assert parts.database == "m8_cash" and not parts.host
    assert str(parts.query.get("host", "")).startswith("/")
    engine = create_engine(PG_URL)
else:
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
    if PG_URL:
        Base.metadata.drop_all(bind=engine)
    elif os.path.exists(TEST_DB):
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



def _admin_headers(client):
    r = client.post("/api/v3/login", json={
        "email": "admin@coupon-go.example", "password": "Demo123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


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
        "valid_from": (PERIOD_START - timedelta(days=2)).isoformat(),
        "valid_to": (PERIOD_END + timedelta(days=2)).isoformat(),
        "usage_rules": {"need_booking": True},
        "stock_total": stock, "refund_policy": "anytime",
    }
    r = client.post("/api/v3/packages", json=body, headers=vh)
    assert r.status_code == 201, r.text
    pkg = r.json()["package"]
    # §3.2: packages enter review on creation; approve before use.
    ah = _admin_headers(client)
    r = client.post(f"/api/v3/admin/packages/{pkg['id']}/approve",
                    params={"approve": True}, headers=ah)
    assert r.status_code == 200, r.text
    r = client.get("/api/v3/package-details", params={"package_id": pkg["id"]})
    assert r.status_code == 200, r.text
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
    assert float(_redemption_snapshot(rid)) == 0
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


def test_m6_revoked_after_settlement_keeps_sale_and_credit_note(client):
    fh = _finance_headers(client)
    _set_coupon_fields(8, coupon_type="Price", cash_amount=None, discount_price=15)
    rid = _redeem(client, "m6-e@example.com", coupon_id=8)  # shop 4, Price $15
    r = _generate(client, fh, 4)
    assert r.status_code == 201, r.text
    assert r.json()["settlement"]["gross_amount"] == 15, r.text
    _revoke(client, rid)
    rep = _run_recon(client, fh, 4)
    assert rep["discrepancy_count"] == 0 and rep["status"] == "resolved"
    sid = r.json()["settlement"]["id"]
    statement = client.get(f"/api/v3/admin/settlements/{sid}", headers=fh).json()["settlement"]
    assert statement["gross_amount"] == statement["commission"] == statement["net_amount"] == 0
    assert statement["items"][0]["amount"] == 15
    assert statement["items"][0]["reversal"]["amount"] == -15


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
    assert r.json()["settlement"]["gross_amount"] == 0, r.text


def test_m8_cash_voucher_snapshot_and_legacy_positive_value_never_payable(client):
    fh = _finance_headers(client)
    rid = _redeem(client, "m8-cash@example.com", coupon_id=36)  # shop 20
    assert float(_redemption_snapshot(rid)) == 0
    # Historic snapshots are kept for audit but not treated as actual cash.
    _set_snapshot(rid, 50)
    paid = _generate(client, fh, 20)
    assert paid.status_code == 201, paid.text
    statement = paid.json()["settlement"]
    assert statement["gross_amount"] == statement["commission"] == statement["net_amount"] == 0
    assert any(i["redemption_id"] == rid and i["amount"] == 0 for i in statement["items"])
    assert float(_redemption_snapshot(rid)) == 50
    # Simulate an issued pre-M8 line. Reconciliation flags it, never rewrites it.
    with TestingSession() as db:
        line = db.query(SettlementItem).filter_by(redemption_id=rid).one()
        line.amount, line.commission = 50, 5
        legacy = db.get(Settlement, statement["id"])
        legacy.gross_amount, legacy.commission, legacy.net_amount = 50, 5, 45
        db.commit()
    ws = _run_recon(client, fh, 20)
    assert any(d["redemption_id"] == rid and d["discrepancy_type"] == "amount_mismatch"
               and d["expected_amount"] == 0 and d["actual_amount"] == 50
               for d in ws["discrepancies"]), ws
    url = f"/api/v3/admin/settlements/{statement['id']}"
    assert client.post(url + "/approve", headers=fh).status_code == 200
    blocked = client.post(url + "/pay", headers=fh)
    assert blocked.status_code == 409, blocked.text
    with TestingSession() as db:
        old = db.get(Settlement, statement["id"])
        assert old.status == "approved" and old.paid_at is None
        assert (old.gross_amount, old.commission, old.net_amount) == (50, 5, 45)
        assert db.query(SettlementItem).filter_by(redemption_id=rid).one().amount == 50
        assert not db.query(SettlementTransfer).filter_by(settlement_id=old.id).count()
        # Record an already-paid historic test fixture, never a gateway call.
        old.status, old.gross_amount, old.commission, old.net_amount = "paid", 50, 5, 45
        db.add(SettlementTransfer(settlement_id=old.id, amount=45, kind="payment",
                                  reference="historic-fixture"))
        db.commit()
    replay = client.post(url + "/pay", headers=fh,
                         json={"amount": 45, "reference": "historic-fixture"})
    assert replay.status_code == 200, replay.text
    with TestingSession() as db:
        assert db.query(SettlementTransfer).filter_by(settlement_id=statement["id"]).count() == 1


def test_m8_price_coupon_nonzero_statement_can_be_marked_paid(client):
    fh = _finance_headers(client)
    _set_coupon_fields(33, coupon_type="Price", discount_price=25, cash_amount=None)
    _redeem(client, "m8-price@example.com", coupon_id=33)  # shop 19
    result = _generate(client, fh, 19)
    assert result.status_code == 201, result.text
    s = result.json()["settlement"]
    assert s["gross_amount"] == 25 and s["net_amount"] == 22.5
    path = f"/api/v3/admin/settlements/{s['id']}"
    assert client.post(path + "/approve", headers=fh).status_code == 200
    pay = client.post(path + "/pay", headers=fh)
    assert pay.status_code == 200 and pay.json()["settlement"]["status"] == "paid", pay.text


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


def test_paid_package_credit_and_cash_recovery_are_independent(client):
    finance = _finance_headers(client)
    email = "settlement-credit@example.com"
    rid, order = _redeem_package(client, email, 11, price=19.95, quantity=2)
    customer = _login(client, email)
    sid = _generate(client, finance, 11).json()["settlement"]["id"]
    url = f"/api/v3/admin/settlements/{sid}"

    def statement():
        return client.get(url, headers=finance).json()["settlement"]

    assert client.get(url, headers=customer).status_code == 403
    assert client.post(url + "/approve", headers=finance).status_code == 200
    assert client.post(url + "/pay", headers=finance, json={"amount": 39.9, "reference": "qa-pay"}).status_code == 409
    for _ in range(2):
        assert client.post(url + "/pay", headers=finance, json={"amount": 35.91, "reference": "qa-pay"}).status_code == 200
    assert statement()["commission"] == 3.99
    inv_url = "/api/v3/admin/invoices"
    inv = client.post(inv_url, headers=finance, json={
        "shop_id": 11, "settlement_id": sid, "amount": 35.91, "title": "Audit"})
    assert inv.status_code == 201, inv.text
    inv_path = inv_url + "/" + str(inv.json()["invoice"]["id"])
    assert client.post(inv_path + "/approve", headers=finance).status_code == 200
    _revoke(client, rid)
    assert client.post(inv_path + "/issue", headers=finance).status_code == 409
    refund = client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=customer)
    assert refund.status_code == 200, refund.text
    assert refund.json()["order"]["refund_status"] == "refunded"
    s = statement()
    assert (s["gross_amount"], s["commission"], s["net_amount"]) == (0, 0, 0)
    assert (s["transferred_net"], s["outstanding_net"]) == (35.91, -35.91)
    assert len(s["items"]) == 1 and s["items"][0]["reversal"]["commission"] == -3.99
    assert [t["amount"] for t in s["transfers"]] == [35.91]
    assert _run_recon(client, finance, 11)["financials"]["outstanding_net"] == -35.91
    body = {"amount": -35.91, "reference": "recovery-1"}
    assert client.post(url + "/transfers", json=body, headers=customer).status_code == 403
    assert client.post(url + "/transfers", json={**body, "amount": -39.9}, headers=finance).status_code == 409
    for _ in range(2):
        assert client.post(url + "/transfers", json=body, headers=finance).status_code == 200
    assert [t["amount"] for t in statement()["transfers"]] == [35.91, -35.91]
    assert statement()["outstanding_net"] == statement()["transferred_net"] == 0
    assert _run_recon(client, finance, 11)["discrepancy_count"] == 0


def test_per_line_rounding_and_late_paid_sale(client):
    finance = _finance_headers(client)
    for n in range(2):
        _redeem_package(client, f"round-{n}@example.com", 12, price=1.05)
    s = _generate(client, finance, 12).json()["settlement"]
    url = f"/api/v3/admin/settlements/{s['id']}"
    assert (s["gross_amount"], s["commission"], s["net_amount"]) == (2.10, 0.20, 1.90)
    assert client.post(url + "/approve", headers=finance).status_code == 200
    assert client.post(url + "/pay", headers=finance).status_code == 200
    _redeem_package(client, "late-sale@example.com", 12, price=1.05)
    s = client.get(url, headers=finance).json()["settlement"]
    assert (s["commission"], s["net_amount"], s["transferred_net"], s["outstanding_net"]) == (0.3, 2.85, 1.9, 0.95)
    assert client.post(url + "/transfers", headers=finance,
                       json={"amount": 0.95, "reference": "supplement-1"}).status_code == 200
    assert _run_recon(client, finance, 12)["financials"]["outstanding_net"] == 0


def test_rescan_in_new_period_waits_for_its_own_statement(client):
    # Model a revoked November sale followed by a December scan of the same voucher.
    email = "settlement-rescan@example.com"
    finance = _finance_headers(client)
    rid, order = _redeem_package(client, email, 14)
    previous_month_end = PERIOD_START - timedelta(seconds=1)
    with TestingSession() as db:
        db.get(models.Redemption, rid).scanned_at = previous_month_end
        db.commit()
    r = client.post("/api/v3/admin/settlements/generate", headers=finance,
                    json={"shop_id": 14, "period": previous_month_end.strftime("%Y-%m")})
    assert r.status_code == 201, r.text
    # Revoke within the allowed window for that scan.
    from unittest.mock import patch
    with patch("app.routers.redemption._now", return_value=PERIOD_START):
        _revoke(client, rid)
    customer = _login(client, email)
    path = f"/api/v3/orders/{order['id']}"
    assert client.get(path, headers=customer).json()["order"]["net_amount"] == 0
    qr = client.post("/api/v3/store-scaned-qr-values", headers=customer,
                     params={"wallet_item_id": order["wallet_item_id"]}).json()
    scan = client.post("/api/v3/new-scan-coupon", headers=_vendor_headers(client), json={"code": qr["code"]})
    assert scan.status_code == 200, scan.text
    assert client.get(path, headers=customer).json()["order"]["settlement_status"] == "pending"
    assert _generate(client, finance, 14).status_code == 201
    assert client.get(path, headers=customer).json()["order"]["net_amount"] == 135


def test_issued_invoice_partial_credits_and_reconciliation(client):
    finance = _finance_headers(client)
    email = "issued-credit@example.com"
    rid, order = _redeem_package(client, email, 15, price=100)
    sid = _generate(client, finance, 15).json()["settlement"]["id"]
    statement_url = f"/api/v3/admin/settlements/{sid}"
    invoice_url = "/api/v3/admin/invoices"
    request = {"shop_id": 15, "settlement_id": sid, "amount": 90, "title": "Receipt"}
    invoice = client.post(invoice_url, json=request, headers=finance)
    assert invoice.status_code == 201, invoice.text
    path = invoice_url + "/" + str(invoice.json()["invoice"]["id"])
    assert client.post(path + "/approve", headers=finance).status_code == 200
    assert client.post(path + "/issue", headers=finance).status_code == 200
    _revoke(client, rid)
    customer = _login(client, email)
    assert client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=customer).status_code == 200
    s = client.get(statement_url, headers=finance).json()["settlement"]
    assert (s["invoice_issued_amount"], s["invoice_net_issued_amount"], s["invoice_available_amount"],
            s["invoice_excess_amount"]) == (90, 90, 0, 90)
    report = _run_recon(client, finance, 15)
    assert any(d["discrepancy_type"] == "invoice_over_settlement" and d["actual_amount"] == 90
               for d in report["discrepancies"])
    body = {"amount": 40, "reference": "CN-1"}
    assert client.post(path + "/credits", json=body, headers=customer).status_code == 403
    for _ in range(2):
        assert client.post(path + "/credits", json=body, headers=finance).status_code == 200
    assert client.post(path + "/credits", json={"amount": 41, "reference": "CN-1"}, headers=finance).status_code == 409
    assert client.post(path + "/credits", json={"amount": 51, "reference": "CN-2"}, headers=finance).status_code == 409
    s = client.get(statement_url, headers=finance).json()["settlement"]
    assert (s["invoice_issued_amount"], s["invoice_credited_amount"], s["invoice_net_issued_amount"],
            s["invoice_excess_amount"], s["invoice_available_amount"]) == (90, 40, 50, 50, 0)
    assert client.post(path + "/credits", json={"amount": 50, "reference": "CN-2"}, headers=finance).status_code == 200
    rep = _run_recon(client, finance, 15)
    assert rep["status"] == "resolved" and rep["financials"]["invoice_excess_amount"] == 0
    i = client.get(path, headers=finance).json()["invoice"]
    assert i["amount"] == 90 and i["credited_amount"] == 90 and len(i["credits"]) == 2


def test_simultaneous_credits_cannot_exceed_invoice(client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    finance = _finance_headers(client)
    _redeem_package(client, "credit-race@example.com", 8, price=100)
    sid = _generate(client, finance, 8).json()["settlement"]["id"]
    response = client.post("/api/v3/admin/invoices", headers=finance,
                           json={"shop_id": 8, "settlement_id": sid, "amount": 90, "title": "Race"})
    assert response.status_code == 201, response.text
    path = f"/api/v3/admin/invoices/{response.json()['invoice']['id']}"
    assert client.post(path + "/approve", headers=finance).status_code == 200
    assert client.post(path + "/issue", headers=finance).status_code == 200
    barrier = Barrier(2)

    def credit(reference):
        # The fixture owns app lifespan; concurrent clients only send requests.
        # Starting lifespan per worker races on the shared scheduler shutdown.
        local = TestClient(app)
        try:
            barrier.wait(timeout=10)
            return local.post(
                path + "/credits", headers=finance, json={"amount": 60, "reference": reference})
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(credit, ["credit-a", "credit-b"]))
    assert sorted(r.status_code for r in results) == [200, 409]
    inv = client.get(path, headers=finance).json()["invoice"]
    assert inv["credited_amount"] == 60 and len(inv["credits"]) == 1


def test_pending_invoice_reservation_after_redemption_reversal(client):
    finance = _finance_headers(client)
    rid, _ = _redeem_package(client, "invoice-reservation@example.com", 17, price=100)
    statement = _generate(client, finance, 17).json()["settlement"]
    url = "/api/v3/admin/invoices"
    ids = []
    for amount in (40, 50):
        response = client.post(url, headers=finance, json={
            "shop_id": 17, "settlement_id": statement["id"], "amount": amount, "title": "Test"})
        assert response.status_code == 201, response.text
        ids.append(response.json()["invoice"]["id"])
    assert client.post(f"{url}/{ids[0]}/approve", headers=finance).status_code == 200
    assert client.post(f"{url}/{ids[0]}/issue", headers=finance).status_code == 200
    _revoke(client, rid)
    summary = client.get(f"/api/v3/admin/settlements/{statement['id']}", headers=finance).json()["settlement"]
    assert (summary["invoice_net_issued_amount"], summary["invoice_reserved_amount"],
            summary["invoice_available_amount"], summary["invoice_excess_amount"]) == (40, 50, 0, 40)
    assert client.post(f"{url}/{ids[1]}/approve", headers=finance).status_code == 409
    assert client.post(f"{url}/{ids[1]}/reject", headers=finance,
                       json={"reason": "Settlement changed"}).status_code == 200
    summary = client.get(f"/api/v3/admin/settlements/{statement['id']}", headers=finance).json()["settlement"]
    assert summary["invoice_reserved_amount"] == 0 and summary["invoice_excess_amount"] == 40


def test_concurrent_invoice_issue_and_reject_are_serialized(client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    finance = _finance_headers(client)
    _redeem_package(client, "invoice-issue-reject@example.com", 18, price=100)
    sid = _generate(client, finance, 18).json()["settlement"]["id"]
    response = client.post("/api/v3/admin/invoices", headers=finance, json={
        "shop_id": 18, "settlement_id": sid, "amount": 90, "title": "Concurrent test"})
    assert response.status_code == 201, response.text
    path = f"/api/v3/admin/invoices/{response.json()['invoice']['id']}"
    assert client.post(path + "/approve", headers=finance).status_code == 200
    barrier = Barrier(2)

    def run(action):
        # Match the credit concurrency test: the outer fixture owns lifespan.
        local = TestClient(app)
        try:
            barrier.wait(timeout=10)
            return local.post(path + "/" + action, headers=finance,
                              json={"reason": "Review"} if action == "reject" else None)
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        issue, reject = pool.map(run, ["issue", "reject"])
    assert sorted([issue.status_code, reject.status_code]) == [200, 400]
    invoice = client.get(path, headers=finance).json()["invoice"]
    assert invoice["status"] == ("issued" if issue.status_code == 200 else "rejected")
