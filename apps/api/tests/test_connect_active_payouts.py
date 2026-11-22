"""Read unfinished payouts for cross-device recovery without changing money."""
from decimal import Decimal

import pytest

from app import config, connect_gateway, models
from app.models_connect import ConnectLedgerEntry
from app.payments_gateway import GatewayError
from tests import test_p1b_connect_fixes as base

client = base.client


@pytest.fixture(scope="module", autouse=True)
def isolated_simulator():
    connect_gateway._reset_simulator_idempotency()
    yield
    connect_gateway._reset_simulator_idempotency()


def test_active_endpoint_returns_all_unresolved_and_only_owned_shop(client):
    with base._db() as db:
        shop = db.get(models.Shop, 2)
        owner = db.get(models.User, shop.vendor_id)
        other = models.User(email="different-vendor@example.com", password_hash=owner.password_hash, role="vendor")
        db.add(other)
        db.flush()
        other_shop = models.Shop(vendor_id=other.id, name="Other shop")
        db.add(other_shop)
        db.flush()
        other_shop_id = other_shop.id
        for status, request_id, amount in [
            ("processing", "request-processing-001", "2.50"),
            ("payout_unknown", "request-unknown-00001", "5.25"),
            ("submitted", "request-submitted-0001", "8.75"),
            ("payout_unknown", None, "4.00"),
        ]:
            db.add(ConnectLedgerEntry(shop_id=shop.id, kind="payout", status=status,
                                      net_amount=-Decimal(amount), payout_request_id=request_id))
        # Unresolved entries must still be discoverable beyond a ledger page.
        for index in range(30):
            db.add(ConnectLedgerEntry(shop_id=shop.id, kind="payout", status="paid_out",
                                      net_amount=Decimal("-1"), payout_request_id=f"done-{index:020d}"))
        db.add(ConnectLedgerEntry(shop_id=other_shop_id, kind="payout", status="payout_unknown",
                                  net_amount=Decimal("-9"), payout_request_id="other-shop-request-001"))
        db.commit()
    vendor = base._vendor_headers(client)
    out = client.get("/api/v3/merchant/connect/payouts/active", params={"shop_id": 2}, headers=vendor)
    assert out.status_code == 200, out.text
    data = out.json()
    assert data["shop_id"] == 2 and data["currency"] == "HKD"
    assert {(row["request_id"], row["amount"], row["status"]) for row in data["payouts"]} == {
        ("request-processing-001", 2.5, "processing"), ("request-unknown-00001", 5.25, "payout_unknown"),
        ("request-submitted-0001", 8.75, "submitted"), (None, 4.0, "payout_unknown")}
    assert client.get("/api/v3/merchant/connect/payouts/active",
                      params={"shop_id": other_shop_id}, headers=vendor).status_code == 403
    with base._db() as db:
        assert db.query(ConnectLedgerEntry).filter_by(shop_id=2, kind="payout").count() == 34
        assert db.query(ConnectLedgerEntry).filter_by(shop_id=2, status="payout_unknown").count() == 2


def test_active_payout_reuses_request_after_unknown_result(client, monkeypatch):
    base._onboard_sync(client, 1)
    base._scan_package(client, "active-payout@example.com", 1)
    base._sweep()
    calls = []
    def uncertain(*args, **kwargs):
        calls.append(kwargs["idempotency_key"])
        raise GatewayError("provider response lost")
    monkeypatch.setattr(connect_gateway, "create_payout", uncertain)
    vendor = base._vendor_headers(client)
    request = {"shop_id": 1, "amount": 20, "request_id": "cross-device-request-001"}
    assert client.post("/api/v3/merchant/connect/payouts", json=request, headers=vendor).status_code == 502
    active = client.get("/api/v3/merchant/connect/payouts/active", params={"shop_id": 1}, headers=vendor)
    assert active.status_code == 200
    row = active.json()["payouts"][0]
    assert (row["request_id"], row["amount"], row["status"]) == (request["request_id"], 20.0, "payout_unknown")
    replay = client.post("/api/v3/merchant/connect/payouts", json=request, headers=vendor)
    assert replay.status_code == 200 and replay.json()["status"] == "payout_unknown"
    assert replay.json()["connect_mode"] == "simulated"
    assert len(calls) == 1


def test_current_connect_mode_is_visible_without_provider_calls(client, monkeypatch):
    vendor = base._vendor_headers(client)
    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED_RAW", "1")
    def assert_mode(expected):
        for path in ("/api/v3/merchant/connect/balance", "/api/v3/merchant/connect/payouts/active"):
            response = client.get(path, params={"shop_id": 1}, headers=vendor)
            assert response.status_code == 200 and response.json()["connect_mode"] == expected
    monkeypatch.setattr(config, "APP_ENV", "dev")
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "")
    assert_mode("simulated")
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_example")
    assert_mode("stripe_test")
    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED_RAW", "0")
    assert_mode("unavailable")
