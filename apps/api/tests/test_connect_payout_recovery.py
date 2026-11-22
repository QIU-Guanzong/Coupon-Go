"""Payout recovery against isolated databases and a stub provider only."""
import os
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import config, connect_gateway, connect_service, models
from app.database import Base
from app.models_connect import ConnectAccount, ConnectLedgerEntry


def test_legacy_account_is_not_presented_as_ready_until_transfer_capability_verified():
    account = ConnectAccount(shop_id=1, stripe_account_id="acct_old", charges_enabled=True,
                             payouts_enabled=True, transfers_enabled=None, onboarding_status="active")
    out = connect_service.account_out(None, account, 1)
    assert out["onboarding_status"] == "incomplete" and out["transfers_enabled"] is False


@pytest.fixture(scope="module")
def factory(tmp_path_factory):
    url = os.getenv("PAYOUT_TEST_DATABASE_URL")
    if url:
        engine = create_engine(url, pool_size=8, max_overflow=4)
    else:
        path = tmp_path_factory.mktemp("connect-payout") / "test.db"
        engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest.fixture
def shop_id(factory, monkeypatch):
    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED_RAW", "1")
    monkeypatch.setattr(config, "APP_ENV", "dev")
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "")
    with factory() as db:
        vendor = models.User(email=f"{uuid4().hex}@example.invalid", password_hash="none", role="vendor")
        admin = models.User(email=f"{uuid4().hex}@example.invalid", password_hash="none", role="admin")
        db.add_all([vendor, admin]); db.flush()
        shop = models.Shop(vendor_id=vendor.id, name="Test shop"); db.add(shop); db.flush()
        db.add(ConnectAccount(shop_id=shop.id, stripe_account_id="acct_test_payout", charges_enabled=True,
                              payouts_enabled=True, details_submitted=True, manual_payouts=True))
        db.add(ConnectLedgerEntry(shop_id=shop.id, kind="transfer", status="transferred",
                                  gross_amount=100, commission=0, net_amount=100,
                                  stripe_transfer_id="tr_test_prior", dispatch_started=True))
        db.commit()
        return shop.id, admin.id


def _submit(factory, shop_id, amount, request_id):
    with factory() as db:
        return connect_service.request_payout(db, db.get(models.Shop, shop_id), Decimal(amount), request_id)


def test_timeout_holds_balance_and_same_request_never_resends(factory, shop_id, monkeypatch):
    sid, _ = shop_id
    calls = []
    def provider(account, amount, *, idempotency_key):
        calls.append(idempotency_key)
        raise TimeoutError("provider may have already paid")
    monkeypatch.setattr(connect_gateway, "create_payout", provider)
    with pytest.raises(HTTPException) as exc:
        _submit(factory, sid, "60", "payout-request-00001")
    assert exc.value.status_code == 502
    replay = _submit(factory, sid, "60", "payout-request-00001")
    assert replay["status"] == "payout_unknown" and replay["balance"]["available"] == 40
    assert replay["balance"]["processing"] == 60
    with pytest.raises(HTTPException) as blocked:
        _submit(factory, sid, "20", "payout-request-00002")
    assert blocked.value.status_code == 409 and len(calls) == 1


def test_unknown_payout_requires_matching_provider_proof(factory, shop_id, monkeypatch):
    sid, admin_id = shop_id
    monkeypatch.setattr(connect_gateway, "create_payout", lambda *a, **kw: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(HTTPException):
        _submit(factory, sid, "60", "payout-request-00003")
    with factory() as db:
        entry = db.query(ConnectLedgerEntry).filter_by(shop_id=sid, kind="payout").one()
        entry_id, operation = entry.id, entry.payout_operation_id
    def provider(account, payout_id):
        assert account == "acct_test_payout"
        return {"id": payout_id, "amount": 6000, "currency": "hkd", "status": "paid",
                "metadata": {"connect_operation_key": f"payout-{operation}"}}
    monkeypatch.setattr(connect_gateway, "retrieve_payout", lambda account, payout_id: {**provider(account, payout_id), "amount": 5000})
    with factory() as db:
        with pytest.raises(HTTPException) as mismatch:
            connect_service.reconcile_payout(db, entry_id, "po_test_found", admin_id)
        assert mismatch.value.status_code == 409
        assert connect_service.merchant_balance(db, sid)["available"] == Decimal("40.00")
    monkeypatch.setattr(connect_gateway, "retrieve_payout", provider)
    with factory() as db:
        result = connect_service.reconcile_payout(db, entry_id, "po_test_found", admin_id)
        assert result["status"] == "paid_out"
        assert result["balance"]["available"] == 40 and result["balance"]["paid_out"] == 60


@pytest.mark.parametrize("provider_status", ["pending", "in_transit"])
def test_submitted_payout_keeps_hold_until_authoritative_completion(factory, shop_id, monkeypatch, provider_status):
    sid, admin_id = shop_id
    monkeypatch.setattr(connect_gateway, "create_payout", lambda account, amount, **kw:
                        {"payout_id": "po_test_pending", "amount": 60, "currency": "HKD", "status": provider_status})
    result = _submit(factory, sid, "60", f"pending-{provider_status}-12345")
    assert result["status"] == "submitted" and result["balance"]["processing"] == 60
    with factory() as db:
        entry = db.get(ConnectLedgerEntry, result["entry_id"])
        operation = entry.payout_operation_id
    monkeypatch.setattr(connect_gateway, "retrieve_payout", lambda account, payout_id:
        {"id": payout_id, "amount": 6000, "currency": "HKD", "status": "failed",
         "metadata": {"connect_operation_key": f"payout-{operation}"}})
    with factory() as db:
        result = connect_service.reconcile_payout(db, result["entry_id"], "po_test_pending", admin_id)
        assert result["status"] == "failed" and result["balance"]["available"] == 100


def test_gateway_success_then_db_commit_failure_keeps_reservation(factory, shop_id, monkeypatch):
    sid, admin_id = shop_id
    calls, raised = [], []
    def provider(account, amount, *, idempotency_key):
        calls.append(idempotency_key)
        return {"payout_id": "po_test_commit", "amount": 60, "currency": "HKD", "status": "paid"}
    original_commit = Session.commit
    def fail_once(db):
        if not raised and any(isinstance(row, ConnectLedgerEntry) and row.kind == "payout" and row.status == "paid_out"
                              for row in db.identity_map.values()):
            raised.append(True)
            raise RuntimeError("simulated database result commit failure")
        return original_commit(db)
    monkeypatch.setattr(connect_gateway, "create_payout", provider)
    monkeypatch.setattr(Session, "commit", fail_once)
    with pytest.raises(RuntimeError):
        _submit(factory, sid, "60", "payout-request-00004")
    replay = _submit(factory, sid, "60", "payout-request-00004")
    assert replay["status"] == "processing" and replay["balance"]["processing"] == 60
    with pytest.raises(HTTPException):
        _submit(factory, sid, "20", "payout-request-00005")
    assert len(calls) == 1
    with factory() as db:
        entry = db.get(ConnectLedgerEntry, replay["entry_id"])
        operation = entry.payout_operation_id
        entry.payout_started_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=3)
        db.commit()
    monkeypatch.setattr(connect_gateway, "retrieve_payout", lambda account, payout_id:
        {"id": payout_id, "amount": 6000, "currency": "HKD", "status": "paid",
         "metadata": {"connect_operation_key": f"payout-{operation}"}})
    with factory() as db:
        result = connect_service.reconcile_payout(db, replay["entry_id"], "po_test_commit", admin_id)
        assert result["status"] == "paid_out" and result["balance"]["available"] == 40


def test_concurrent_payouts_cannot_spend_same_balance(factory, shop_id, monkeypatch):
    sid, _ = shop_id
    started, release = threading.Event(), threading.Event()
    calls, result, errors = [], [], []
    def provider(account, amount, *, idempotency_key):
        calls.append(idempotency_key); started.set()
        assert release.wait(10)
        return {"payout_id": "po_test_concurrent", "amount": 80, "currency": "HKD", "status": "pending"}
    def first():
        try:
            result.append(_submit(factory, sid, "80", "payout-request-00006"))
        except BaseException as exc:
            errors.append(exc)
    monkeypatch.setattr(connect_gateway, "create_payout", provider)
    thread = threading.Thread(target=first); thread.start()
    try:
        assert started.wait(10)
        replay = _submit(factory, sid, "80", "payout-request-00006")
        assert replay["status"] == "processing" and replay["balance"]["processing"] == 80
        with pytest.raises(HTTPException) as changed:
            _submit(factory, sid, "79", "payout-request-00006")
        assert changed.value.status_code == 409
        with pytest.raises(HTTPException) as blocked:
            _submit(factory, sid, "80", "payout-request-00007")
        assert blocked.value.status_code == 409
    finally:
        release.set(); thread.join(10)
    assert not thread.is_alive() and not errors and len(calls) == 1
    assert result[0]["status"] == "submitted" and result[0]["balance"]["available"] == 20


def test_late_authoritative_payout_failure_releases_balance_once(factory, shop_id, monkeypatch):
    sid, admin_id = shop_id
    monkeypatch.setattr(connect_gateway, "create_payout", lambda account, amount, **kw:
                        {"payout_id": "po_test_latefail", "amount": 60, "currency": "HKD", "status": "paid"})
    result = _submit(factory, sid, "60", "payout-request-00008")
    assert result["status"] == "paid_out" and result["balance"]["available"] == 40
    with factory() as db:
        operation = db.get(ConnectLedgerEntry, result["entry_id"]).payout_operation_id
    monkeypatch.setattr(connect_gateway, "retrieve_payout", lambda account, payout_id:
        {"id": payout_id, "amount": 6000, "currency": "HKD", "status": "failed",
         "metadata": {"connect_operation_key": f"payout-{operation}"}})
    with factory() as db:
        updated = connect_service.reconcile_payout(db, result["entry_id"], "po_test_latefail", admin_id)
        assert updated["status"] == "failed" and updated["balance"]["available"] == 100
        assert db.query(ConnectLedgerEntry).filter_by(shop_id=sid, kind="payout").count() == 1


def test_scheduler_checks_submitted_payout_without_sending_again(factory, shop_id, monkeypatch):
    sid, _ = shop_id
    calls = []
    def send(account, amount, *, idempotency_key):
        calls.append(idempotency_key)
        return {"payout_id": "po_test_poll", "amount": 60, "currency": "HKD", "status": "pending"}
    monkeypatch.setattr(connect_gateway, "create_payout", send)
    payout = _submit(factory, sid, "60", "payout-poll-0000001")
    with factory() as db:
        operation = db.get(ConnectLedgerEntry, payout["entry_id"]).payout_operation_id
    states = iter(("pending", "paid"))
    def retrieve(account, payout_id):
        if payout_id != "po_test_poll":
            raise connect_service.GatewayError("another test payout is unavailable")
        return {"id": payout_id, "amount": 6000, "currency": "HKD", "status": next(states),
                "metadata": {"connect_operation_key": f"payout-{operation}"}}
    monkeypatch.setattr(connect_gateway, "retrieve_payout", retrieve)
    with factory() as db:
        assert connect_service.sweep_pending_transfers(db)["payouts_checked"] == 1
        assert connect_service.merchant_balance(db, sid)["processing"] == Decimal("60.00")
    with factory() as db:
        assert connect_service.sweep_pending_transfers(db)["payouts_checked"] == 1
        balance = connect_service.merchant_balance(db, sid)
        assert balance["processing"] == 0 and balance["paid_out"] == Decimal("60.00")
    assert len(calls) == 1


def test_scheduler_detects_failure_after_paid_without_resending(factory, shop_id, monkeypatch):
    sid, _ = shop_id
    calls = []
    def send(account, amount, *, idempotency_key):
        calls.append(idempotency_key)
        return {"payout_id": "po_test_late_failure", "amount": 60, "currency": "HKD", "status": "paid"}
    monkeypatch.setattr(connect_gateway, "create_payout", send)
    payout = _submit(factory, sid, "60", "payout-late-auto-001")
    with factory() as db:
        entry = db.get(ConnectLedgerEntry, payout["entry_id"])
        operation = entry.payout_operation_id
        entry.payout_last_polled_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        db.commit()
    monkeypatch.setattr(connect_gateway, "retrieve_payout", lambda account, payout_id: {
        "id": payout_id, "amount": 6000, "currency": "HKD", "status": "failed",
        "metadata": {"connect_operation_key": f"payout-{operation}"}})
    with factory() as db:
        assert connect_service.sweep_pending_transfers(db)["payouts_checked"] == 1
        balance = connect_service.merchant_balance(db, sid)
        assert balance["paid_out"] == 0 and balance["available"] == Decimal("100.00")
        assert db.get(ConnectLedgerEntry, payout["entry_id"]).status == "failed"
        assert db.query(ConnectLedgerEntry).filter_by(shop_id=sid, kind="payout").count() == 1
    with factory() as db:
        connect_service.sweep_pending_transfers(db)
        assert connect_service.merchant_balance(db, sid)["available"] == Decimal("100.00")
    assert len(calls) == 1


def test_unverifiable_old_payout_does_not_starve_another_shop(factory, shop_id, monkeypatch):
    sid, _ = shop_id
    with factory() as db:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        db.query(ConnectLedgerEntry).filter_by(kind="payout", status="submitted").update(
            {"payout_last_polled_at": now}, synchronize_session=False)
        merchant = db.get(models.Shop, sid)
        old_shop = models.Shop(vendor_id=merchant.vendor_id, name="legacy shop")
        db.add(old_shop); db.flush()
        db.add(ConnectLedgerEntry(shop_id=old_shop.id, kind="payout", status="submitted",
                                  gross_amount=0, commission=0, net_amount=-5,
                                  stripe_payout_id="po_legacy"))
        db.commit()
    monkeypatch.setattr(connect_gateway, "create_payout", lambda account, amount, **kw:
                        {"payout_id": "po_test_ready", "amount": 60, "currency": "HKD", "status": "pending"})
    entry = _submit(factory, sid, "60", "payout-ready-0000001")
    with factory() as db:
        operation = db.get(ConnectLedgerEntry, entry["entry_id"]).payout_operation_id
    monkeypatch.setattr(connect_gateway, "retrieve_payout", lambda account, payout_id:
        {"id": payout_id, "amount": 6000, "currency": "HKD", "status": "paid",
         "metadata": {"connect_operation_key": f"payout-{operation}"}})
    with factory() as db:
        first = connect_service.sweep_pending_transfers(db, batch_size=1)
        assert first["failed"] == 1 and first["payouts_checked"] == 0
    with factory() as db:
        second = connect_service.sweep_pending_transfers(db, batch_size=1)
        assert second["payouts_checked"] == 1
        assert db.get(ConnectLedgerEntry, entry["entry_id"]).status == "paid_out"


def test_disabled_connect_never_polls_submitted_payout(factory, shop_id, monkeypatch):
    sid, admin_id = shop_id
    monkeypatch.setattr(connect_gateway, "create_payout", lambda account, amount, **kw:
                        {"payout_id": "po_test_disabled", "amount": 60, "currency": "HKD", "status": "pending"})
    entry = _submit(factory, sid, "60", "payout-disabled-00001")
    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED_RAW", "0")
    monkeypatch.setattr(connect_gateway, "retrieve_payout", lambda *a, **kw: pytest.fail("disabled Connect called provider"))
    with factory() as db:
        assert connect_service.sweep_pending_transfers(db)["payouts_checked"] == 0
        with pytest.raises(HTTPException) as disabled:
            connect_service.reconcile_payout(db, entry["entry_id"], "po_test_disabled", admin_id)
        assert disabled.value.status_code == 503
        assert connect_service.merchant_balance(db, sid)["processing"] == Decimal("60.00")


@pytest.mark.parametrize("field,value", [("currency", "USD"), ("metadata", {}), ("id", "po_other")])
def test_reconciliation_rejects_wrong_provider_details(factory, shop_id, monkeypatch, field, value):
    sid, admin_id = shop_id
    monkeypatch.setattr(connect_gateway, "create_payout", lambda *a, **kw: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(HTTPException):
        _submit(factory, sid, "60", f"payout-bad-{field}-123456")
    with factory() as db:
        entry = db.query(ConnectLedgerEntry).filter_by(shop_id=sid, kind="payout").one()
        entry_id, operation = entry.id, entry.payout_operation_id
    def provider(account, payout_id):
        obj = {"id": payout_id, "amount": 6000, "currency": "HKD", "status": "paid",
               "metadata": {"connect_operation_key": f"payout-{operation}"}}
        obj[field] = value
        return obj
    monkeypatch.setattr(connect_gateway, "retrieve_payout", provider)
    with factory() as db:
        with pytest.raises(HTTPException) as mismatch:
            connect_service.reconcile_payout(db, entry_id, "po_test_mismatch", admin_id)
        assert mismatch.value.status_code == 409
        assert connect_service.merchant_balance(db, sid)["available"] == Decimal("40.00")
