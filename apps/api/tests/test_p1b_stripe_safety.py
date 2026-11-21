"""Use only a disposable local P1B_SAFE_POSTGRES_URL; provider is stubbed."""
import os
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import urlparse
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app import config, connect_gateway, connect_service, models, models_connect
from app.database import Base
from app.models_connect import ConnectAccount, ConnectLedgerEntry

URL = os.environ.get('P1B_SAFE_POSTGRES_URL', '')
p = urlparse(URL.replace('postgresql+psycopg2://', 'postgresql://', 1))
if p.hostname not in ('127.0.0.1', 'localhost') or not p.path.startswith('/coupon_pr16_'):
    pytest.skip('requires explicitly configured disposable local PR16 Postgres', allow_module_level=True)

@pytest.fixture(scope='module')
def factory():
    engine = create_engine(URL, pool_size=8, max_overflow=4)
    Base.metadata.create_all(bind=engine)  # never DROP existing data
    yield sessionmaker(bind=engine)
    engine.dispose()

@pytest.fixture
def shop_id(factory, monkeypatch):
    monkeypatch.setattr(config, 'STRIPE_CONNECT_ENABLED_RAW', '1')
    monkeypatch.setattr(config, 'APP_ENV', 'dev')
    monkeypatch.setattr(config, 'STRIPE_CONNECT_SIMULATOR', True)
    with factory() as db:
        user = models.User(email=f'{uuid4().hex}@example.invalid', password_hash='disabled', role='vendor')
        db.add(user); db.flush()
        shop = models.Shop(vendor_id=user.id, name='Test shop'); db.add(shop); db.flush()
        db.add(ConnectAccount(shop_id=shop.id, stripe_account_id='acct_test_safe', charges_enabled=True,
                              payouts_enabled=True, details_submitted=True, manual_payouts=True))
        db.add(ConnectLedgerEntry(shop_id=shop.id, kind='transfer', status='transferred', gross_amount=100,
                                   commission=0, net_amount=100, stripe_transfer_id='tr_test_original'))
        db.commit(); return shop.id

def test_concurrent_payouts_cannot_spend_same_balance(factory, shop_id, monkeypatch):
    started, release, calls, result = threading.Event(), threading.Event(), [], []
    def provider(account, amount, *, idempotency_key):
        calls.append((account, amount, idempotency_key)); started.set(); assert release.wait(10)
        return {'payout_id': 'po_test_one', 'status': 'pending'}
    monkeypatch.setattr(connect_gateway, 'create_payout', provider)
    def first():
        with factory() as db:
            result.append(connect_service.request_payout(db, db.get(models.Shop, shop_id), Decimal('80'), 'request_0000000001'))
    thread = threading.Thread(target=first); thread.start(); assert started.wait(10)
    with factory() as db:
        with pytest.raises(HTTPException) as exc:
            connect_service.request_payout(db, db.get(models.Shop, shop_id), Decimal('80'), 'request_0000000002')
        assert exc.value.status_code == 422
    release.set(); thread.join(10)
    assert not thread.is_alive() and len(calls) == 1
    assert calls[0][1] == Decimal('80') and calls[0][2].startswith('payout-')
    assert result[0]['status'] == 'submitted'
    with factory() as db:
        assert connect_service.merchant_balance(db, shop_id)['available'] == Decimal('20')

def test_timeout_keeps_reservation_and_retry_does_not_resend(factory, shop_id, monkeypatch):
    calls = []
    def provider(account, amount, *, idempotency_key):
        calls.append(idempotency_key); raise TimeoutError('provider may have executed')
    monkeypatch.setattr(connect_gateway, 'create_payout', provider)
    with factory() as db:
        with pytest.raises(HTTPException) as exc:
            connect_service.request_payout(db, db.get(models.Shop, shop_id), Decimal('60'), 'request_0000000003')
        assert exc.value.status_code == 502
    with factory() as db:
        result = connect_service.request_payout(db, db.get(models.Shop, shop_id), Decimal('60'), 'request_0000000003')
        assert result['status'] == 'payout_unknown' and result['balance']['available'] == 40.0
        with pytest.raises(HTTPException):
            connect_service.request_payout(db, db.get(models.Shop, shop_id), Decimal('60'), 'request_0000000004')
    assert len(calls) == 1

def test_transfer_timeout_is_unknown_and_never_resubmitted(factory, shop_id, monkeypatch):
    calls = []
    def provider(account, amount, transfer_group, *, idempotency_key):
        calls.append(idempotency_key); raise TimeoutError('unknown')
    monkeypatch.setattr(connect_gateway, 'create_transfer', provider)
    with factory() as db:
        entry = ConnectLedgerEntry(shop_id=shop_id, kind='transfer', status='pending', gross_amount=20, commission=0, net_amount=20)
        db.add(entry); db.commit(); entry_id, operation_id = entry.id, entry.operation_id
        assert connect_service.retry_entry(db, entry)['status'] == 'unknown'
    with factory() as db:
        entry = db.get(ConnectLedgerEntry, entry_id)
        assert entry.first_attempt_at is not None and entry.attempt_count == 1
        with pytest.raises(HTTPException) as exc:
            connect_service.retry_entry(db, entry)
        assert exc.value.status_code == 409
    assert calls == [f'transfer-{operation_id}']

def test_refund_during_transfer_keeps_reversal_due(factory, shop_id, monkeypatch):
    started, release = threading.Event(), threading.Event()
    def provider(account, amount, transfer_group, *, idempotency_key):
        started.set(); assert release.wait(10); return {'transfer_id': 'tr_test_concurrent'}
    monkeypatch.setattr(connect_gateway, 'create_transfer', provider)
    with factory() as db:
        user = db.query(models.User).join(models.Shop).filter(models.Shop.id == shop_id).one()
        coupon = models.Coupon(shop_id=shop_id, vendor_id=user.id, coupon_name='test', valid_from=datetime.now(), valid_to=datetime.now()+timedelta(days=1))
        db.add(coupon); db.flush()
        wallet = models.WalletItem(user_id=user.id, coupon_id=coupon.id); db.add(wallet); db.flush()
        red = models.Redemption(wallet_item_id=wallet.id, coupon_id=coupon.id, shop_id=shop_id, scanned_by_vendor_id=user.id)
        db.add(red); db.flush()
        entry = ConnectLedgerEntry(shop_id=shop_id, redemption_id=red.id, kind='transfer', status='pending', gross_amount=20, commission=0, net_amount=20)
        db.add(entry); db.commit(); entry_id, red_id = entry.id, red.id
    def dispatch():
        with factory() as db:
            connect_service.retry_entry(db, db.get(ConnectLedgerEntry, entry_id))
    thread = threading.Thread(target=dispatch); thread.start(); assert started.wait(10)
    with factory() as db:
        assert connect_service.reverse_redemption(db, db.get(models.Redemption, red_id), 'refunded')['reason'] == 'pending_reversal'
        db.commit()
    release.set(); thread.join(10); assert not thread.is_alive()
    with factory() as db:
        entry = db.get(ConnectLedgerEntry, entry_id)
        assert entry.status == 'transferred' and entry.pending_reversal is True
        assert connect_service.merchant_balance(db, shop_id)['available'] == Decimal('100')

def test_gateway_sends_stable_keys_and_exact_cents(monkeypatch):
    captured = []
    monkeypatch.setattr(config, 'STRIPE_SECRET_KEY', 'sk_test_fake_only')
    def post(key, path, data, headers=None):
        captured.append((path, data, headers))
        return {'id': 'po_test_1' if path == '/payouts' else 'tr_test_1', 'amount': data['amount'], 'currency': 'hkd'}
    monkeypatch.setattr(connect_gateway, '_post', post)
    connect_gateway.create_transfer('acct_test', Decimal('1.01'), 'redemption-1', idempotency_key='transfer-7', source_transaction='ch_123')
    connect_gateway.reverse_transfer('tr_test_1', Decimal('1.01'), idempotency_key='reversal-7')
    connect_gateway.create_payout('acct_test', Decimal('1.01'), idempotency_key='payout-8')
    assert [entry[1]['amount'] for entry in captured] == [101, 101, 101]
    assert [entry[2]['Idempotency-Key'] for entry in captured] == ['transfer-7', 'reversal-7', 'payout-8']
    assert captured[-1][2]['Stripe-Account'] == 'acct_test'
    with pytest.raises(Exception):
        connect_gateway.create_payout('acct_test', Decimal('1.001'), idempotency_key='payout-9')


def test_unconfigured_or_live_key_cannot_simulate_money(monkeypatch):
    monkeypatch.setattr(config, 'STRIPE_CONNECT_ENABLED_RAW', '1')
    monkeypatch.setattr(config, 'APP_ENV', 'production')
    monkeypatch.setattr(config, 'STRIPE_CONNECT_SIMULATOR', True)
    monkeypatch.setattr(config, 'STRIPE_SECRET_KEY', '')
    assert not connect_service.connect_configured()
    with pytest.raises(Exception):
        connect_gateway.create_payout('acct_test', Decimal('1'), idempotency_key='payout-1')
    monkeypatch.setattr(config, 'STRIPE_SECRET_KEY', 'sk_live_invalid_do_not_use')
    assert not connect_service.connect_configured()
    with pytest.raises(Exception):
        connect_gateway.create_payout('acct_test', Decimal('1'), idempotency_key='payout-1')


def test_redemption_intent_rolls_back_without_provider_call(factory, shop_id, monkeypatch):
    monkeypatch.setattr(connect_gateway, 'create_transfer', lambda *args, **kwargs: pytest.fail('Stripe was called before commit'))
    with factory() as db:
        user = db.query(models.User).join(models.Shop).filter(models.Shop.id == shop_id).one()
        coupon = models.Coupon(shop_id=shop_id, vendor_id=user.id, coupon_name='rollback test', valid_from=datetime.now(), valid_to=datetime.now()+timedelta(days=1))
        db.add(coupon); db.flush()
        order = models.Order(user_id=user.id, amount=50, paid_amount=50, payment_status='paid', refund_status='none')
        db.add(order); db.flush()
        payment = models.Payment(order_id=order.id, gateway='stripe', gateway_ref='pi_test_rollback', amount=50, currency='HKD', status='succeeded')
        db.add(payment); db.flush()
        wallet = models.WalletItem(user_id=user.id, coupon_id=coupon.id, source='purchase', order_id=order.id); db.add(wallet); db.flush()
        red = models.Redemption(wallet_item_id=wallet.id, coupon_id=coupon.id, shop_id=shop_id, scanned_by_vendor_id=user.id)
        db.add(red); db.flush()
        red_id = red.id
        result = connect_service.record_redemption(db, red)
        assert result['transfer'] == 'pending'
        db.rollback()
    with factory() as db:
        assert db.query(ConnectLedgerEntry).filter_by(redemption_id=red_id).count() == 0


def test_reversal_and_payout_share_shop_lock(factory, shop_id, monkeypatch):
    called = []
    monkeypatch.setattr(connect_gateway, 'create_payout', lambda *a, **k: called.append(1))
    with factory() as db:
        user = db.query(models.User).join(models.Shop).filter(models.Shop.id == shop_id).one()
        coupon = models.Coupon(shop_id=shop_id, vendor_id=user.id, coupon_name='locked', valid_from=datetime.now(), valid_to=datetime.now()+timedelta(days=1))
        db.add(coupon); db.flush()
        wallet = models.WalletItem(user_id=user.id, coupon_id=coupon.id); db.add(wallet); db.flush()
        red = models.Redemption(wallet_item_id=wallet.id, coupon_id=coupon.id, shop_id=shop_id, scanned_by_vendor_id=user.id)
        db.add(red); db.flush()
        entry = db.query(ConnectLedgerEntry).filter_by(shop_id=shop_id, kind='transfer').one()
        entry.redemption_id = red.id
        db.commit(); red_id = red.id
    entered, outcomes = threading.Event(), []
    reversal_db = factory()
    try:
        red = reversal_db.get(models.Redemption, red_id)
        connect_service.reverse_redemption(reversal_db, red, 'refunded')
        def payout():
            with factory() as db:
                entered.set()
                try:
                    connect_service.request_payout(db, db.get(models.Shop, shop_id), Decimal('80'), 'payout_after_reversal_1')
                    outcomes.append('paid')
                except HTTPException as exc:
                    outcomes.append(exc.status_code)
        thread = threading.Thread(target=payout); thread.start(); assert entered.wait(5)
        # The payout must wait for the merchant lock until reversal commits.
        assert thread.is_alive()
        reversal_db.commit()
        thread.join(10)
        assert not thread.is_alive() and outcomes == [422] and called == []
    finally:
        reversal_db.close()


def test_dispatch_commits_before_network_io(factory, shop_id, monkeypatch):
    with factory() as db:
        entry = ConnectLedgerEntry(shop_id=shop_id, kind='transfer', status='pending', gross_amount=20, commission=0, net_amount=20)
        db.add(entry); db.commit(); entry_id = entry.id
        def provider(account, amount, transfer_group, *, idempotency_key):
            assert not db.in_transaction()
            with factory() as other:
                assert other.get(ConnectLedgerEntry, entry_id).status == 'processing'
            return {'transfer_id': 'tr_after_commit'}
        monkeypatch.setattr(connect_gateway, 'create_transfer', provider)
        assert connect_service.retry_entry(db, entry)['status'] == 'transferred'


def test_unknown_payout_reconciles_only_matching_provider_object(factory, shop_id, monkeypatch):
    def timeout(*args, **kwargs):
        raise TimeoutError('response lost')
    monkeypatch.setattr(connect_gateway, 'create_payout', timeout)
    with factory() as db:
        with pytest.raises(HTTPException):
            connect_service.request_payout(db, db.get(models.Shop, shop_id), Decimal('60'), 'request_reconcile_0001')
    with factory() as db:
        entry = db.query(ConnectLedgerEntry).filter_by(shop_id=shop_id, kind='payout').one()
        entry_id, operation_id = entry.id, entry.operation_id
        admin_id = db.get(models.Shop, shop_id).vendor_id
    obj = {'id': 'po_test_confirmed', 'amount': 6000, 'currency': 'hkd',
           'metadata': {'connect_operation_key': f'payout-{operation_id}'}, 'status': 'pending'}
    seen = []
    def read(account_id, provider_id):
        seen.append((account_id, provider_id))
        return obj.copy()
    monkeypatch.setattr(connect_gateway, 'retrieve_payout', read)
    with factory() as db:
        out = connect_service.reconcile_entry(db, entry_id, 'po_test_confirmed', admin_id)
        assert out['status'] == 'submitted'
        assert connect_service.merchant_balance(db, shop_id)['available'] == Decimal('40')
    assert seen == [('acct_test_safe', 'po_test_confirmed')]
    obj['status'] = 'failed'
    with factory() as db:
        out = connect_service.reconcile_entry(db, entry_id, 'po_test_confirmed', admin_id)
        assert out['status'] == 'failed'
        assert connect_service.merchant_balance(db, shop_id)['available'] == Decimal('100')
        logs = db.query(models.AdminAuditLog).filter_by(target_id=entry_id, action='connect.reconcile').count()
        assert logs == 2


def test_reconciliation_rejects_mismatched_metadata_and_preserves_hold(factory, shop_id, monkeypatch):
    with factory() as db:
        entry = ConnectLedgerEntry(shop_id=shop_id, kind='payout', status='payout_unknown',
            gross_amount=0, commission=0, net_amount=-60, client_request_id='request_reconcile_0002',
            destination_account_id='acct_test_safe')
        db.add(entry); db.commit(); entry_id = entry.id; admin_id = db.get(models.Shop, shop_id).vendor_id
    monkeypatch.setattr(connect_gateway, 'retrieve_payout', lambda *args: {
        'id': 'po_test_wrong', 'amount': 6000, 'currency': 'hkd',
        'metadata': {'connect_operation_key': 'payout-other'}, 'status': 'failed'})
    with factory() as db:
        with pytest.raises(HTTPException) as exc:
            connect_service.reconcile_entry(db, entry_id, 'po_test_wrong', admin_id)
        assert exc.value.status_code == 409
        assert connect_service.merchant_balance(db, shop_id)['available'] == Decimal('40')
        assert db.get(ConnectLedgerEntry, entry_id).status == 'payout_unknown'


def test_unknown_transfer_can_be_verified_without_resending(factory, shop_id, monkeypatch):
    with factory() as db:
        entry = ConnectLedgerEntry(shop_id=shop_id, kind='transfer', status='unknown', gross_amount=20,
            commission=0, net_amount=20, destination_account_id='acct_test_safe', pending_reversal=True)
        db.add(entry); db.commit(); entry_id, operation_id = entry.id, entry.operation_id; admin_id = db.get(models.Shop, shop_id).vendor_id
    monkeypatch.setattr(connect_gateway, 'retrieve_transfer', lambda provider_id: {
        'id': provider_id, 'amount': 2000, 'currency': 'hkd', 'destination': 'acct_test_safe',
        'metadata': {'connect_operation_key': f'transfer-{operation_id}'}})
    with factory() as db:
        out = connect_service.reconcile_entry(db, entry_id, 'tr_test_found', admin_id)
        assert out['status'] == 'transferred' and out['pending_reversal'] is True
        assert connect_service.merchant_balance(db, shop_id)['available'] == Decimal('100')


def test_unknown_reversal_reconciliation_restores_correct_balance(factory, shop_id, monkeypatch):
    with factory() as db:
        entry = db.query(ConnectLedgerEntry).filter_by(shop_id=shop_id, kind='transfer').one()
        entry.status = 'reversal_unknown'; entry.pending_reversal = True
        entry.destination_account_id = 'acct_test_safe'; entry.stripe_transfer_id = 'tr_test_original'
        db.commit(); entry_id, operation_id = entry.id, entry.operation_id; admin_id = db.get(models.Shop, shop_id).vendor_id
    monkeypatch.setattr(connect_gateway, 'retrieve_reversal', lambda transfer_id, provider_id: {
        'id': provider_id, 'amount': 10000, 'currency': 'hkd', 'transfer': transfer_id,
        'metadata': {'connect_operation_key': f'reversal-{operation_id}'}})
    with factory() as db:
        out = connect_service.reconcile_entry(db, entry_id, 'trr_test_found', admin_id)
        assert out['status'] == 'reversed' and out['pending_reversal'] is False
        assert connect_service.merchant_balance(db, shop_id)['available'] == Decimal('0')


def test_recent_processing_cannot_be_reconciled_while_request_is_live(factory, shop_id, monkeypatch):
    with factory() as db:
        entry = ConnectLedgerEntry(shop_id=shop_id, kind='payout', status='processing',
            gross_amount=0, commission=0, net_amount=-60, client_request_id='request_processing_0001',
            destination_account_id='acct_test_safe', last_attempt_at=datetime.utcnow())
        db.add(entry); db.commit(); entry_id = entry.id; admin_id = db.get(models.Shop, shop_id).vendor_id
    monkeypatch.setattr(connect_gateway, 'retrieve_payout', lambda *args: pytest.fail('must not query a live request'))
    with factory() as db:
        with pytest.raises(HTTPException) as exc:
            connect_service.reconcile_entry(db, entry_id, 'po_test_unverified', admin_id)
        assert exc.value.status_code == 409
        assert connect_service.merchant_balance(db, shop_id)['available'] == Decimal('40')


def test_unready_shops_do_not_starve_ready_batch(factory, monkeypatch):
    monkeypatch.setattr(config, 'STRIPE_CONNECT_ENABLED_RAW', '1')
    monkeypatch.setattr(config, 'APP_ENV', 'dev')
    monkeypatch.setattr(config, 'STRIPE_CONNECT_SIMULATOR', True)
    with factory() as db:
        ids = []
        for i in range(4):
            user = models.User(email=f'{uuid4().hex}@example.invalid', password_hash='x', role='vendor')
            db.add(user); db.flush()
            shop = models.Shop(vendor_id=user.id, name=f'queue-{i}'); db.add(shop); db.flush()
            db.add(ConnectAccount(shop_id=shop.id, stripe_account_id='' if i == 2 else f'acct_ready_{i}',
                charges_enabled=i >= 2, payouts_enabled=i >= 2, manual_payouts=i >= 2))
            entry = ConnectLedgerEntry(shop_id=shop.id, kind='transfer', status='pending',
                gross_amount=10, commission=0, net_amount=10)
            db.add(entry); db.flush(); ids.append(entry.id)
        db.commit()
    monkeypatch.setattr(connect_gateway, 'create_transfer', lambda *args, **kwargs: {'transfer_id': 'tr_ready'})
    with factory() as db:
        # Earlier test cases may have open pending rows; isolate the batch query
        # by marking only our fixture's unready rows and dispatching by ID order.
        for row in db.query(ConnectLedgerEntry).filter(
                ((ConnectLedgerEntry.status == 'pending') |
                 ((ConnectLedgerEntry.status == 'transferred') & ConnectLedgerEntry.pending_reversal.is_(True))),
                ~ConnectLedgerEntry.id.in_(ids)).all():
            row.status = 'unknown'
        db.commit()
        assert connect_service.sweep_connect_transfers(db, batch_size=1)['processed'] == 1
    with factory() as db:
        rows = [db.get(ConnectLedgerEntry, entry_id).status for entry_id in ids]
        assert rows == ['pending', 'pending', 'pending', 'transferred']


def test_reversal_does_not_require_account_still_being_ready(factory, shop_id, monkeypatch):
    with factory() as db:
        account = db.query(ConnectAccount).filter_by(shop_id=shop_id).one()
        account.charges_enabled = account.payouts_enabled = False
        entry = db.query(ConnectLedgerEntry).filter_by(shop_id=shop_id, kind='transfer').one()
        entry.pending_reversal = True; entry.destination_account_id = account.stripe_account_id
        db.commit(); entry_id = entry.id
    monkeypatch.setattr(connect_gateway, 'reverse_transfer', lambda *args, **kwargs: {'reversal_id': 'trr_not_ready'})
    with factory() as db:
        assert connect_service.retry_entry(db, db.get(ConnectLedgerEntry, entry_id))['status'] == 'reversed'


def test_two_databases_with_same_integer_id_use_different_operation_keys(factory, shop_id):
    import secrets
    second_url = os.environ.get('P1B_SECOND_POSTGRES_URL', '')
    parsed = urlparse(second_url.replace('postgresql+psycopg2://', 'postgresql://', 1))
    if parsed.hostname not in ('127.0.0.1', 'localhost') or not parsed.path.startswith('/coupon_pr16_second'):
        pytest.skip('requires explicitly designated second disposable PR16 database')
    engine2 = create_engine(second_url); Base.metadata.create_all(engine2); factory2 = sessionmaker(bind=engine2)
    same_id = 1000000 + secrets.randbelow(1000000)
    with factory() as db:
        first = ConnectLedgerEntry(id=same_id, shop_id=shop_id, kind='transfer', status='pending', gross_amount=10, commission=0, net_amount=10)
        db.add(first); db.commit(); first_operation = first.operation_id
    with factory2() as db:
        user = models.User(email=f'{uuid4().hex}@example.invalid', password_hash='x', role='vendor'); db.add(user); db.flush()
        shop = models.Shop(vendor_id=user.id, name='other environment'); db.add(shop); db.flush()
        second = ConnectLedgerEntry(id=same_id, shop_id=shop.id, kind='transfer', status='pending', gross_amount=10, commission=0, net_amount=10)
        db.add(second); db.commit(); second_operation = second.operation_id
    engine2.dispose()
    assert first_operation != second_operation
    assert f'transfer-{first_operation}' != f'transfer-{second_operation}'


def test_version_config_and_provider_errors_fail_closed(monkeypatch):
    monkeypatch.setattr(config, 'STRIPE_CONNECT_ENABLED_RAW', '1')
    monkeypatch.setattr(config, 'STRIPE_SECRET_KEY', 'sk_test_fake_only')
    monkeypatch.setattr(config, 'STRIPE_CONNECT_API_VERSION', 'invalid\r\nInjected: value')
    assert not connect_service.connect_configured()
    with pytest.raises(Exception):
        connect_gateway._v2_headers('sk_test_fake_only')
    monkeypatch.setattr(config, 'STRIPE_CONNECT_API_VERSION', '2026-02-30.preview')
    assert not connect_service.connect_configured()
    monkeypatch.setattr(config, 'STRIPE_CONNECT_API_VERSION', '2026-11-18.preview')
    assert connect_service.connect_configured()
    class FailedResponse:
        status_code = 400
        text = 'secret private request content must not escape'
    monkeypatch.setattr(connect_gateway.httpx, 'post', lambda *args, **kwargs: FailedResponse())
    with pytest.raises(Exception) as exc:
        connect_gateway._post('sk_test_fake_only', '/payouts', {})
    assert 'secret private request content' not in str(exc.value)


@pytest.mark.parametrize('source', ['claim', 'bargain'])
def test_unpaid_price_and_simulated_bargain_never_create_transfer(factory, shop_id, monkeypatch, source):
    monkeypatch.setattr(connect_gateway, 'create_transfer', lambda *a, **k: pytest.fail('unpaid money moved'))
    with factory() as db:
        user = db.query(models.User).join(models.Shop).filter(models.Shop.id == shop_id).one()
        coupon = models.Coupon(shop_id=shop_id, vendor_id=user.id, coupon_type='Price', coupon_name='no receipt', discount_price=138,
            valid_from=datetime.now(), valid_to=datetime.now()+timedelta(days=1))
        db.add(coupon); db.flush()
        item = models.WalletItem(user_id=user.id, coupon_id=coupon.id, source=source); db.add(item); db.flush()
        red = models.Redemption(wallet_item_id=item.id, coupon_id=coupon.id, shop_id=shop_id, scanned_by_vendor_id=user.id)
        db.add(red); db.flush()
        assert connect_service.record_redemption(db, red)['transfer'] == 'skipped_unpaid'
        db.commit()
        assert db.query(ConnectLedgerEntry).filter_by(redemption_id=red.id).count() == 0


def test_real_gateway_refuses_simulated_or_unverified_receipt(factory, shop_id, monkeypatch):
    monkeypatch.setattr(config, 'STRIPE_SECRET_KEY', 'sk_test_fake_only')
    monkeypatch.setattr(connect_gateway, 'create_transfer', lambda *a, **k: pytest.fail('unverified funds moved'))
    with factory() as db:
        entry = ConnectLedgerEntry(shop_id=shop_id, kind='transfer', status='pending', gross_amount=100,
            commission=0, net_amount=100, payment_intent_id='pi_test_simulated')
        db.add(entry); db.commit(); entry_id = entry.id
        out = connect_service.retry_entry(db, entry)
        assert out['status'] == 'payment_verification_required'
        assert connect_service.merchant_balance(db, shop_id)['available'] == Decimal('0')
    with factory() as db:
        assert db.get(ConnectLedgerEntry, entry_id).note == 'verified_payment_provenance_required'


def test_payout_poller_handles_pending_paid_then_late_failure_idempotently(factory, shop_id, monkeypatch):
    monkeypatch.setattr(config, 'STRIPE_SECRET_KEY', 'sk_test_fake_only')
    with factory() as db:
        for existing in db.query(ConnectLedgerEntry).filter(
                ConnectLedgerEntry.kind == 'payout',
                ConnectLedgerEntry.status.in_(['submitted', 'paid_out'])).all():
            existing.last_polled_at = datetime.utcnow()
        transfer = db.query(ConnectLedgerEntry).filter_by(shop_id=shop_id, kind='transfer').one()
        transfer.stripe_charge_id = 'ch_verified'
        payout = ConnectLedgerEntry(shop_id=shop_id, kind='payout', status='submitted', gross_amount=0,
            commission=0, net_amount=-60, client_request_id='request_poll_test_0001',
            destination_account_id='acct_test_safe', stripe_payout_id='po_test_polled')
        db.add(payout); db.commit(); entry_id, op = payout.id, payout.operation_id
    provider_status = ['pending']; calls = []
    def retrieve(account_id, provider_id):
        calls.append(provider_status[0])
        return {'id': provider_id, 'amount': 6000, 'currency': 'hkd', 'status': provider_status[0],
                'metadata': {'connect_operation_key': f'payout-{op}'}}
    monkeypatch.setattr(connect_gateway, 'retrieve_payout', retrieve)
    def allow_next_poll():
        with factory() as db:
            row = db.get(ConnectLedgerEntry, entry_id)
            row.last_polled_at = datetime.utcnow() - timedelta(minutes=6)
            db.commit()
    with factory() as db:
        assert connect_service.sweep_payout_status(db, 1)['processed'] == 1
        assert db.get(ConnectLedgerEntry, entry_id).status == 'submitted'
    provider_status[0] = 'paid'; allow_next_poll()
    with factory() as db:
        assert connect_service.sweep_payout_status(db, 1)['processed'] == 1
        assert db.get(ConnectLedgerEntry, entry_id).status == 'paid_out'
        assert connect_service.merchant_balance(db, shop_id)['available'] == Decimal('40')
    provider_status[0] = 'failed'; allow_next_poll()
    with factory() as db:
        assert connect_service.sweep_payout_status(db, 1)['processed'] == 1
        assert db.get(ConnectLedgerEntry, entry_id).status == 'failed'
        assert connect_service.merchant_balance(db, shop_id)['available'] == Decimal('100')
        assert connect_service.sweep_payout_status(db, 1)['processed'] == 0
        events = db.query(models_connect.ConnectReconciliationEvent).filter_by(entry_id=entry_id).all()
        assert [(e.prior_status, e.new_status) for e in events] == [('submitted', 'paid_out'), ('paid_out', 'failed')]
    assert calls == ['pending', 'paid', 'failed']


@pytest.mark.parametrize('refund_status,key,expected', [
    ('refunded', '', 'skipped_unpaid'), ('requested', '', 'skipped_unpaid'),
    ('none', 'sk_test_fake_only', 'skipped_simulated'),
])
def test_refunded_or_demo_paid_orders_never_queue_live_transfer(factory, shop_id, monkeypatch, refund_status, key, expected):
    monkeypatch.setattr(config, 'STRIPE_SECRET_KEY', key)
    monkeypatch.setattr(connect_gateway, 'create_transfer', lambda *a, **k: pytest.fail('unverified money moved'))
    with factory() as db:
        user = db.query(models.User).join(models.Shop).filter(models.Shop.id == shop_id).one()
        coupon = models.Coupon(shop_id=shop_id, vendor_id=user.id, coupon_type='Price', coupon_name='receipt', discount_price=138,
            valid_from=datetime.now(), valid_to=datetime.now()+timedelta(days=1))
        db.add(coupon); db.flush()
        order = models.Order(user_id=user.id, amount=138, paid_amount=138, payment_status='paid', refund_status=refund_status)
        db.add(order); db.flush()
        db.add(models.Payment(order_id=order.id, gateway='stripe', gateway_ref='pi_test_simulated', amount=138, currency='HKD', status='succeeded'))
        item = models.WalletItem(user_id=user.id, coupon_id=coupon.id, source='purchase', order_id=order.id)
        db.add(item); db.flush()
        red = models.Redemption(wallet_item_id=item.id, coupon_id=coupon.id, shop_id=shop_id, scanned_by_vendor_id=user.id)
        db.add(red); db.flush()
        assert connect_service.record_redemption(db, red)['transfer'] == expected
        db.commit()
        assert db.query(ConnectLedgerEntry).filter_by(redemption_id=red.id).count() == 0


@pytest.mark.xfail(strict=True, reason='BLOCKER: settlement reads current rate, Connect freezes redemption-time rate')
def test_fee_change_between_redemption_and_statement_uses_same_snapshot(factory, shop_id):
    from app.models_line_f import MerchantCommissionRate
    from app.routers.admin_v2 import generate_settlement
    from app.schemas_line_f import SettlementGenerateIn
    with factory() as db:
        user = db.query(models.User).join(models.Shop).filter(models.Shop.id == shop_id).one()
        coupon = models.Coupon(shop_id=shop_id, vendor_id=user.id, coupon_name='fee change',
            valid_from=datetime.utcnow(), valid_to=datetime.utcnow()+timedelta(days=1))
        db.add(coupon); db.flush()
        order = models.Order(user_id=user.id, amount=100, paid_amount=100, payment_status='paid', refund_status='none')
        db.add(order); db.flush()
        db.add(models.Payment(order_id=order.id, gateway='stripe', gateway_ref='pi_test_fee', amount=100, currency='HKD', status='succeeded'))
        item = models.WalletItem(user_id=user.id, coupon_id=coupon.id, source='purchase', order_id=order.id)
        db.add(item); db.flush()
        red = models.Redemption(wallet_item_id=item.id, coupon_id=coupon.id, shop_id=shop_id, scanned_by_vendor_id=user.id, face_value=100)
        db.add(red); db.flush()
        rate = MerchantCommissionRate(shop_id=shop_id, rate=Decimal('0.10'), set_by=user.id)
        db.add(rate); db.flush()
        result = connect_service.record_redemption(db, red); db.commit()
        assert result['commission'] == 10.0
        rate.rate = Decimal('0.20'); db.commit()
        statement = generate_settlement(SettlementGenerateIn(shop_id=shop_id, period=datetime.utcnow().strftime('%Y-%m')), user=user, db=db)
        assert Decimal(str(statement['settlement']['commission'])) == Decimal('10.00')

@pytest.mark.parametrize('reason', ['merchant_revoked', 'customer_refund'])
def test_unverified_receipt_stays_visible_then_reversal_closes_without_stripe(factory, shop_id, monkeypatch, reason):
    monkeypatch.setattr(config, 'STRIPE_SECRET_KEY', 'sk_test_fake_only')
    monkeypatch.setattr(connect_gateway, 'create_transfer', lambda *a, **k: pytest.fail('unverified transfer dispatched'))
    monkeypatch.setattr(connect_gateway, 'reverse_transfer', lambda *a, **k: pytest.fail('undispatched transfer reversed externally'))
    with factory() as db:
        user = db.query(models.User).join(models.Shop).filter(models.Shop.id == shop_id).one()
        coupon = models.Coupon(shop_id=shop_id, vendor_id=user.id, coupon_name='verification hold',
            valid_from=datetime.now(), valid_to=datetime.now()+timedelta(days=1))
        db.add(coupon); db.flush()
        order = models.Order(user_id=user.id, amount=100, paid_amount=100, payment_status='paid', refund_status='none')
        db.add(order); db.flush()
        db.add(models.Payment(order_id=order.id, gateway='stripe', gateway_ref='pi_example_valid_shape',
            amount=100, currency='HKD', status='succeeded'))
        item = models.WalletItem(user_id=user.id, coupon_id=coupon.id, source='purchase', order_id=order.id)
        db.add(item); db.flush()
        red = models.Redemption(wallet_item_id=item.id, coupon_id=coupon.id, shop_id=shop_id, scanned_by_vendor_id=user.id)
        db.add(red); db.flush()
        result = connect_service.record_redemption(db, red)
        db.commit(); entry_id, red_id = result['entry_id'], red.id
        assert connect_service.merchant_balance(db, shop_id)['pending'] == Decimal('90')
        assert connect_service.merchant_balance(db, shop_id)['available'] == Decimal('0')
        assert connect_service.retry_entry(db, db.get(ConnectLedgerEntry, entry_id))['status'] == 'payment_verification_required'
    with factory() as db:
        assert connect_service.merchant_balance(db, shop_id)['pending'] == Decimal('90')
        assert connect_service.merchant_balance(db, shop_id)['available'] == Decimal('0')
        assert connect_service.reverse_redemption(db, db.get(models.Redemption, red_id), reason)['reversed'] is True
        db.rollback()  # caller's failed transaction must not close the intent
    with factory() as db:
        assert db.get(ConnectLedgerEntry, entry_id).status == 'payment_verification_required'
        assert connect_service.reverse_redemption(db, db.get(models.Redemption, red_id), reason)['reversed'] is True
        db.commit()
    with factory() as db:
        entry = db.get(ConnectLedgerEntry, entry_id)
        assert entry.status == 'reversed' and not entry.pending_reversal
        assert connect_service.merchant_balance(db, shop_id)['pending'] == Decimal('0')
        assert connect_service.merchant_balance(db, shop_id)['available'] == Decimal('0')
        assert connect_service.reverse_redemption(db, db.get(models.Redemption, red_id), reason)['reason'] == 'already_reversed'


def test_unverified_row_with_attempt_evidence_never_assumes_no_transfer(factory, shop_id):
    with factory() as db:
        user = db.query(models.User).join(models.Shop).filter(models.Shop.id == shop_id).one()
        coupon = models.Coupon(shop_id=shop_id, vendor_id=user.id, coupon_name='inconsistent history',
            valid_from=datetime.now(), valid_to=datetime.now()+timedelta(days=1))
        db.add(coupon); db.flush()
        item = models.WalletItem(user_id=user.id, coupon_id=coupon.id); db.add(item); db.flush()
        red = models.Redemption(wallet_item_id=item.id, coupon_id=coupon.id, shop_id=shop_id, scanned_by_vendor_id=user.id)
        db.add(red); db.flush()
        entry = ConnectLedgerEntry(shop_id=shop_id, redemption_id=red.id, kind='transfer',
            status='payment_verification_required', gross_amount=10, commission=0, net_amount=10,
            first_attempt_at=datetime.utcnow(), attempt_count=1)
        db.add(entry); db.commit()
        assert connect_service.reverse_redemption(db, red, 'refunded')['reason'] == 'pending_reversal'
        db.commit()
        assert entry.status == 'payment_verification_required' and entry.pending_reversal


@pytest.mark.xfail(strict=True, reason='BLOCKER: order uniqueness prevents re-redemption after a completed cancellation')
def test_cancelled_paid_coupon_can_be_redeemed_again_without_duplicate_active_transfer(factory, shop_id):
    with factory() as db:
        user = db.query(models.User).join(models.Shop).filter(models.Shop.id == shop_id).one()
        coupon = models.Coupon(shop_id=shop_id, vendor_id=user.id, coupon_name='redeem again',
            valid_from=datetime.now(), valid_to=datetime.now()+timedelta(days=1))
        db.add(coupon); db.flush()
        order = models.Order(user_id=user.id, amount=100, paid_amount=100, payment_status='paid', refund_status='none')
        db.add(order); db.flush()
        db.add(models.Payment(order_id=order.id, gateway='stripe', gateway_ref='pi_test_reredeem', amount=100, currency='HKD', status='succeeded'))
        item = models.WalletItem(user_id=user.id, coupon_id=coupon.id, source='purchase', order_id=order.id)
        db.add(item); db.flush()
        first = models.Redemption(wallet_item_id=item.id, coupon_id=coupon.id, shop_id=shop_id, scanned_by_vendor_id=user.id)
        db.add(first); db.flush()
        assert connect_service.record_redemption(db, first)['transfer'] == 'pending'
        assert connect_service.reverse_redemption(db, first, 'merchant_revoked')['reversed'] is True
        first.revoked_at = datetime.utcnow(); db.commit()
        second = models.Redemption(wallet_item_id=item.id, coupon_id=coupon.id, shop_id=shop_id, scanned_by_vendor_id=user.id)
        db.add(second); db.flush()
        assert connect_service.record_redemption(db, second)['transfer'] == 'pending'
        db.commit()
        active = db.query(ConnectLedgerEntry).filter_by(order_id=order.id).filter(ConnectLedgerEntry.status != 'reversed').all()
        assert len(active) == 1 and active[0].redemption_id == second.id
