"""Merchant dashboard and monthly statements with Connect as payout source.

Uses an isolated SQLite database unless an explicit test PostgreSQL is selected.
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

PG_URL = os.environ.get("STATS_POSTGRES_URL", "")
TEMP_DB_DIR = tempfile.TemporaryDirectory(prefix="coupon_go_test_stats_")
SQLITE_DB = os.path.join(TEMP_DB_DIR.name,
                         "test.db")


def _pg_reachable():
    if os.environ.get("STATS_FORCE_SQLITE", "") == "1":
        return False
    if not PG_URL:
        return False
    from sqlalchemy.engine import make_url
    url = make_url(PG_URL)
    assert url.get_backend_name() == 'postgresql'
    assert url.host in (None, '', '127.0.0.1', 'localhost', '::1') and not url.password
    assert (url.database or '').startswith('coupon_stats_test_')
    try:
        probe = create_engine(PG_URL, connect_args={"connect_timeout": 2})
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
        probe.dispose()
        return True
    except Exception as exc:
        raise RuntimeError("Explicit isolated STATS_POSTGRES_URL is unreachable") from exc


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
    engine.dispose()


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


# ---------- P0-3 (§2.1): live balance ----------
def test_balance_pending_before_settlement(client):
    _redeem(client, "stats-bal-1@example.com", PRICE_COUPON)
    bal = _balance(client, PRICE_SHOP)
    assert bal["pending_settlement"] == pytest.approx(_net(PRICE_AMOUNT))
    assert bal["withdrawable"] == 0
    assert bal["withdrawn"] == 0
    assert bal["shop_id"] == PRICE_SHOP


def test_statement_approval_does_not_create_a_second_payout_balance(client):
    _redeem(client, "stats-bal-2@example.com", PRICE_COUPON_2)
    fh = _finance_headers(client)
    s = _generate(client, fh, PRICE_SHOP_2, PERIOD)
    before = _balance(client, PRICE_SHOP_2)
    _approve(client, s["id"])
    after = _balance(client, PRICE_SHOP_2)
    assert after == before
    assert after["pending_settlement"] == pytest.approx(_net(PRICE_AMOUNT_2))
    assert after["withdrawable"] == 0


def test_balance_cash_coupon_contributes_zero(client):
    before = _balance(client, CASH_SHOP)
    _redeem(client, "stats-bal-3@example.com", CASH_COUPON)
    after = _balance(client, CASH_SHOP)
    # M8: a cash coupon is a discount, not money collected — balance
    # must not move at all.
    assert after["pending_settlement"] == before["pending_settlement"]
    assert after["withdrawable"] == before["withdrawable"]
    assert after["legacy_untracked_redemptions"] == before["legacy_untracked_redemptions"]


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
    assert e["state"] == "pending"
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


def test_duplicate_settlement_withdrawal_routes_are_absent(client):
    vh = _vendor_headers(client)
    fh = _finance_headers(client)
    assert client.post("/api/v3/merchant/withdrawals", json={}, headers=vh).status_code == 404
    assert client.get("/api/v3/admin/merchant-withdrawals", headers=fh).status_code == 404


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
    assert nxt.hour == 2, nxt
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
        row[0]["redeemed_claims"] / row[0]["claims"])


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




def test_dashboard_balance_agrees_with_connect_ledger_and_pending_reversal(client):
    from app.models_connect import ConnectLedgerEntry

    vh = _vendor_headers(client)
    _, _, red = _redeem(client, "stats-connect-consistency@example.com", PRICE_COUPON)
    with TestingSession() as db:
        entry = db.query(ConnectLedgerEntry).filter_by(redemption_id=red).one()
        entry.status = "transferred"
        entry.pending_reversal = False
        db.commit()

    def check():
        stats = client.get(f"/api/v3/merchant/shops/{PRICE_SHOP}/balance", headers=vh)
        connect = client.get("/api/v3/merchant/connect/balance", params={"shop_id": PRICE_SHOP}, headers=vh)
        assert stats.status_code == connect.status_code == 200
        a, b = stats.json(), connect.json()
        assert (a["pending_settlement"], a["withdrawable"], a["reserved"], a["withdrawn"]) == (
            b["pending"], b["available"], b["processing"], b["paid_out"])
        return a

    before = check()
    with TestingSession() as db:
        entry = db.query(ConnectLedgerEntry).filter_by(redemption_id=red).one()
        entry.pending_reversal = True
        db.commit()
    after = check()
    assert after["withdrawable"] == pytest.approx(before["withdrawable"] - _net(PRICE_AMOUNT))
    entries = client.get(f"/api/v3/merchant/shops/{PRICE_SHOP}/balance/entries", headers=vh).json()["entries"]
    assert next(e for e in entries if e["redemption_id"] == red)["state"] == "reversal_pending"


def test_connect_settlement_cannot_also_be_manually_paid(client):
    from app.models_line_f import SettlementTransfer

    settlement = _approved_settlement(client, "stats-connect-no-double@example.com", 11, "2026-07")
    assert settlement["payment_mode"] == "connect"
    fh = _finance_headers(client)
    response = client.post(f"/api/v3/admin/settlements/{settlement['id']}/pay", headers=fh)
    assert response.status_code == 409
    with TestingSession() as db:
        assert db.query(SettlementTransfer).filter_by(settlement_id=settlement["id"]).count() == 0


def test_legacy_settlement_without_connect_keeps_manual_recording(client):
    from app.models_connect import ConnectLedgerEntry

    coupon = _priced_coupon_for_shop(12, 100)
    _, _, red = _redeem(client, "stats-legacy-pay@example.com", coupon)
    _backdate_redemption(red, "2026-07")
    with TestingSession() as db:
        db.query(ConnectLedgerEntry).filter_by(redemption_id=red).delete()
        db.commit()
    fh = _finance_headers(client)
    settlement = _generate(client, fh, 12, "2026-07")
    assert settlement["payment_mode"] == "legacy_manual"
    balance = _balance(client, 12)
    assert balance["balance_complete"] is False
    assert balance["legacy_untracked_redemptions"] >= 1
    _approve(client, settlement["id"])
    response = client.post(f"/api/v3/admin/settlements/{settlement['id']}/pay", headers=fh)
    assert response.status_code == 200, response.text
    assert response.json()["settlement"]["status"] == "paid"
    balance = _balance(client, 12)
    assert balance["balance_complete"] is True
    assert balance["legacy_untracked_redemptions"] == 0


def test_legacy_free_voucher_with_old_positive_snapshot_is_not_untracked(client):
    from app.models_connect import ConnectLedgerEntry

    before = _balance(client, GIFT_SHOP)
    user, wallet_id, red = _redeem(client, 'stats-legacy-gift@example.com', GIFT_COUPON)
    wallets = client.get('/api/v3/get-favorite-coupons-list', headers=user).json()['items']
    assert next(w for w in wallets if w['id'] == wallet_id)['order_paid_amount'] is None
    with TestingSession() as db:
        # Reconstruct a legacy erroneous face-value snapshot for a free gift.
        db.get(models.Redemption, red).face_value = 50
        db.query(ConnectLedgerEntry).filter_by(redemption_id=red).delete()
        db.commit()
    after = _balance(client, GIFT_SHOP)
    assert after['legacy_untracked_redemptions'] == before['legacy_untracked_redemptions']


def test_monthly_catchup_retries_failed_old_period_and_is_idempotent(client, monkeypatch):
    from app import merchant_finance as finance
    from app.models_line_f import Settlement
    period = f'{datetime.now(timezone.utc).year - 2}-04'
    shop_id = 14
    coupon_id = _priced_coupon_for_shop(shop_id, 100)
    _, _, red_id = _redeem(client, 'stats-catchup-retry@example.com', coupon_id)
    _backdate_redemption(red_id, period)
    original = finance.generate_settlement_for_shop
    attempts = []
    def fail_once(db, shop, target_period, *args, **kwargs):
        if shop.id == shop_id and target_period == period and not attempts:
            attempts.append('simulated failure')
            raise RuntimeError('simulated temporary failure')
        return original(db, shop, target_period, *args, **kwargs)
    monkeypatch.setattr(finance, 'generate_settlement_for_shop', fail_once)
    with TestingSession() as db:
        first = finance.catch_up_monthly_settlements(db)
        assert first['failed'] >= 1
        assert not db.query(Settlement).filter_by(shop_id=shop_id, period=period).first()
    with TestingSession() as db:
        second = finance.catch_up_monthly_settlements(db)
        assert second['generated'] >= 1
        assert db.query(Settlement).filter_by(shop_id=shop_id, period=period).count() == 1
    with TestingSession() as db:
        finance.catch_up_monthly_settlements(db)
        assert db.query(Settlement).filter_by(shop_id=shop_id, period=period).count() == 1


def test_conversion_excludes_redemptions_of_earlier_claims(client):
    _, wallet_id, _ = _redeem(client, 'stats-claim-cohort@example.com', PRICE_COUPON)
    with TestingSession() as db:
        db.get(models.WalletItem, wallet_id).claimed_at = _now() - timedelta(days=60)
        db.commit()
    response = client.get(f'/api/v3/merchant/shops/{PRICE_SHOP}/dashboard',
                          params={'days': 30}, headers=_vendor_headers(client))
    assert response.status_code == 200, response.text
    row = next(c for c in response.json()['coupons'] if c['coupon_id'] == PRICE_COUPON)
    assert 0 <= row['conversion_rate'] <= 1
    assert row['conversion_rate'] == pytest.approx(row['redeemed_claims'] / row['claims'] if row['claims'] else 0)
    assert row['redemptions'] > row['redeemed_claims']


def test_monthly_catchup_never_rewrites_existing_paid_period(client):
    from app import merchant_finance as finance
    from app.models_line_f import Settlement, SettlementItem
    period = f'{datetime.now(timezone.utc).year - 3}-03'
    shop_id = 11
    original = _approved_settlement(client, 'stats-paid-month@example.com', shop_id, period)
    coupon = _priced_coupon_for_shop(shop_id, 100)
    _, _, orphan = _redeem(client, 'stats-paid-orphan@example.com', coupon)
    _backdate_redemption(orphan, period)
    with TestingSession() as db:
        statement = db.get(Settlement, original['id'])
        statement.status = 'paid'
        original_gross, original_net = statement.gross_amount, statement.net_amount
        old_count = db.query(SettlementItem).filter_by(settlement_id=statement.id).count()
        db.commit()
    with TestingSession() as db:
        result = finance.catch_up_monthly_settlements(db)
        assert {'shop_id': shop_id, 'period': period} in result['blocked_existing_periods']
        statements = db.query(Settlement).filter_by(shop_id=shop_id, period=period).all()
        assert len(statements) == 1 and statements[0].status == 'paid'
        assert (statements[0].gross_amount, statements[0].net_amount) == (original_gross, original_net)
        assert db.query(SettlementItem).filter_by(settlement_id=statements[0].id).count() == old_count
        assert not db.query(SettlementItem).filter_by(redemption_id=orphan).first()


def test_monthly_allocation_conflict_is_failure_not_duplicate_skip(client, monkeypatch):
    from fastapi import HTTPException
    from app import merchant_finance as finance
    from app.models_line_f import Settlement
    period = f'{datetime.now(timezone.utc).year - 4}-05'
    shop_id = 15
    coupon_id = _priced_coupon_for_shop(shop_id, 100)
    _, _, red_id = _redeem(client, 'stats-allocation-conflict@example.com', coupon_id)
    _backdate_redemption(red_id, period)
    original = finance.generate_settlement_for_shop
    def conflict(db, shop, target_period, *args, **kwargs):
        if shop.id == shop_id and target_period == period:
            raise HTTPException(409, 'allocation needs review')
        return original(db, shop, target_period, *args, **kwargs)
    monkeypatch.setattr(finance, 'generate_settlement_for_shop', conflict)
    with TestingSession() as db:
        result = finance.catch_up_monthly_settlements(db)
        item = next(p for p in result['periods'] if p['period'] == period)
        assert item['failed'] >= 1
        assert any(x['shop_id'] == shop_id for x in item['failures'])
        assert not db.query(Settlement).filter_by(shop_id=shop_id, period=period).first()
