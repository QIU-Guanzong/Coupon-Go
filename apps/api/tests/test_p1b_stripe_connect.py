"""P1-B tests: Stripe Connect (Accounts v2) — onboarding, post-redemption
transfer, merchant balance / ledger / payout, reversal on revoke /
refund, and fail-closed when Connect is not configured.

Run:  cd apps/api && python -m pytest tests/test_p1b_stripe_connect.py -v
Uses its own throwaway SQLite file so parallel lines do not delete each
other's test DB. No real Stripe key: the connect gateway runs its local
test-mode simulator (acct_test_… / tr_test_… / po_test_…), same as the
existing payments_gateway test mode.

Money path under test (FEATURE_GAP_20261120 §3 decision 3): user pays
into the platform; after redemption the platform commission is deducted
(MerchantCommissionRate, default 10%) and the net is transferred to the
merchant's Connect account; the merchant withdraws via Payout (manual
payouts). P1-A (paid ordinary coupons) is not on this baseline, so the
paid chain is verified through package orders (paid_amount basis).
"""
import os
import sys
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import config, models  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

TEST_DB = "/tmp/coupon_go_test_p1b.db"

# Postgres probe (same pattern as F3 / FIX-3 / M3): when P1B_POSTGRES_URL
# points at a reachable server the suite runs against real Postgres
# (row locks are real there; SQLite ignores with_for_update). Otherwise
# it falls back to the throwaway SQLite file. BACKEND is reported so a
# failure shows which run mode it ran in.
PG_URL = os.environ.get(
    "P1B_POSTGRES_URL",
    # NOTE: explicit +psycopg2 driver — this venv ships psycopg2-binary
    # only; a bare postgresql:// URL makes SQLAlchemy 2.x look for
    # psycopg (v3) and fail to import.
    "postgresql+psycopg2://coupon_go:coupon_go@localhost:5432/coupon_go_test_p1b")


def _pg_reachable():
    try:
        from sqlalchemy import create_engine as _ce, text as _text
        probe = _ce(PG_URL, connect_args={"connect_timeout": 2})
        with probe.connect() as conn:
            conn.execute(_text("SELECT 1"))
        probe.dispose()
        return True
    except Exception:
        return False


BACKEND = "postgres" if _pg_reachable() else "sqlite"

if BACKEND == "postgres":
    engine = create_engine(PG_URL, pool_size=5, max_overflow=5)
else:
    engine = create_engine(f"sqlite:///{TEST_DB}", connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine)


@pytest.fixture(scope="module")
def client():
    if BACKEND == "postgres":
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


def _db():
    return TestingSession()


def _register_and_login(client, email, role="customer"):
    assert client.post("/api/v3/new-register", json={
        "email": email, "password": "Demo123!", "name": "Test",
        "role": role}).status_code == 200
    code = client.get("/api/v3/_dev/otp", params={"email": email}).json()["code"]
    r = client.post("/api/v3/new-verify-otp", json={
        "email": email, "code": code, "purpose": "register"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _vendor_headers(client):
    r = client.post("/api/v3/login", json={
        "email": "vendor@coupon-go.example", "password": "Demo123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _create_package(client, name="P1-B 套餐", stock=5, price=150, original=200,
                    shop_id=1):
    vh = _vendor_headers(client)
    body = {
        "shop_id": shop_id, "name": name,
        "items": [{"name": "主菜一份", "qty": 1}],
        "person_count": 2, "original_price": original, "package_price": price,
        "valid_from": "2026-11-01T00:00:00",
        "valid_to": "2026-12-31T00:00:00",
        "stock_total": stock, "refund_policy": "anytime",
    }
    r = client.post("/api/v3/packages", json=body, headers=vh)
    assert r.status_code == 201, r.text
    return r.json()["package"]


def _buy_paid(client, headers, package_id, method="card"):
    r = client.post(f"/api/v3/packages/{package_id}/purchase",
                    json={"payment_method": method}, headers=headers)
    assert r.status_code == 200, r.text
    order_id = r.json()["order"]["id"]
    r = client.post(f"/api/v3/orders/{order_id}/confirm-payment",
                    json={"outcome": "succeeded"}, headers=headers)
    assert r.status_code == 200, r.text
    order = r.json()["order"]
    assert order["payment_status"] == "paid"
    return order


def _qr_and_scan(client, user_headers, vh, wallet_item_id):
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wallet_item_id},
                     headers=user_headers)
    assert qr.status_code == 200, qr.text
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 200, r.text
    return r.json()


def _claim_and_scan(client, user_headers, vh, coupon_id):
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": coupon_id}, headers=user_headers)
    assert r.status_code == 200, r.text
    return _qr_and_scan(client, user_headers, vh, r.json()["wallet_item_id"])


def _onboard(client, vh, shop_id=1):
    r = client.post("/api/v3/merchant/connect/onboarding", json={
        "shop_id": shop_id,
        "return_url": "coupon-go://connect/return",
        "refresh_url": "coupon-go://connect/refresh"}, headers=vh)
    assert r.status_code == 200, r.text
    return r.json()


def _sync(client, vh, shop_id=1):
    r = client.post("/api/v3/merchant/connect/sync",
                    json={"shop_id": shop_id}, headers=vh)
    assert r.status_code == 200, r.text
    return r.json()


def _balance(client, vh, shop_id=1):
    r = client.get("/api/v3/merchant/connect/balance",
                   params={"shop_id": shop_id}, headers=vh)
    assert r.status_code == 200, r.text
    return r.json()


def _ledger(client, vh, shop_id=1):
    r = client.get("/api/v3/merchant/connect/ledger",
                   params={"shop_id": shop_id}, headers=vh)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- onboarding + sync ----------

def test_onboarding_creates_account_and_link(client):
    vh = _vendor_headers(client)
    out = _onboard(client, vh, shop_id=2)
    assert out["stripe_account_id"].startswith("acct_test_")
    assert out["onboarding_url"].startswith("https://accounts.stripe.test/r/")
    assert out["onboarding_status"] == "pending"
    assert out["charges_enabled"] is False
    assert out["payouts_enabled"] is False
    assert out["connect_configured"] is True
    with _db() as db:
        row = db.query(models.ConnectAccount).filter_by(shop_id=2).one()
        assert row.stripe_account_id == out["stripe_account_id"]
        # No bank account is ever stored: Stripe collects it.
        assert not hasattr(row, "bank_account")
    # Re-onboarding reuses the same account, only a fresh link.
    out2 = _onboard(client, vh, shop_id=2)
    assert out2["stripe_account_id"] == out["stripe_account_id"]


def test_sync_records_flags_and_manual_payouts(client, monkeypatch):
    vh = _vendor_headers(client)
    _onboard(client, vh, shop_id=3)
    from app import connect_gateway
    monkeypatch.setattr(connect_gateway, "retrieve_account", lambda account_id: {
        "account_id": account_id, "charges_enabled": False,
        "payouts_enabled": False, "details_submitted": False,
        "requirements": ["external_account"], "simulated": True})
    out = _sync(client, vh, shop_id=3)
    assert out["onboarding_status"] == "incomplete"
    assert out["manual_payouts"] is False
    monkeypatch.setattr(connect_gateway, "retrieve_account", lambda account_id: {
        "account_id": account_id, "charges_enabled": True,
        "payouts_enabled": True, "details_submitted": True,
        "requirements": [], "simulated": True})
    out = _sync(client, vh, shop_id=3)
    assert out["onboarding_status"] == "active"
    assert out["charges_enabled"] is True and out["payouts_enabled"] is True
    assert out["manual_payouts"] is True  # set to manual once payouts enabled
    st = client.get("/api/v3/merchant/connect/status",
                    params={"shop_id": 3}, headers=vh).json()
    assert st["payout_hold_days"] == 90  # HK: manual-payout funds within 90 days


def test_status_without_account(client):
    vh = _vendor_headers(client)
    r = client.get("/api/v3/merchant/connect/status",
                   params={"shop_id": 9}, headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["stripe_account_id"] is None
    assert r.json()["onboarding_status"] == "pending"


# ---------- redemption -> pending -> retry -> transferred ----------

def test_scan_package_pending_then_retry_transfers(client):
    h = _register_and_login(client, "p1b-scan@example.com")
    vh = _vendor_headers(client)
    pkg = _create_package(client, name="P1-B 核銷套餐", price=150)
    paid = _buy_paid(client, h, pkg["id"])
    scan = _qr_and_scan(client, h, vh, paid["wallet_item_id"])
    assert scan["connect"]["transfer"] == "pending"  # shop 1: no account yet
    assert scan["connect"]["net_amount"] == 135.00  # 150 - 10% commission
    bal = _balance(client, vh, shop_id=1)
    assert bal["pending"] == 135.00 and bal["available"] == 0.00
    entries = _ledger(client, vh, shop_id=1)["entries"]
    entry = [e for e in entries if e["redemption_id"] == scan["redemption_id"]]
    assert len(entry) == 1
    assert entry[0]["status"] == "pending"
    assert entry[0]["gross_amount"] == 150.00
    assert entry[0]["commission"] == 15.00
    # Onboard + sync, then retry the pending transfer.
    _onboard(client, vh, shop_id=1)
    _sync(client, vh, shop_id=1)
    r = client.post(
        f"/api/v3/merchant/connect/transfers/{entry[0]['id']}/retry", headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "transferred"
    assert r.json()["stripe_transfer_id"].startswith("tr_test_")
    bal = _balance(client, vh, shop_id=1)
    assert bal["pending"] == 0.00 and bal["available"] == 135.00
    # Retry again is a no-op conflict, never a second transfer.
    r = client.post(
        f"/api/v3/merchant/connect/transfers/{entry[0]['id']}/retry", headers=vh)
    assert r.status_code == 409


def test_scan_price_coupon_transfers_when_account_ready(client):
    h = _register_and_login(client, "p1b-price@example.com")
    vh = _vendor_headers(client)
    # Shop 5 (seed coupon 10: Price $138 belongs to shops[4], id 5)
    # — onboard it first.
    _onboard(client, vh, shop_id=5)
    _sync(client, vh, shop_id=5)
    scan = _claim_and_scan(client, h, vh, coupon_id=10)
    assert scan["connect"]["transfer"] == "transferred"
    assert scan["connect"]["net_amount"] == 124.20  # 138 - 10%
    bal = _balance(client, vh, shop_id=5)
    assert bal["available"] == 124.20


def test_free_coupon_creates_no_entry(client):
    h = _register_and_login(client, "p1b-free@example.com")
    vh = _vendor_headers(client)
    # Seed coupon 1: percentage-only, settlement basis 0.
    scan = _claim_and_scan(client, h, vh, coupon_id=1)
    assert scan["connect"]["transfer"] == "skipped_zero"
    with _db() as db:
        rows = db.query(models.ConnectLedgerEntry).filter_by(
            redemption_id=scan["redemption_id"]).all()
        assert rows == []


# ---------- payout ----------

def test_payout_moves_available_to_paid_out(client):
    vh = _vendor_headers(client)
    # Shop 5 has 124.20 available from the price-coupon test.
    bal = _balance(client, vh, shop_id=5)
    assert bal["available"] == 124.20
    r = client.post("/api/v3/merchant/connect/payouts",
                    json={"shop_id": 5, "amount": 100.00}, headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["stripe_payout_id"].startswith("po_test_")
    assert r.json()["balance"]["available"] == 24.20
    assert r.json()["balance"]["paid_out"] == 100.00
    # Over-payout is rejected and changes nothing.
    r = client.post("/api/v3/merchant/connect/payouts",
                    json={"shop_id": 5, "amount": 100.00}, headers=vh)
    assert r.status_code == 422
    assert _balance(client, vh, shop_id=5)["available"] == 24.20
    # Zero / negative amounts rejected.
    r = client.post("/api/v3/merchant/connect/payouts",
                    json={"shop_id": 5, "amount": 0}, headers=vh)
    assert r.status_code == 422


def test_payout_without_ready_account_rejected(client):
    vh = _vendor_headers(client)
    # Shop 7 has no Connect account at all.
    r = client.post("/api/v3/merchant/connect/payouts",
                    json={"shop_id": 7, "amount": 10.00}, headers=vh)
    assert r.status_code == 409


# ---------- reversal on revoke / refund ----------

def test_revoke_reverses_transferred_entry(client):
    h = _register_and_login(client, "p1b-revoke@example.com")
    vh = _vendor_headers(client)
    pkg = _create_package(client, name="P1-B 撤销套餐", price=150)
    paid = _buy_paid(client, h, pkg["id"])
    scan = _qr_and_scan(client, h, vh, paid["wallet_item_id"])
    assert scan["connect"]["transfer"] == "transferred"  # shop 1 ready now
    rid = scan["redemption_id"]
    r = client.post(f"/api/v3/redemptions/{rid}/revoke", headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["connect"]["reversed"] is True
    with _db() as db:
        row = db.query(models.ConnectLedgerEntry).filter_by(
            redemption_id=rid).one()
        assert row.status == "reversed"
        assert (row.stripe_reversal_id or "").startswith("trr_test_")
    # Shop 1 keeps the earlier retried transfer (135.00); only this
    # redemption's 135.00 was clawed back.
    assert _balance(client, vh, shop_id=1)["available"] == 135.00


def test_refund_after_revoke_does_not_double_reverse(client):
    h = _register_and_login(client, "p1b-refrev@example.com")
    vh = _vendor_headers(client)
    pkg = _create_package(client, name="P1-B 退款冲回套餐", price=150)
    paid = _buy_paid(client, h, pkg["id"])
    scan = _qr_and_scan(client, h, vh, paid["wallet_item_id"])
    rid = scan["redemption_id"]
    assert client.post(f"/api/v3/redemptions/{rid}/revoke", headers=vh).status_code == 200
    r = client.post(f"/api/v3/orders/{paid['id']}/refund",
                    json={"reason": "行程有變"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["order"]["refund_status"] == "refunded"
    with _db() as db:
        rows = db.query(models.ConnectLedgerEntry).filter_by(
            redemption_id=rid).all()
        assert len(rows) == 1
        assert rows[0].status == "reversed"


# ---------- fail closed ----------

def test_connect_disabled_fails_closed(client, monkeypatch):
    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED_RAW", "0")
    vh = _vendor_headers(client)
    r = client.post("/api/v3/merchant/connect/onboarding", json={
        "shop_id": 6, "return_url": "coupon-go://connect/return",
        "refresh_url": "coupon-go://connect/refresh"}, headers=vh)
    assert r.status_code == 503
    h = _register_and_login(client, "p1b-closed@example.com")
    pkg = _create_package(client, name="P1-B 未配置套餐", price=150, shop_id=6)
    paid = _buy_paid(client, h, pkg["id"])
    scan = _qr_and_scan(client, h, vh, paid["wallet_item_id"])
    # Redemption still succeeds, but the money never moves: pending with
    # an explicit not_configured note — never "marked paid".
    assert scan["connect"]["transfer"] == "not_configured"
    with _db() as db:
        row = db.query(models.ConnectLedgerEntry).filter_by(
            redemption_id=scan["redemption_id"]).one()
        assert row.status == "pending"
        assert "not_configured" in (row.note or "")
        # The monthly Settlement is untouched by Connect: nothing paid.
        paid_rows = db.query(models.Settlement).filter_by(
            shop_id=6, status="paid").all()
        assert paid_rows == []
    r = client.post("/api/v3/merchant/connect/payouts",
                    json={"shop_id": 6, "amount": 10.00}, headers=vh)
    assert r.status_code == 503
    entry_id = row.id
    r = client.post(
        f"/api/v3/merchant/connect/transfers/{entry_id}/retry", headers=vh)
    assert r.status_code == 503
    monkeypatch.undo()
    # Once configured again, the same entry retries to transferred
    # (shop 6 has no account yet, so it stays pending as no_account).
    r = client.post(
        f"/api/v3/merchant/connect/transfers/{entry_id}/retry", headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"


# ---------- Accounts v2 gateway (mocked Stripe HTTP) ----------
# Miso 2026-11-20 拍板：P1-B 从 Express v1 迁到 Accounts v2。开户 / 开户链接 /
# 状态查询走 v2（/v2/core/accounts、/v2/core/account_links）；转帐 / 冲回 /
# 提款 / 手动提款仍走 v1——Stripe 官方说明 v2 帐户 id 可直接传给 v1 端点
# （见 docs/P1B_API.md）。以下测试 mock httpx，不碰真实 Stripe。

class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = ""

    def json(self):
        return self._payload


def _v2_account_payload(**caps):
    """A v2 Account payload. caps: card/payouts/transfers statuses."""
    def _cap(status):
        return {"requested": True, "status": status}
    return {
        "id": "acct_v2_test_123",
        "object": "v2.core.account",
        "configuration": {
            "merchant": {"capabilities": {
                "card_payments": _cap(caps.get("card", "pending")),
                "stripe_balance": {"payouts": _cap(caps.get("payouts", "pending"))},
            }},
            "recipient": {"capabilities": {
                "stripe_balance": {"stripe_transfers": _cap(caps.get("transfers", "pending"))},
            }},
        },
        "requirements": {"entries": caps.get("entries", [])},
    }


def test_gateway_v2_create_account_posts_v2_shape(monkeypatch):
    from app import connect_gateway
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_p1b_v2")
    seen = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return _FakeResp(_v2_account_payload())

    monkeypatch.setattr(httpx, "post", fake_post)
    out = connect_gateway.create_account("shop@example.com", country="HK")
    assert seen["url"] == "https://api.stripe.com/v2/core/accounts"
    headers = seen["kwargs"]["headers"]
    assert headers["Authorization"] == "Bearer sk_test_p1b_v2"
    assert headers["Stripe-Version"] == "2026-11-18.preview"
    body = seen["kwargs"]["json"]
    assert body["contact_email"] == "shop@example.com"
    assert body["dashboard"] == "express"
    assert body["identity"]["country"] == "hk"
    merchant = body["configuration"]["merchant"]["capabilities"]
    assert merchant["card_payments"]["requested"] is True
    assert merchant["stripe_balance"]["payouts"]["requested"] is True
    recipient = body["configuration"]["recipient"]["capabilities"]
    assert recipient["stripe_balance"]["stripe_transfers"]["requested"] is True
    assert body["defaults"]["currency"] == "hkd"
    assert body["defaults"]["responsibilities"] == {
        "fees_collector": "application", "losses_collector": "application"}
    assert "configuration.merchant" in body["include"]
    assert "configuration.recipient" in body["include"]
    assert "requirements" in body["include"]
    assert out["account_id"] == "acct_v2_test_123"
    assert out["simulated"] is False
    # Capabilities still pending -> not ready.
    assert out["charges_enabled"] is False
    assert out["payouts_enabled"] is False


def test_gateway_v2_create_account_link_posts_use_case(monkeypatch):
    from app import connect_gateway
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_p1b_v2")
    seen = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return _FakeResp({
            "object": "v2.core.account_link",
            "account": "acct_v2_test_123",
            "url": "https://accounts.stripe.com/r/acct_v2_test_123#alu_test_abc",
            "expires_at": "2026-11-20T16:25:18.000Z"})

    monkeypatch.setattr(httpx, "post", fake_post)
    out = connect_gateway.create_account_link(
        "acct_v2_test_123", "coupon-go://connect/refresh",
        "coupon-go://connect/return")
    assert seen["url"] == "https://api.stripe.com/v2/core/account_links"
    assert seen["kwargs"]["headers"]["Stripe-Version"] == "2026-11-18.preview"
    body = seen["kwargs"]["json"]
    assert body["account"] == "acct_v2_test_123"
    assert body["use_case"]["type"] == "account_onboarding"
    onboarding = body["use_case"]["account_onboarding"]
    assert onboarding["return_url"] == "coupon-go://connect/return"
    assert onboarding["refresh_url"] == "coupon-go://connect/refresh"
    assert set(onboarding["configurations"]) == {"merchant", "recipient"}
    assert out["url"].startswith("https://accounts.stripe.com/r/")
    assert out["expires_at"] == "2026-11-20T16:25:18.000Z"
    assert out["simulated"] is False


def test_gateway_v2_retrieve_maps_capability_status(monkeypatch):
    from app import connect_gateway
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_p1b_v2")
    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return _FakeResp(_v2_account_payload(
            card="active", payouts="restricted", transfers="active",
            entries=[{
                "description": "business_profile.product_description",
                "awaiting_action_from": "user",
                "minimum_deadline": {"status": "currently_due"},
            }]))

    monkeypatch.setattr(httpx, "get", fake_get)
    out = connect_gateway.retrieve_account("acct_v2_test_123")
    assert seen["url"] == "https://api.stripe.com/v2/core/accounts/acct_v2_test_123"
    assert seen["kwargs"]["headers"]["Stripe-Version"] == "2026-11-18.preview"
    assert out["charges_enabled"] is True   # card_payments active
    assert out["payouts_enabled"] is False  # payouts restricted
    assert out["details_submitted"] is False  # one requirement currently due
    assert out["requirements"] == ["business_profile.product_description"]
    # All active + nothing due -> fully ready.
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _FakeResp(
        _v2_account_payload(card="active", payouts="active",
                            transfers="active")))
    out = connect_gateway.retrieve_account("acct_v2_test_123")
    assert out["charges_enabled"] is True
    assert out["payouts_enabled"] is True
    assert out["details_submitted"] is True
    assert out["requirements"] == []


def test_gateway_v2_transfers_and_payouts_stay_v1(monkeypatch):
    from app import connect_gateway
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_p1b_v2")
    seen = []

    def fake_post(url, **kwargs):
        seen.append(url)
        if url.endswith("/transfers"):
            return _FakeResp({"id": "tr_v2_1", "status": "paid",
                              "amount": 13500, "currency": "hkd"})
        return _FakeResp({"id": "po_v2_1", "status": "paid",
                          "amount": 10000, "currency": "hkd"})

    monkeypatch.setattr(httpx, "post", fake_post)
    tr = connect_gateway.create_transfer("acct_v2_test_123", 135.00,
                                         "coupon-go-redemption-1")
    po = connect_gateway.create_payout("acct_v2_test_123", 100.00)
    assert tr["transfer_id"] == "tr_v2_1"
    assert po["payout_id"] == "po_v2_1"
    # v1 money endpoints keep the v1 base and carry NO preview header:
    # a v2 account id is passed to them as the destination / Stripe-Account.
    assert seen == ["https://api.stripe.com/v1/transfers",
                    "https://api.stripe.com/v1/payouts"]
