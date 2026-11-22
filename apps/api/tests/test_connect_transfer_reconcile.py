"""Provider-confirmed transfer/reversal recovery using an isolated simulator."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from fastapi import HTTPException

from tests import test_p1b_connect_fixes as base
from app import application_connect, config, connect_gateway, connect_service
from app.models_connect import ConnectLedgerEntry
from app.models_line_b import AdminAuditLog
from app.payments_gateway import GatewayError
from app.timeutil import utcnow

client = base.client


@pytest.fixture(scope="module", autouse=True)
def isolated_simulator():
    connect_gateway._reset_simulator_idempotency()
    yield
    connect_gateway._reset_simulator_idempotency()


def test_simulator_rejects_same_transfer_operation_with_different_parameters(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "")
    first = connect_gateway.create_transfer("acct_sim_x", Decimal("10"), "group-x", idempotency_key="unique-mismatch-test")
    assert connect_gateway.create_transfer("acct_sim_x", Decimal("10.00"), "group-x", idempotency_key="unique-mismatch-test") == first
    with pytest.raises(GatewayError):
        connect_gateway.create_transfer("acct_sim_x", Decimal("20"), "group-x", idempotency_key="unique-mismatch-test")
    with pytest.raises(GatewayError):
        connect_gateway.create_transfer("acct_sim_y", Decimal("10"), "group-x", idempotency_key="unique-mismatch-test")


def test_non_development_missing_key_never_marks_simulated_transfer_complete(client, monkeypatch):
    base._onboard_sync(client, 11)
    scan, _ = base._scan_package(client, "reconcile-no-key@example.com", 11)
    entry_id = scan["connect"]["entry_id"]
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED_RAW", "1")
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "")
    assert not connect_service.connect_configured()
    with pytest.raises(HTTPException) as exc:
        application_connect._enabled()
    assert exc.value.status_code == 503
    base._sweep()
    row = base._entry(entry_id)
    assert row["status"] == "pending" and not row["stripe_transfer_id"]
    with pytest.raises(GatewayError):
        connect_gateway.create_transfer("acct_test", Decimal("10"), "group", idempotency_key="no-key-production")
    # Leave no unrelated pending work for the following simulated sweep.
    with base._db() as db:
        db.get(ConnectLedgerEntry, entry_id).status = "reversed"
        db.commit()


def test_non_test_connect_key_is_rejected_before_dispatch(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "dev")
    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED_RAW", "1")
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "not_a_test_key")
    assert not connect_service.connect_configured()
    with pytest.raises(HTTPException) as exc:
        application_connect._enabled()
    assert exc.value.status_code == 503


def _uncertain_transfer(client, monkeypatch, name, shop_id):
    base._onboard_sync(client, shop_id)
    scan, _ = base._scan_package(client, f"reconcile-{name}@example.com", shop_id)
    entry_id = scan["connect"]["entry_id"]
    created = []
    original = connect_gateway.create_transfer
    def succeeded_but_lost(*args, **kwargs):
        obj = original(*args, **kwargs)
        created.append(obj["transfer_id"])
        raise GatewayError("response lost")
    monkeypatch.setattr(connect_gateway, "create_transfer", succeeded_but_lost)
    base._sweep()
    monkeypatch.setattr(connect_gateway, "create_transfer", original)
    assert base._entry(entry_id)["status"] == "transfer_unknown"
    assert len(created) == 1
    return entry_id, scan["redemption_id"], created[0]


def test_unknown_transfer_reconciles_once_then_dispatches_requested_reversal(client, monkeypatch):
    entry_id, redemption_id, provider_id = _uncertain_transfer(client, monkeypatch, "full", 2)
    vendor, admin = base._vendor_headers(client), base._admin_headers(client)
    assert client.post(f"/api/v3/redemptions/{redemption_id}/revoke", headers=vendor).status_code == 200
    url = f"/api/v3/admin/connect/transfers/{entry_id}/reconcile"
    result = client.post(url, json={"provider_id": provider_id}, headers=admin)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "transferred" and result.json()["pending_reversal"]
    again = client.post(url, json={"provider_id": provider_id}, headers=admin)
    assert again.status_code == 200 and again.json()["duplicate"]
    base._sweep()
    row = base._entry(entry_id)
    assert row["status"] == "reversed" and row["stripe_transfer_id"] == provider_id


@pytest.mark.parametrize("field,value", [
    ("destination", "acct_wrong"), ("amount", 1), ("currency", "usd"),
    ("transfer_group", "wrong"), ("metadata", {}), ("livemode", True),
    ("amount_reversed", None), ("amount_reversed", False), ("reversed", True),
])
def test_transfer_mismatch_preserves_unknown(client, monkeypatch, field, value):
    suffix = "bad-" + field + ("-false" if value is False else "")
    entry_id, _, provider_id = _uncertain_transfer(client, monkeypatch, suffix, 3)
    original = connect_gateway.retrieve_transfer
    monkeypatch.setattr(connect_gateway, "retrieve_transfer", lambda pid: {**original(pid), field: value})
    result = client.post(f"/api/v3/admin/connect/transfers/{entry_id}/reconcile",
                         json={"provider_id": provider_id}, headers=base._admin_headers(client))
    assert result.status_code == 409
    assert base._entry(entry_id)["status"] == "transfer_unknown"
    with base._db() as db:
        assert db.query(AdminAuditLog).filter_by(target_id=entry_id, action="connect.reconcile.incomplete",
                                                 detail="transfer_evidence_mismatch").count() >= 1


def test_interrupted_transfer_requires_stale_marker_before_lookup(client, monkeypatch):
    entry_id, _, provider_id = _uncertain_transfer(client, monkeypatch, "interrupted", 9)
    url = f"/api/v3/admin/connect/transfers/{entry_id}/reconcile"
    admin = base._admin_headers(client)
    with base._db() as db:
        entry = db.get(ConnectLedgerEntry, entry_id)
        entry.status, entry.updated_at = "transferring", utcnow()
        db.commit()
    assert client.post(url, json={"provider_id": provider_id}, headers=admin).status_code == 409
    with base._db() as db:
        db.get(ConnectLedgerEntry, entry_id).updated_at = utcnow() - timedelta(minutes=3)
        db.commit()
    assert client.post(url, json={"provider_id": provider_id}, headers=admin).status_code == 200


def test_legacy_unknown_without_original_account_cannot_be_guessed(client, monkeypatch):
    entry_id, _, provider_id = _uncertain_transfer(client, monkeypatch, "legacy", 4)
    with base._db() as db:
        db.get(ConnectLedgerEntry, entry_id).transfer_account_id = None
        db.commit()
    result = client.post(f"/api/v3/admin/connect/transfers/{entry_id}/reconcile",
                         json={"provider_id": provider_id}, headers=base._admin_headers(client))
    assert result.status_code == 409 and "缺少原帳戶" in result.json()["detail"]
    assert base._entry(entry_id)["status"] == "transfer_unknown"


def _uncertain_reversal(client, monkeypatch, suffix, shop_id):
    base._onboard_sync(client, shop_id)
    scan, _ = base._scan_package(client, f"reconcile-reversal-{suffix}@example.com", shop_id)
    entry_id, redemption_id = scan["connect"]["entry_id"], scan["redemption_id"]
    base._sweep()
    vendor = base._vendor_headers(client)
    assert client.post(f"/api/v3/redemptions/{redemption_id}/revoke", headers=vendor).status_code == 200
    original = connect_gateway.reverse_transfer
    created = []
    def succeeded_but_lost(*args, **kwargs):
        obj = original(*args, **kwargs)
        created.append(obj["reversal_id"])
        raise GatewayError("response lost")
    monkeypatch.setattr(connect_gateway, "reverse_transfer", succeeded_but_lost)
    base._sweep()
    monkeypatch.setattr(connect_gateway, "reverse_transfer", original)
    assert base._entry(entry_id)["note"] == "reversal_result_unknown"
    return entry_id, created[0]


def test_unknown_reversal_requires_matching_transfer_and_reversal(client, monkeypatch):
    entry_id, reversal_id = _uncertain_reversal(client, monkeypatch, "full", 5)
    admin = base._admin_headers(client)
    url = f"/api/v3/admin/connect/transfers/{entry_id}/reversal/reconcile"
    assert client.post(url, json={"provider_id": "trr_unknown"}, headers=admin).status_code == 502
    result = client.post(url, json={"provider_id": reversal_id}, headers=admin)
    assert result.status_code == 200 and result.json()["status"] == "reversed", result.text
    assert client.post(url, json={"provider_id": reversal_id}, headers=admin).json()["duplicate"]
    assert base._entry(entry_id)["pending_reversal"] is False


@pytest.mark.parametrize("field,value", [
    ("transfer", "tr_wrong"), ("amount", 1), ("currency", "usd"), ("metadata", {}),
])
def test_reversal_mismatch_keeps_original_amount_reserved(client, monkeypatch, field, value):
    entry_id, reversal_id = _uncertain_reversal(client, monkeypatch, "bad-" + field, 7)
    original = connect_gateway.retrieve_reversal
    monkeypatch.setattr(connect_gateway, "retrieve_reversal", lambda tid, rid: {**original(tid, rid), field: value})
    result = client.post(f"/api/v3/admin/connect/transfers/{entry_id}/reversal/reconcile",
                         json={"provider_id": reversal_id}, headers=base._admin_headers(client))
    assert result.status_code == 409
    row = base._entry(entry_id)
    assert row["status"] == "transferred" and row["pending_reversal"]


def test_two_admins_reconciling_same_reversal_are_idempotent(client, monkeypatch):
    entry_id, reversal_id = _uncertain_reversal(client, monkeypatch, "concurrent", 8)
    admin = base._admin_headers(client)
    barrier = Barrier(2)
    def request():
        barrier.wait(timeout=10)
        return client.post(f"/api/v3/admin/connect/transfers/{entry_id}/reversal/reconcile",
                           json={"provider_id": reversal_id}, headers=admin)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(request), pool.submit(request)
        results = [first.result(timeout=20), second.result(timeout=20)]
    assert [r.status_code for r in results] == [200, 200]
    assert sorted(r.json()["duplicate"] for r in results) == [False, True]


def test_interrupted_reversal_requires_stale_marker_before_lookup(client, monkeypatch):
    entry_id, reversal_id = _uncertain_reversal(client, monkeypatch, "interrupted", 10)
    url = f"/api/v3/admin/connect/transfers/{entry_id}/reversal/reconcile"
    admin = base._admin_headers(client)
    with base._db() as db:
        entry = db.get(ConnectLedgerEntry, entry_id)
        entry.note, entry.updated_at = "reversal_dispatch_started", utcnow()
        db.commit()
    assert client.post(url, json={"provider_id": reversal_id}, headers=admin).status_code == 409
    with base._db() as db:
        db.get(ConnectLedgerEntry, entry_id).updated_at = utcnow() - timedelta(minutes=3)
        db.commit()
    assert client.post(url, json={"provider_id": reversal_id}, headers=admin).status_code == 200


def test_two_admins_reconciling_same_transfer_are_idempotent(client, monkeypatch):
    entry_id, _, provider_id = _uncertain_transfer(client, monkeypatch, "concurrent", 6)
    admin = base._admin_headers(client)
    barrier = Barrier(2)
    def request():
        barrier.wait(timeout=10)
        return client.post(f"/api/v3/admin/connect/transfers/{entry_id}/reconcile",
                           json={"provider_id": provider_id}, headers=admin)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(request), pool.submit(request)
        results = [first.result(timeout=20), second.result(timeout=20)]
    assert [r.status_code for r in results] == [200, 200]
    assert sorted(r.json()["duplicate"] for r in results) == [False, True]
