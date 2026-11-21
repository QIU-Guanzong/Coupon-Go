"""Dianping stats (GAP §2): merchant balance, withdrawals, monthly
settlement sweep, dashboard, basis notes, merchant dispute, CSV export.

What this file proves (TDD — written before the implementation):
- P0-3 (§2.1): a merchant sees a live balance — pending_settlement /
  withdrawable / withdrawn — aggregated from the settlement ledger,
  with a per-redemption entry list. Amounts follow settlement_basis
  only (package = Order.paid_amount, price coupon = discount_price,
  cash / free / pure-threshold = 0, refunded order = never settles).
- P0-4 (§2.2): a merchant can file a withdrawal against an APPROVED
  settlement, finance approves/rejects it, and payout execution FAILS
  CLOSED (503) while P1-B Stripe Connect is not wired in — it must
  never fall back to "mark as paid". The payout seam is
  app.payouts.execute_merchant_payout, monkeypatchable in tests so the
  paid / reversed path is provable without a real Stripe account.
- P1 (§2.3): the scheduler gains a 5th job, monthly-settlements —
  1st of month 02:00 UTC — generating last month's settlement for
  every shop with unsettled redemptions, reusing the same generation
  logic as POST /admin/settlements/generate.
- §2.4: merchant dashboard — redemption trend (daily), revenue,
  average order value, per-coupon conversion, per-shop breakdown.
- §2.5: settlement lines carry basis_note (package_paid /
  price_coupon / cash_zero / free_zero / refunded_skip) plus a
  plain-language basis_explainer on the settlement.
- §2.6: a merchant can dispute their own settlement from the
  merchant side (reuses the admin dispute state machine).
- Export: settlement lists (admin + merchant) support ?format=csv.
- Reconciliation stays truthful with partial payouts: an approved
  settlement with a partial withdrawal payment is NOT a discrepancy.

Isolation: every settlement-generating test owns one (shop, period)
pair — redemptions are backdated into that period before generation —
so tests never collide on uq_settlement_shop_period and never pollute
each other's balance assertions.

RUN MODE — same setup as test_f3_settlement_locks.py:
  * Postgres when reachable (STATS_POSTGRES_URL, default
    postgresql+psycopg2://coupon_go:coupon_go@localhost:5432/coupon_go_test_stats).
  * SQLite fallback in a fresh mkdtemp dir (never the shared /tmp
    coupon_go_test_*.db files the parallel blocks use).

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_dianping_stats.py -v
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import models  # noqa: E402,F401
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models_line_f import Settlement  # noqa: E402,F401
from app.seed import seed  # noqa: E402

PG_URL = os.environ.get(
    "STATS_POSTGRES_URL",
    "postgresql+psycopg2://coupon_go:coupon_go@localhost:5432/coupon_go_test_stats")
SQLITE_DB = os.path.join(tempfile.mkdtemp(prefix="coupon_go_test_stats_"),
                         "test.db")


def _pg_reachable():
    if os.environ.get("STATS_FORCE_SQLITE", "") == "1":
        return False
    try:
        probe = create_engine(PG_URL, connect_args={"connect_timeout": 2})
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
        probe.dispose()
        return True
    except Exception:
        return False


BACKEND = "postgres" if _pg_reachable() else "sqlite-immediate"

if BACKEND == "postgres":
    engine = create_engine(PG_URL, pool_size=5, max_overflow=5)
else:
    engine = create_engine(
        f"sqlite:///{SQLITE_DB}",
        connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "begin")
    def _begin_immediate(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")


TestingSession = sessionmaker(bind=engine)

PERIOD = datetime.now(timezone.utc).strftime("%Y-%m")
LAST_MONTH = (datetime.now(timezone.utc).replace(day=1)
              - timedelta(days=1)).strftime("%Y-%m")

# Seed fixtures. Price coupon 10 is $138 at shop 5; price coupon 15 is
# $299 at shop 8; cash coupon 2 is shop 1; gift coupon 4 is shop 2.
PRICE_COUPON = 10
PRICE_SHOP = 5
PRICE_AMOUNT = 138
PRICE_COUPON_2 = 15
PRICE_SHOP_2 = 8
PRICE_AMOUNT_2 = 299
CASH_COUPON = 2
CASH_SHOP = 1
GIFT_COUPON = 4
GIFT_SHOP = 2
RATE = 0.10


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture(scope="module")
def client():
    if BACKEND == "postgres":
        Base.metadata.drop_all(bind=engine)
    elif os.path.exists(SQLITE_DB):
        os.remove(SQLITE_DB)
    Base.metadata.create_all(bind=engine)
    db = TestingSession()
    seed(db)
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


# ---------- shared helpers (same pattern as test_f3_settlement_locks.py) ----------
def _register_and_login(client, email):
    assert client.post("/api/v3/new-register", json={
        "email": email, "password": "Demo123!", "name": "Test",
        "role": "customer"}).status_code == 200
    code = client.get("/api/v3/_dev/otp", params={"email": email}).json()["code"]
    r = client.post("/api/v3/new-verify-otp", json={
        "email": email, "code": code, "purpose": "register"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _login(client, email):
    r = client.post("/api/v3/login", json={
        "email": email, "password": "Demo123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _vendor_headers(client):
    return _login(client, "vendor@coupon-go.example")


def _finance_headers(client):
    return _login(client, "finance@coupon-go.example")


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


def _generate(client, headers, shop_id, period, rate=RATE):
    r = client.post("/api/v3/admin/settlements/generate", json={
        "shop_id": shop_id, "period": period, "commission_rate": rate},
        headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["settlement"]


def _approve(client, settlement_id):
    fh = _finance_headers(client)
    r = client.post(f"/api/v3/admin/settlements/{settlement_id}/approve",
                    headers=fh)
    assert r.status_code == 200, r.text
    return r.json()["settlement"]


def _balance(client, shop_id, headers=None):
    h = headers or _vendor_headers(client)
    r = client.get(f"/api/v3/merchant/shops/{shop_id}/balance", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _backdate_redemption(redemption_id, period):
    """Move a redemption's scanned_at into `period` (YYYY-MM)."""
    year, month = int(period[:4]), int(period[5:7])
    db = TestingSession()
    red = db.get(models.Redemption, redemption_id)
    red.scanned_at = datetime(year, month, 15, 12, 0, 0)
    db.commit()
    db.close()


def _net(amount):
    return round(amount * (1 - RATE), 2)


def _priced_coupon_for_shop(shop_id, amount):
    """Convert a shop's first coupon into a Price coupon of `amount`
    (same trick as test_f3's PRICED fixtures). Returns coupon id."""
    db = TestingSession()
    c = (db.query(models.Coupon).filter(models.Coupon.shop_id == shop_id)
         .order_by(models.Coupon.id).first())
    c.coupon_type = "Price"
    c.discount_price = amount
    c.cash_amount = None
    cid = c.id
    db.commit()
    db.close()
    return cid


def _approved_settlement(client, email, shop_id, period, amount=100):
    """One priced redemption backdated into `period`, generated and
    approved. Each test owns its (shop, period) pair."""
    cid = _priced_coupon_for_shop(shop_id, amount)
    _, _, rid = _redeem(client, email, cid)
    _backdate_redemption(rid, period)
    fh = _finance_headers(client)
    s = _generate(client, fh, shop_id, period)
    return _approve(client, s["id"])


def _create_withdrawal(client, settlement_id, amount, shop_id):
    vh = _vendor_headers(client)
    r = client.post("/api/v3/merchant/withdrawals", json={
        "shop_id": shop_id, "settlement_id": settlement_id,
        "amount": amount}, headers=vh)
    return r


# ---------- P0-3 (§2.1): live balance ----------
def test_balance_pending_before_settlement(client):
    _redeem(client, "stats-bal-1@example.com", PRICE_COUPON)
    bal = _balance(client, PRICE_SHOP)
    assert bal["pending_settlement"] == pytest.approx(_net(PRICE_AMOUNT))
    assert bal["withdrawable"] == 0
    assert bal["withdrawn"] == 0
    assert bal["shop_id"] == PRICE_SHOP


def test_balance_moves_through_states(client):
    _redeem(client, "stats-bal-2@example.com", PRICE_COUPON_2)
    fh = _finance_headers(client)
    s = _generate(client, fh, PRICE_SHOP_2, PERIOD)
    # Generated but not approved: still pending, not withdrawable.
    bal = _balance(client, PRICE_SHOP_2)
    assert bal["pending_settlement"] == pytest.approx(_net(PRICE_AMOUNT_2))
    assert bal["withdrawable"] == 0
    _approve(client, s["id"])
    bal = _balance(client, PRICE_SHOP_2)
    assert bal["withdrawable"] == pytest.approx(_net(PRICE_AMOUNT_2))
    assert bal["pending_settlement"] == 0
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/pay", headers=fh)
    assert r.status_code == 200, r.text
    bal = _balance(client, PRICE_SHOP_2)
    assert bal["withdrawable"] == 0
    assert bal["withdrawn"] == pytest.approx(_net(PRICE_AMOUNT_2))


def test_balance_cash_coupon_contributes_zero(client):
    before = _balance(client, CASH_SHOP)
    _redeem(client, "stats-bal-3@example.com", CASH_COUPON)
    after = _balance(client, CASH_SHOP)
    # M8: a cash coupon is a discount, not money collected — balance
    # must not move at all.
    assert after["pending_settlement"] == before["pending_settlement"]
    assert after["withdrawable"] == before["withdrawable"]


def test_balance_entries_show_per_redemption_state(client):
    _redeem(client, "stats-bal-4@example.com", PRICE_COUPON)
    vh = _vendor_headers(client)
    r = client.get(f"/api/v3/merchant/shops/{PRICE_SHOP}/balance/entries",
                   headers=vh)
    assert r.status_code == 200, r.text
    entries = r.json()["entries"]
    assert entries, "balance entries must list the unsettled redemption"
    mine = [e for e in entries if e["coupon_id"] == PRICE_COUPON]
    assert mine
    e = mine[-1]
    assert e["state"] == "pending_settlement"
    assert e["basis_amount"] == pytest.approx(PRICE_AMOUNT)
    assert e["commission"] == pytest.approx(PRICE_AMOUNT * RATE)
    assert e["net_amount"] == pytest.approx(_net(PRICE_AMOUNT))
    assert e["settlement_id"] is None


def test_balance_forbidden_for_other_merchant(client):
    h = _register_and_login(client, "stats-bal-5@example.com")
    r = client.get(f"/api/v3/merchant/shops/{PRICE_SHOP}/balance", headers=h)
    assert r.status_code == 403


def test_balance_aggregate_across_shops(client):
    vh = _vendor_headers(client)
    r = client.get("/api/v3/merchant/balance", headers=vh)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "pending_settlement" in body and "withdrawable" in body
    assert "withdrawn" in body and "shops" in body
    shop_ids = {s["shop_id"] for s in body["shops"]}
    assert PRICE_SHOP in shop_ids


# ---------- P0-4 (§2.2): withdrawals ----------
def test_withdrawal_create_validation(client):
    s = _approved_settlement(client, "stats-wd-1@example.com",
                             shop_id=3, period="2026-01")
    assert _create_withdrawal(client, s["id"], 0, 3).status_code == 400
    assert _create_withdrawal(client, s["id"], -5, 3).status_code == 400
    assert _create_withdrawal(client, s["id"], 999999, 3).status_code == 400
    assert _create_withdrawal(client, 999999, 10, 3).status_code == 404


def test_withdrawal_requires_approved_settlement(client):
    cid = _priced_coupon_for_shop(4, 100)
    _, _, rid = _redeem(client, "stats-wd-2@example.com", cid)
    _backdate_redemption(rid, "2026-02")
    fh = _finance_headers(client)
    s = _generate(client, fh, 4, "2026-02")  # pending, not approved
    assert _create_withdrawal(client, s["id"], 10, 4).status_code == 400


def test_withdrawal_reserves_balance(client):
    s = _approved_settlement(client, "stats-wd-3@example.com",
                             shop_id=6, period="2026-03")
    avail = _net(100)
    r = _create_withdrawal(client, s["id"], 50, 6)
    assert r.status_code == 201, r.text
    w = r.json()["withdrawal"]
    assert w["status"] == "pending"
    bal = _balance(client, 6)
    assert bal["withdrawable"] == pytest.approx(avail - 50)
    assert bal["reserved"] == pytest.approx(50)
    # The reserved 50 cannot be withdrawn a second time.
    assert _create_withdrawal(client, s["id"], avail - 50 + 0.01, 6).status_code == 400
    # ... but the exact remainder can.
    r2 = _create_withdrawal(client, s["id"], avail - 50, 6)
    assert r2.status_code == 201, r2.text


def test_withdrawal_approve_reject_cancel(client):
    s = _approved_settlement(client, "stats-wd-4@example.com",
                             shop_id=7, period="2026-04")
    fh = _finance_headers(client)
    vh = _vendor_headers(client)
    w1 = _create_withdrawal(client, s["id"], 10, 7).json()["withdrawal"]
    r = client.post(f"/api/v3/admin/merchant-withdrawals/{w1['id']}/approve",
                    headers=fh)
    assert r.status_code == 200, r.text
    assert r.json()["withdrawal"]["status"] == "approved"
    # An approved withdrawal can no longer be cancelled by the merchant.
    r = client.post(f"/api/v3/merchant/withdrawals/{w1['id']}/cancel",
                    headers=vh)
    assert r.status_code == 400
    w2 = _create_withdrawal(client, s["id"], 10, 7).json()["withdrawal"]
    r = client.post(f"/api/v3/admin/merchant-withdrawals/{w2['id']}/reject",
                    json={"reason": "資料不符，請重新提交"}, headers=fh)
    assert r.status_code == 200, r.text
    assert r.json()["withdrawal"]["status"] == "rejected"
    # Rejection frees the reservation (only w1's 10 stays reserved).
    bal = _balance(client, 7)
    assert bal["reserved"] == pytest.approx(10)
    w3 = _create_withdrawal(client, s["id"], 5, 7).json()["withdrawal"]
    r = client.post(f"/api/v3/merchant/withdrawals/{w3['id']}/cancel",
                    headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["withdrawal"]["status"] == "cancelled"


def test_withdrawal_execute_payout_fails_closed_without_p1b(client):
    """No Stripe Connect wired in yet: executing a payout must fail
    closed (503) and must NOT mark anything paid."""
    s = _approved_settlement(client, "stats-wd-5@example.com",
                             shop_id=9, period="2026-05")
    fh = _finance_headers(client)
    w = _create_withdrawal(client, s["id"], 10, 9).json()["withdrawal"]
    client.post(f"/api/v3/admin/merchant-withdrawals/{w['id']}/approve",
                headers=fh)
    r = client.post(
        f"/api/v3/admin/merchant-withdrawals/{w['id']}/execute-payout",
        headers=fh)
    assert r.status_code == 503, r.text
    assert "Stripe" in r.text or "Connect" in r.text or "P1-B" in r.text
    after = client.get(f"/api/v3/merchant/withdrawals/{w['id']}",
                       headers=_vendor_headers(client)).json()["withdrawal"]
    assert after["status"] == "approved"  # unchanged, nothing paid
    assert after["payout_reference"] in (None, "")
    bal = _balance(client, 9)
    assert bal["withdrawn"] == 0


def test_withdrawal_execute_payout_and_reverse_with_fake_provider(
        client, monkeypatch):
    """The P1-B seam: patching app.payouts.execute_merchant_payout
    stands in for the real Stripe Connect payout. Full payout marks
    the settlement paid; a reversal (payout bounced) restores it."""
    import app.payouts as payouts

    def _fake_payout(db, withdrawal):
        return {"payout_reference": "po_test_fake_001",
                "provider": "stripe_connect"}

    monkeypatch.setattr(payouts, "execute_merchant_payout", _fake_payout)
    s = _approved_settlement(client, "stats-wd-6@example.com",
                             shop_id=10, period="2026-06")
    fh = _finance_headers(client)
    avail = _net(100)
    w = _create_withdrawal(client, s["id"], avail, 10).json()["withdrawal"]
    client.post(f"/api/v3/admin/merchant-withdrawals/{w['id']}/approve",
                headers=fh)
    r = client.post(
        f"/api/v3/admin/merchant-withdrawals/{w['id']}/execute-payout",
        headers=fh)
    assert r.status_code == 200, r.text
    paid = r.json()["withdrawal"]
    assert paid["status"] == "paid"
    assert paid["payout_reference"] == "po_test_fake_001"
    # Settlement fully transferred -> paid.
    got = client.get(f"/api/v3/admin/settlements/{s['id']}",
                     headers=fh).json()["settlement"]
    assert got["status"] == "paid"
    assert got["transferred_net"] == pytest.approx(avail)
    bal = _balance(client, 10)
    assert bal["withdrawn"] == pytest.approx(avail)
    # Reversal: payout bounced after the fact.
    r = client.post(
        f"/api/v3/admin/merchant-withdrawals/{w['id']}/reverse",
        json={"reason": "銀行退票"}, headers=fh)
    assert r.status_code == 200, r.text
    assert r.json()["withdrawal"]["status"] == "reversed"
    got = client.get(f"/api/v3/admin/settlements/{s['id']}",
                     headers=fh).json()["settlement"]
    assert got["status"] == "approved"  # back to withdrawable
    assert got["transferred_net"] == pytest.approx(0)
    bal = _balance(client, 10)
    assert bal["withdrawable"] == pytest.approx(avail)


def test_withdrawal_admin_list(client):
    s = _approved_settlement(client, "stats-wd-7@example.com",
                             shop_id=11, period="2026-07")
    _create_withdrawal(client, s["id"], 10, 11)
    fh = _finance_headers(client)
    r = client.get("/api/v3/admin/merchant-withdrawals", headers=fh)
    assert r.status_code == 200, r.text
    assert r.json()["total"] >= 1
    vh = _vendor_headers(client)
    r = client.get("/api/v3/merchant/withdrawals", headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["total"] >= 1


# ---------- P1 (§2.3): monthly settlement sweep ----------
def test_monthly_settlement_sweep(client):
    from app.merchant_finance import sweep_monthly_settlements
    _, _, rid1 = _redeem(client, "stats-sched-1@example.com", PRICE_COUPON)
    _, _, rid2 = _redeem(client, "stats-sched-2@example.com", GIFT_COUPON)
    _backdate_redemption(rid1, LAST_MONTH)
    _backdate_redemption(rid2, LAST_MONTH)
    db = TestingSession()
    result = sweep_monthly_settlements(db, period=LAST_MONTH)
    db.close()
    assert result["generated"] == 2, result
    assert result["failed"] == 0, result
    assert result["period"] == LAST_MONTH
    # Idempotent: a second run generates nothing for the same period.
    db = TestingSession()
    again = sweep_monthly_settlements(db, period=LAST_MONTH)
    db.close()
    assert again["generated"] == 0, again
    assert again["skipped"] == 2, again
    # The generated settlements hold the right amounts (price 138 net
    # 124.20; gift coupon settles at 0).
    fh = _finance_headers(client)
    r = client.get("/api/v3/admin/settlements",
                   params={"shop_id": PRICE_SHOP}, headers=fh)
    mine = [s for s in r.json()["settlements"]
            if s["period"] == LAST_MONTH]
    assert mine and mine[0]["net_amount"] == pytest.approx(_net(PRICE_AMOUNT))


def test_scheduler_registers_monthly_settlements_job(client):
    from app.scheduler import JOB_NAMES, create_scheduler
    assert "monthly-settlements" in JOB_NAMES
    sched = create_scheduler(session_factory=TestingSession,
                             interval_seconds=3600)
    job = sched.get_job("monthly-settlements")
    assert job is not None
    # APScheduler 3.11 Job has no next_run_time attribute; ask the
    # trigger for its next fire time instead.
    nxt = job.trigger.get_next_fire_time(
        None, datetime.now(timezone.utc))
    assert nxt.day == 1 and nxt.hour == 2, nxt
    if sched.running:
        sched.shutdown(wait=False)


# ---------- §2.4: dashboard ----------
def test_dashboard_totals_trend_and_coupon_conversion(client):
    _redeem(client, "stats-dash-1@example.com", PRICE_COUPON)
    _redeem(client, "stats-dash-2@example.com", PRICE_COUPON)
    vh = _vendor_headers(client)
    r = client.get(f"/api/v3/merchant/shops/{PRICE_SHOP}/dashboard",
                   params={"days": 30}, headers=vh)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["totals"]["redemptions"] >= 2
    assert d["totals"]["gross_amount"] >= 2 * PRICE_AMOUNT
    assert d["totals"]["avg_order_value"] == pytest.approx(PRICE_AMOUNT)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bucket = [t for t in d["trend"] if t["date"] == today]
    assert bucket and bucket[0]["redemptions"] >= 2
    row = [c for c in d["coupons"] if c["coupon_id"] == PRICE_COUPON]
    assert row
    assert row[0]["claims"] >= 2
    assert row[0]["redemptions"] >= 2
    assert row[0]["conversion_rate"] == pytest.approx(
        row[0]["redemptions"] / row[0]["claims"])


def test_dashboard_aggregate_per_shop_breakdown(client):
    vh = _vendor_headers(client)
    r = client.get("/api/v3/merchant/dashboard", params={"days": 30},
                   headers=vh)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "totals" in d and "shops" in d
    assert any(s["shop_id"] == PRICE_SHOP for s in d["shops"])


# ---------- §2.5: basis notes ----------
def test_basis_note_on_settlement_lines(client):
    _redeem(client, "stats-note-1@example.com", PRICE_COUPON)
    _redeem(client, "stats-note-2@example.com", CASH_COUPON)
    _redeem(client, "stats-note-3@example.com", GIFT_COUPON)
    fh = _finance_headers(client)
    s = _generate(client, fh, PRICE_SHOP, PERIOD)
    _generate(client, fh, CASH_SHOP, PERIOD)
    _generate(client, fh, GIFT_SHOP, PERIOD)
    r = client.get(f"/api/v3/admin/settlements/{s['id']}", headers=fh)
    assert r.status_code == 200, r.text
    detail = r.json()["settlement"]
    assert detail.get("basis_explainer"), "settlement must explain its basis"
    items = {i["coupon_id"]: i for i in detail["items"]}
    assert items[PRICE_COUPON]["basis_note"] == "price_coupon"
    assert items[PRICE_COUPON]["amount"] == pytest.approx(PRICE_AMOUNT)
    # Cash + gift lines live on their own settlements; check them too.
    for shop_id, coupon_id, note in (
            (CASH_SHOP, CASH_COUPON, "cash_zero"),
            (GIFT_SHOP, GIFT_COUPON, "free_zero")):
        sid = [x for x in client.get(
            "/api/v3/admin/settlements", params={"shop_id": shop_id},
            headers=fh).json()["settlements"]
            if x["period"] == PERIOD][0]["id"]
        det = client.get(f"/api/v3/admin/settlements/{sid}",
                         headers=fh).json()["settlement"]
        line = [i for i in det["items"] if i["coupon_id"] == coupon_id]
        assert line and line[0]["basis_note"] == note, det["items"]
        assert line[0]["amount"] == pytest.approx(0)


# ---------- §2.6: merchant dispute ----------
def test_merchant_can_dispute_own_settlement(client):
    cid = _priced_coupon_for_shop(13, 100)
    _, _, rid = _redeem(client, "stats-disp-1@example.com", cid)
    _backdate_redemption(rid, "2026-09")
    fh = _finance_headers(client)
    s = _generate(client, fh, 13, "2026-09")
    vh = _vendor_headers(client)
    r = client.post(f"/api/v3/merchant/settlements/{s['id']}/dispute",
                    json={"reason": "有一筆核銷金額不對"}, headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["settlement"]["status"] == "disputed"
    # A disputed settlement cannot be approved until resolved.
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/approve",
                    headers=fh)
    assert r.status_code == 400
    # Another merchant (a plain customer here) cannot dispute it.
    h = _register_and_login(client, "stats-disp-2@example.com")
    r = client.post(f"/api/v3/merchant/settlements/{s['id']}/dispute",
                    json={"reason": "x"}, headers=h)
    assert r.status_code == 403


# ---------- export ----------
def test_settlements_csv_export(client):
    _redeem(client, "stats-csv-1@example.com", PRICE_COUPON)
    fh = _finance_headers(client)
    # Shop 5 / current period may already have a settlement from the
    # basis-note test; only generate when missing.
    existing = [x for x in client.get(
        "/api/v3/admin/settlements", params={"shop_id": PRICE_SHOP},
        headers=fh).json()["settlements"] if x["period"] == PERIOD]
    if not existing:
        _generate(client, fh, PRICE_SHOP, PERIOD)
    r = client.get("/api/v3/admin/settlements",
                   params={"format": "csv"}, headers=fh)
    assert r.status_code == 200, r.text
    assert "text/csv" in r.headers["content-type"]
    assert "period" in r.text and PERIOD in r.text
    vh = _vendor_headers(client)
    r = client.get(f"/api/v3/merchant/shops/{PRICE_SHOP}/settlements",
                   params={"format": "csv"}, headers=vh)
    assert r.status_code == 200, r.text
    assert "period" in r.text


# ---------- reconciliation stays truthful with partial payouts ----------
def test_reconciliation_ok_with_partial_withdrawal_payment(
        client, monkeypatch):
    import app.payouts as payouts
    monkeypatch.setattr(
        payouts, "execute_merchant_payout",
        lambda db, w: {"payout_reference": "po_test_partial_001",
                       "provider": "stripe_connect"})
    s = _approved_settlement(client, "stats-recon-1@example.com",
                             shop_id=12, period="2026-08")
    fh = _finance_headers(client)
    w = _create_withdrawal(client, s["id"], 50, 12).json()["withdrawal"]
    client.post(f"/api/v3/admin/merchant-withdrawals/{w['id']}/approve",
                headers=fh)
    r = client.post(
        f"/api/v3/admin/merchant-withdrawals/{w['id']}/execute-payout",
        headers=fh)
    assert r.status_code == 200, r.text
    # Settlement is still approved (partially paid) and reconciliation
    # must NOT flag the partial payment as a discrepancy.
    r = client.post("/api/v3/admin/reconciliation/run", json={
        "shop_id": 12, "period": "2026-08"}, headers=fh)
    assert r.status_code == 201, r.text
    rep = r.json()["report"]
    bad = [d for d in rep["discrepancies"]
           if d["discrepancy_type"].startswith("payment")]
    assert bad == [], rep["discrepancies"]


# ---------- migration chain ----------
def test_migration_chain_single_head_is_0021():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config()
    cfg.set_main_option(
        "script_location",
        os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert list(heads) == ["0021_merchant_withdrawals"], heads
