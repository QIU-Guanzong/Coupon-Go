"""A Connect reversal is dispatched only after its durable business intent."""
import threading
from datetime import datetime, timedelta
import pytest
from fastapi import HTTPException
from tests import test_p1b_connect_fixes as base
from tests.test_p1b_connect_fixes import client
from app import connect_gateway, connect_service, models
from app.models_connect import ConnectLedgerEntry


def transferred(client, suffix, shop=1):
    base._onboard_sync(client, shop_id=shop)
    scan, paid = base._scan_package(client, f'reversal-{suffix}@example.com', shop_id=shop)
    assert base._sweep()['transferred'] >= 1
    return base._vendor_headers(client), scan, paid


def test_rollback_before_business_commit_never_calls_provider(client, monkeypatch):
    vendor, scan, paid = transferred(client, 'rollback', 1)
    calls = []
    monkeypatch.setattr(connect_gateway, 'reverse_transfer', lambda *a, **k: calls.append(k))
    with base._db() as db:
        red = db.get(models.Redemption, scan['redemption_id'])
        red.revoked_at = datetime.utcnow()
        assert connect_service.reverse_redemption(db, red, 'revoked')['reason'] == 'reversal_queued'
        db.rollback()
    base._sweep()
    assert calls == []
    with base._db() as db:
        assert db.get(models.Redemption, scan['redemption_id']).revoked_at is None
        assert not db.get(ConnectLedgerEntry, scan['connect']['entry_id']).pending_reversal


def test_queue_committed_before_provider_and_repeat_sweep_is_noop(client, monkeypatch):
    vendor, scan, paid = transferred(client, 'committed', 2)
    entry_id = scan['connect']['entry_id']
    calls = []
    def reverse(*args, **kwargs):
        with base._db() as observer:
            assert observer.get(models.Redemption, scan['redemption_id']).revoked_at is not None
            entry = observer.get(ConnectLedgerEntry, entry_id)
            assert entry.pending_reversal and entry.note == 'reversal_dispatch_started'
        calls.append(kwargs['idempotency_key'])
        return {'reversal_id': 'trr_test_committed'}
    monkeypatch.setattr(connect_gateway, 'reverse_transfer', reverse)
    r = client.post(f"/api/v3/redemptions/{scan['redemption_id']}/revoke", headers=vendor)
    assert r.status_code == 200 and r.json()['connect']['reason'] == 'reversal_queued'
    assert calls == []
    base._sweep()
    base._sweep()
    assert calls == [f'reversal-{entry_id}']
    assert base._entry(entry_id)['status'] == 'reversed'


def test_timeout_stays_unknown_and_never_retries(client, monkeypatch):
    vendor, scan, paid = transferred(client, 'timeout', 3)
    entry_id = scan['connect']['entry_id']
    calls = []
    def timeout(*args, **kwargs):
        calls.append(kwargs['idempotency_key'])
        raise TimeoutError('result unknown')
    monkeypatch.setattr(connect_gateway, 'reverse_transfer', timeout)
    assert client.post(f"/api/v3/redemptions/{scan['redemption_id']}/revoke", headers=vendor).status_code == 200
    base._sweep()
    base._sweep()
    assert calls == [f'reversal-{entry_id}']
    row = base._entry(entry_id)
    assert row['pending_reversal'] and row['note'] == 'reversal_result_unknown' and row['manual_notified']
    assert client.post(f'/api/v3/merchant/connect/transfers/{entry_id}/retry', headers=vendor).status_code == 409
    with base._db() as db:
        with pytest.raises(HTTPException) as error:
            connect_service.ensure_prior_transfer_closed(db, paid['wallet_item_id'])
        assert error.value.status_code == 409


def test_success_then_local_commit_failure_stays_unknown(client, monkeypatch):
    vendor, scan, _ = transferred(client, 'lostcommit', 4)
    entry_id = scan['connect']['entry_id']
    calls = []
    monkeypatch.setattr(connect_gateway, 'reverse_transfer', lambda *a, **k: calls.append(k['idempotency_key']) or {'reversal_id': 'trr_test_lost'})
    assert client.post(f"/api/v3/redemptions/{scan['redemption_id']}/revoke", headers=vendor).status_code == 200
    with base._db() as db:
        entry = db.query(ConnectLedgerEntry).filter_by(id=entry_id).with_for_update().one()
        real_commit = db.commit
        commits = []
        def fail_result_commit():
            commits.append(1)
            if len(commits) == 2:
                raise RuntimeError('simulated local result commit failure')
            return real_commit()
        monkeypatch.setattr(db, 'commit', fail_result_commit)
        assert connect_service._attempt_reversal(db, entry)['reversed']
        with pytest.raises(RuntimeError, match='result commit failure'):
            db.commit()
        db.rollback()
    base._sweep()
    assert calls == [f'reversal-{entry_id}']
    row = base._entry(entry_id)
    assert row['note'] == 'reversal_dispatch_started' and row['pending_reversal']
    assert client.post(f'/api/v3/merchant/connect/transfers/{entry_id}/retry', headers=vendor).status_code == 409
    with base._db() as db:
        db.get(ConnectLedgerEntry, entry_id).updated_at = datetime.utcnow() - timedelta(minutes=16)
        db.commit()
    base._sweep()
    base._sweep()
    assert calls == [f'reversal-{entry_id}']
    assert base._entry(entry_id)['manual_notified']


@pytest.mark.skipif(base.BACKEND != 'postgres', reason='requires isolated PostgreSQL')
def test_concurrent_sweeps_only_dispatch_once(client, monkeypatch):
    vendor, scan, _ = transferred(client, 'concurrent', 5)
    entry_id = scan['connect']['entry_id']
    calls, entered, release = [], threading.Event(), threading.Event()
    def wait_for_result(*args, **kwargs):
        calls.append(kwargs['idempotency_key'])
        entered.set()
        assert release.wait(15)
        return {'reversal_id': 'trr_test_concurrent'}
    monkeypatch.setattr(connect_gateway, 'reverse_transfer', wait_for_result)
    assert client.post(f"/api/v3/redemptions/{scan['redemption_id']}/revoke", headers=vendor).status_code == 200
    first = threading.Thread(target=base._sweep)
    first.start()
    assert entered.wait(15)
    try:
        base._sweep()
        assert calls == [f'reversal-{entry_id}']
    finally:
        release.set()
        first.join(20)
    assert not first.is_alive()
    assert base._entry(entry_id)['status'] == 'reversed'


def test_refund_never_dispatches_connect_in_its_transaction(client, monkeypatch):
    base._onboard_sync(client, shop_id=6)
    user = base._register_and_login(client, 'reversal-refund@example.com')
    vendor = base._vendor_headers(client)
    package = base._create_package(client, 'reversal-refund', shop_id=6)
    order = base._buy_paid(client, user, package['id'])
    scan = base._qr_and_scan(client, user, vendor, order['wallet_item_id'])
    base._sweep()
    entry_id = scan['connect']['entry_id']
    calls = []
    monkeypatch.setattr(connect_gateway, 'reverse_transfer', lambda *a, **k: calls.append(k['idempotency_key']) or {'reversal_id': 'trr_test_refund'})
    assert client.post(f"/api/v3/redemptions/{scan['redemption_id']}/revoke", headers=vendor).status_code == 200
    refund = client.post(f"/api/v3/orders/{order['id']}/refund", json={'reason': 'changed'}, headers=user)
    assert refund.status_code == 200 and refund.json()['refund']['status'] == 'refunded'
    assert calls == []
    base._sweep()
    assert calls == [f'reversal-{entry_id}']
