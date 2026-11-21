"""Business regression: a re-redemption cannot get a second receivable
until the same voucher's prior transfer has been conclusively reversed.
All provider operations are test doubles; no Stripe calls are made.
"""
import threading
import pytest
from tests import test_p1b_connect_fixes as base
from tests.test_p1b_connect_fixes import client
from app import connect_gateway, connect_service, models
from app.models_connect import ConnectLedgerEntry


def _fresh(client, email, shop_id):
    base._sweep()
    base._onboard_sync(client, shop_id=shop_id)
    user = base._register_and_login(client, email)
    vendor = base._vendor_headers(client)
    package = base._create_package(client, email, price=150, shop_id=shop_id)
    paid = base._buy_paid(client, user, package['id'])
    scan = base._qr_and_scan(client, user, vendor, paid['wallet_item_id'])
    return user, vendor, paid, scan


def _code(client, user, item_id):
    r = client.post('/api/v3/store-scaned-qr-values', params={'wallet_item_id': item_id}, headers=user)
    assert r.status_code == 200, r.text
    return r.json()['code']


def _rescan(client, vendor, code):
    return client.post('/api/v3/new-scan-coupon', json={'code': code}, headers=vendor)


def _entries(wallet_id):
    with base._db() as db:
        return [(e.id, e.status, e.pending_reversal) for e in
                db.query(ConnectLedgerEntry).join(models.Redemption,
                models.Redemption.id == ConnectLedgerEntry.redemption_id)
                .filter(models.Redemption.wallet_item_id == wallet_id)
                .order_by(ConnectLedgerEntry.id).all()]


def test_redeem_waits_for_prior_reversal_then_can_retry(client, monkeypatch):
    user, vendor, paid, scan = _fresh(client, 'p1b-reredeem-block@example.com', 1)
    entry = scan['connect']['entry_id']
    assert base._sweep()['transferred'] >= 1
    def unknown_reversal(*args, **kwargs):
        raise RuntimeError('provider unreachable')
    monkeypatch.setattr(connect_gateway, 'reverse_transfer', unknown_reversal)
    revoke = client.post(f"/api/v3/redemptions/{scan['redemption_id']}/revoke", headers=vendor)
    assert revoke.status_code == 200, revoke.text
    assert _entries(paid['wallet_item_id']) == [(entry, 'transferred', True)]
    assert base._balance(client, 1)['available'] == 0
    code = _code(client, user, paid['wallet_item_id'])
    prepare = client.post('/api/v3/new-scan-coupon/prepare', json={'code': code}, headers=vendor)
    assert prepare.status_code == 200 and prepare.json()['can_redeem'] is False
    assert '商戶結算仍待確認' in prepare.json()['reason']
    blocked = _rescan(client, vendor, code)
    assert blocked.status_code == 409, blocked.text
    assert len(_entries(paid['wallet_item_id'])) == 1
    monkeypatch.setattr(connect_gateway, 'reverse_transfer', lambda *a, **k: {'reversal_id': 'trr_test_resolved'})
    retry = client.post(f'/api/v3/merchant/connect/transfers/{entry}/retry', headers=vendor)
    assert retry.status_code == 200, retry.text
    successful = _rescan(client, vendor, code)
    assert successful.status_code == 200, successful.text
    rows = _entries(paid['wallet_item_id'])
    assert len(rows) == 2 and rows[0][1] == 'reversed' and rows[1][1] == 'pending'


def test_unknown_prior_transfer_is_not_misclassified_as_unsent(client, monkeypatch):
    user, vendor, paid, scan = _fresh(client, 'p1b-reredeem-unknown@example.com', 2)
    entry = scan['connect']['entry_id']
    attempts = []
    def uncertain(*args, **kwargs):
        attempts.append(kwargs['idempotency_key'])
        raise RuntimeError('provider result unknown')
    monkeypatch.setattr(connect_gateway, 'create_transfer', uncertain)
    base._sweep()
    assert base._entry(entry)['status'] == 'transfer_unknown'
    revoke = client.post(f"/api/v3/redemptions/{scan['redemption_id']}/revoke", headers=vendor)
    assert revoke.status_code == 200, revoke.text
    assert _entries(paid['wallet_item_id']) == [(entry, 'transfer_unknown', True)]
    blocked = _rescan(client, vendor, _code(client, user, paid['wallet_item_id']))
    assert blocked.status_code == 409, blocked.text
    base._sweep()
    assert len(attempts) == 1



def test_unsent_intent_can_be_cancelled_and_redeemed_again(client):
    user, vendor, paid, scan = _fresh(client, 'p1b-reredeem-unsent@example.com', 4)
    entry = scan['connect']['entry_id']
    revoke = client.post(f"/api/v3/redemptions/{scan['redemption_id']}/revoke", headers=vendor)
    assert revoke.status_code == 200 and revoke.json()['connect']['reversed'] is True
    rescan = _rescan(client, vendor, _code(client, user, paid['wallet_item_id']))
    assert rescan.status_code == 200, rescan.text
    rows = _entries(paid['wallet_item_id'])
    assert len(rows) == 2 and rows[0] == (entry, 'reversed', False) and rows[1][1] == 'pending'


def test_legacy_reversal_without_provenance_stays_blocked(client):
    user, vendor, paid, scan = _fresh(client, 'p1b-reredeem-legacy@example.com', 5)
    entry_id = scan['connect']['entry_id']
    revoke = client.post(f"/api/v3/redemptions/{scan['redemption_id']}/revoke", headers=vendor)
    assert revoke.status_code == 200
    with base._db() as db:
        row = db.get(ConnectLedgerEntry, entry_id)
        row.dispatch_started = None
        db.commit()
    blocked = _rescan(client, vendor, _code(client, user, paid['wallet_item_id']))
    assert blocked.status_code == 409
    assert len(_entries(paid['wallet_item_id'])) == 1


def test_legacy_pending_without_provenance_is_not_dispatched(client, monkeypatch):
    user, vendor, paid, scan = _fresh(client, 'p1b-reredeem-legacy-pending@example.com', 7)
    entry_id = scan['connect']['entry_id']
    with base._db() as db:
        row = db.get(ConnectLedgerEntry, entry_id)
        row.dispatch_started = None
        db.commit()
    calls = []
    monkeypatch.setattr(connect_gateway, 'create_transfer', lambda *a, **k: calls.append((a, k)))
    base._sweep()
    assert calls == []
    assert base._entry(entry_id)['status'] == 'transfer_unknown'
    revoke = client.post(f"/api/v3/redemptions/{scan['redemption_id']}/revoke", headers=vendor)
    assert revoke.status_code == 200 and revoke.json()['connect']['reversed'] is False
    assert _rescan(client, vendor, _code(client, user, paid['wallet_item_id'])).status_code == 409


def test_refund_after_revocation_cannot_be_rescanned(client):
    user, vendor, paid, scan = _fresh(client, 'p1b-reredeem-refund@example.com', 6)
    revoke = client.post(f"/api/v3/redemptions/{scan['redemption_id']}/revoke", headers=vendor)
    assert revoke.status_code == 200
    refund = client.post(f"/api/v3/orders/{paid['id']}/refund", json={'reason': 'changed'}, headers=user)
    assert refund.status_code == 200 and refund.json()['order']['refund_status'] == 'refunded'
    qr = client.post('/api/v3/store-scaned-qr-values', params={'wallet_item_id': paid['wallet_item_id']}, headers=user)
    assert qr.status_code == 400
    assert len(_entries(paid['wallet_item_id'])) == 1


@pytest.mark.skipif(base.BACKEND != 'postgres', reason='requires dedicated PostgreSQL to exercise concurrent row locks')
def test_inflight_transfer_then_revoke_blocks_rescan_without_deadlock(client, monkeypatch):
    user, vendor, paid, scan = _fresh(client, 'p1b-reredeem-race@example.com', 3)
    entry = scan['connect']['entry_id']
    entered, release = threading.Event(), threading.Event()
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(15)
        return {'transfer_id': 'tr_test_inflight', 'status': 'paid'}
    monkeypatch.setattr(connect_gateway, 'create_transfer', delayed)
    results = []
    t = threading.Thread(target=lambda: results.append(base._sweep()))
    t.start()
    assert entered.wait(15)
    try:
        with base._db() as db:
            persisted = db.get(ConnectLedgerEntry, entry)
            assert persisted.status == 'transferring'
            assert persisted.dispatch_started is True
        revoke = client.post(f"/api/v3/redemptions/{scan['redemption_id']}/revoke", headers=vendor)
        assert revoke.status_code == 200, revoke.text
        assert revoke.json()['connect']['reversed'] is False
        blocked = _rescan(client, vendor, _code(client, user, paid['wallet_item_id']))
        assert blocked.status_code == 409, blocked.text
    finally:
        release.set()
        t.join(timeout=20)
    assert not t.is_alive()
    assert _entries(paid['wallet_item_id']) == [(entry, 'transferred', True)]


@pytest.mark.parametrize('legacy', [False, True])
def test_dispatch_marker_migration_preserves_legacy_unknown(legacy, tmp_path):
    """Fresh and preexisting tables both gain a nullable dispatch marker."""
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    path = Path(__file__).resolve().parents[1] / 'alembic/versions/0021_p1b_stripe_connect.py'
    spec = importlib.util.spec_from_file_location('reredeem_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f'sqlite:///{tmp_path}/migration.db')
    with engine.begin() as conn:
        if legacy:
            conn.execute(text('CREATE TABLE connect_ledger_entries (id INTEGER PRIMARY KEY)'))
            conn.execute(text('INSERT INTO connect_ledger_entries (id) VALUES (1)'))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.upgrade()
        columns = {c['name']: c for c in inspect(conn).get_columns('connect_ledger_entries')}
        assert columns['dispatch_started']['nullable'] is True
        if legacy:
            assert conn.execute(text('SELECT dispatch_started FROM connect_ledger_entries WHERE id=1')).scalar_one() is None
    engine.dispose()


@pytest.mark.skipif(base.BACKEND != 'postgres', reason='requires dedicated PostgreSQL to exercise concurrent row locks')
def test_rescan_waits_for_concurrent_successful_reversal(client, monkeypatch):
    user, vendor, paid, scan = _fresh(client, 'p1b-reredeem-reversal-race@example.com', 8)
    entry = scan['connect']['entry_id']
    assert base._sweep()['transferred'] >= 1
    def failed(*args, **kwargs):
        raise RuntimeError('reversal pending')
    monkeypatch.setattr(connect_gateway, 'reverse_transfer', failed)
    revoke = client.post(f"/api/v3/redemptions/{scan['redemption_id']}/revoke", headers=vendor)
    assert revoke.status_code == 200 and revoke.json()['connect']['reversed'] is False
    code = _code(client, user, paid['wallet_item_id'])
    entered, release, scan_done = threading.Event(), threading.Event(), threading.Event()
    result = {}
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(15)
        return {'reversal_id': 'trr_test_concurrent'}
    monkeypatch.setattr(connect_gateway, 'reverse_transfer', delayed)
    def retry():
        result['retry'] = client.post(f'/api/v3/merchant/connect/transfers/{entry}/retry', headers=vendor)
    def rescan():
        try:
            result['scan'] = _rescan(client, vendor, code)
        finally:
            scan_done.set()
    retry_thread = threading.Thread(target=retry)
    retry_thread.start()
    assert entered.wait(15)
    scan_thread = threading.Thread(target=rescan)
    scan_thread.start()
    try:
        assert not scan_done.wait(0.3), 'scan should wait for the previous ledger lock'
    finally:
        release.set()
        retry_thread.join(timeout=20)
        scan_thread.join(timeout=20)
    assert not retry_thread.is_alive() and not scan_thread.is_alive()
    assert result['retry'].status_code == 200, result['retry'].text
    assert result['scan'].status_code == 200, result['scan'].text
    rows = _entries(paid['wallet_item_id'])
    assert len(rows) == 2 and rows[0][1] == 'reversed' and rows[1][1] == 'pending'
