"""Late provider success must never be lost or refund a different attempt."""
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app import late_payments, models
from app.models_packages import LatePaymentRefund, Order, Package, Payment
from app.payments_gateway import GatewayError
from app.timeutil import utcnow
from tests.test_packages import (client, TestingSession, _create_package, _pay,
                                 _purchase, _register_and_login)


def _callback(client, ref, **fields):
    return client.post('/api/v3/payments/webhook', json={
        'type': 'payment_intent.succeeded', 'data': {'object': {'id': ref, **fields}}})


def _payment(order_id, first=True):
    with TestingSession() as db:
        payments = db.query(Payment).filter_by(order_id=order_id).order_by(Payment.id).all()
        return payments[0 if first else -1].gateway_ref


def _cancelled(client, suffix):
    headers = _register_and_login(client, f'late-{suffix}@example.com')
    pkg = _create_package(client, name=f'Late {suffix}', stock=3)
    order_id = _purchase(client, headers, pkg['id'])['order']['id']
    ref = _payment(order_id)
    response = client.post(f'/api/v3/orders/{order_id}/cancel', headers=headers)
    assert response.status_code == 200, response.text
    return headers, pkg, order_id, ref


def test_cancelled_late_success_replays_once_and_keeps_stock(client, monkeypatch):
    headers, pkg, oid, ref = _cancelled(client, 'cancel')
    orig = late_payments.refund_payment
    calls = []
    def refund(*args, **kwargs):
        calls.append(kwargs['request_key'])
        return orig(*args, **kwargs)
    monkeypatch.setattr(late_payments, 'refund_payment', refund)
    first = _callback(client, ref)
    second = _callback(client, ref)
    assert first.status_code == second.status_code == 200
    assert first.json()['late_payment_refund']['status'] == 'refunded'
    assert len(calls) == 1
    with TestingSession() as db:
        order = db.get(Order, oid)
        assert order.payment_status == 'cancelled' and order.refund_status == 'none'
        assert db.get(Package, pkg['id']).stock_left == 3
        assert db.query(models.WalletItem).filter_by(order_id=oid).count() == 0
        assert db.query(LatePaymentRefund).filter_by(order_id=oid, status='refunded').count() == 1
    info = client.get(f'/api/v3/orders/{oid}', headers=headers).json()['order']
    assert info['late_payment_refunds'][0]['status'] == 'refunded'


def test_old_timed_out_attempt_is_refunded_without_voiding_new_paid_order(client):
    h = _register_and_login(client, 'late-old-attempt@example.com')
    pkg = _create_package(client, name='Late previous attempt', stock=3)
    oid = _purchase(client, h, pkg['id'])['order']['id']
    old_ref = _payment(oid)
    _pay(client, h, oid, 'timeout')
    retry = client.post(f'/api/v3/orders/{oid}/retry-payment', headers=h, json={'payment_method': 'fps'})
    assert retry.status_code == 200, retry.text
    _pay(client, h, oid)
    assert _payment(oid, first=False) != old_ref
    response = _callback(client, old_ref)
    assert response.json()['late_payment_refund']['status'] == 'refunded'
    with TestingSession() as db:
        order = db.get(Order, oid)
        assert order.payment_status == 'paid' and order.refund_status == 'none'
        assert db.get(Package, pkg['id']).stock_left == 2
        assert db.query(models.WalletItem).filter_by(order_id=oid, status=1).count() == 1


def test_gateway_timeout_retries_with_same_key(client, monkeypatch):
    _, _, oid, ref = _cancelled(client, 'timeout')
    orig = late_payments.refund_payment
    calls = []
    def refund(*args, **kwargs):
        calls.append(kwargs['request_key'])
        result = orig(*args, **kwargs)
        if len(calls) == 1:
            raise GatewayError('response lost after gateway processed request')
        return result
    monkeypatch.setattr(late_payments, 'refund_payment', refund)
    assert _callback(client, ref).json()['late_payment_refund']['status'] == 'pending'
    with TestingSession() as db:
        assert late_payments.sweep(db)['refunded'] == 1
    assert len(calls) == 2 and calls[0] == calls[1]
    with TestingSession() as db:
        assert db.query(LatePaymentRefund).filter_by(order_id=oid, status='refunded').count() == 1


def test_gateway_success_then_database_commit_fails_is_retried_same_key(client, monkeypatch):
    _, _, oid, ref = _cancelled(client, 'db-failure')
    orig_refund, orig_commit = late_payments.refund_payment, Session.commit
    calls, raised = [], []
    def refund(*args, **kwargs):
        calls.append(kwargs['request_key'])
        return orig_refund(*args, **kwargs)
    def fail_once(db):
        if not raised and any(isinstance(obj, LatePaymentRefund) and obj.status == 'refunded'
                              for obj in db.identity_map.values()):
            raised.append(True)
            raise RuntimeError('simulated commit failure after gateway success')
        return orig_commit(db)
    monkeypatch.setattr(late_payments, 'refund_payment', refund)
    monkeypatch.setattr(Session, 'commit', fail_once)
    with pytest.raises(RuntimeError, match='simulated commit failure'):
        _callback(client, ref)
    with TestingSession() as db:
        assert db.query(LatePaymentRefund).filter_by(order_id=oid).one().status == 'pending'
    assert _callback(client, ref).json()['late_payment_refund']['status'] == 'refunded'
    assert len(calls) == 2 and calls[0] == calls[1]


def test_mismatched_provider_amount_or_currency_requires_manual_reconciliation(client, monkeypatch):
    _, _, oid, ref = _cancelled(client, 'mismatch')
    def forbidden(*args, **kwargs):
        raise AssertionError('must not refund mismatched payment')
    monkeypatch.setattr(late_payments, 'refund_payment', forbidden)
    response = _callback(client, ref, amount_received=1, currency='USD')
    assert response.json()['late_payment_refund']['status'] == 'manual_review'
    with TestingSession() as db:
        entry = db.query(LatePaymentRefund).filter_by(order_id=oid).one()
        assert entry.last_error == 'payment_event_mismatch'


def test_provider_amount_and_amount_received_must_both_match(client, monkeypatch):
    _, _, oid, ref = _cancelled(client, 'inconsistent-amount')
    monkeypatch.setattr(late_payments, 'refund_payment', lambda *a, **kw: pytest.fail('unexpected refund'))
    response = _callback(client, ref, amount_received=15000, amount=20000, currency='hkd')
    assert response.json()['late_payment_refund']['status'] == 'manual_review'
    with TestingSession() as db:
        assert db.query(LatePaymentRefund).filter_by(order_id=oid).one().last_error == 'payment_event_mismatch'


def test_pending_refund_lookup_must_return_same_gateway_reference(client, monkeypatch):
    _, _, oid, ref = _cancelled(client, 'refund-reference')
    def pending(payment_ref, amount, **kwargs):
        return {'gateway_ref': 're_first', 'payment_intent': payment_ref,
                'amount': amount, 'currency': 'HKD', 'status': 'pending'}
    def wrong_ref(gateway_ref):
        return {'gateway_ref': 're_other', 'payment_intent': ref,
                'amount': 150, 'currency': 'HKD', 'status': 'succeeded'}
    monkeypatch.setattr(late_payments, 'refund_payment', pending)
    monkeypatch.setattr(late_payments, 'retrieve_refund', wrong_ref)
    assert _callback(client, ref).json()['late_payment_refund']['status'] == 'pending'
    with TestingSession() as db:
        assert late_payments.sweep(db)['manual_review'] == 1
        assert db.query(LatePaymentRefund).filter_by(order_id=oid).one().last_error == 'refund_result_mismatch'


def test_expired_idempotency_window_requires_manual_reconciliation(client, monkeypatch):
    _, _, oid, ref = _cancelled(client, 'window')
    monkeypatch.setattr(late_payments, 'refund_payment', lambda *a, **kw: (_ for _ in ()).throw(GatewayError()))
    assert _callback(client, ref).json()['late_payment_refund']['status'] == 'pending'
    with TestingSession() as db:
        entry = db.query(LatePaymentRefund).filter_by(order_id=oid).one()
        entry.created_at = utcnow() - timedelta(hours=24)
        db.commit()
        assert late_payments.sweep(db)['manual_review'] == 1


def test_finance_can_only_confirm_matching_existing_refund(client, monkeypatch):
    _, _, oid, ref = _cancelled(client, 'manual-proof')
    monkeypatch.setattr(late_payments, 'refund_payment', lambda *a, **kw: (_ for _ in ()).throw(GatewayError()))
    callback = _callback(client, ref)
    entry_id = callback.json()['late_payment_refund']['id']
    admin = client.post('/api/v3/login', json={'email': 'admin@coupon-go.example', 'password': 'Demo123!'})
    auth = {'Authorization': f"Bearer {admin.json()['access_token']}"}
    def retrieved(gateway_ref):
        return {'gateway_ref': gateway_ref, 'payment_intent': ref,
                'currency': 'HKD', 'amount': 149, 'status': 'succeeded'}
    monkeypatch.setattr(late_payments, 'retrieve_refund', retrieved)
    wrong = client.post(f'/api/v3/admin/late-payment-refunds/{entry_id}/verify',
                        params={'gateway_ref': 're_manual'}, headers=auth)
    assert wrong.status_code == 409
    def correct(gateway_ref):
        return {**retrieved(gateway_ref), 'amount': 150}
    monkeypatch.setattr(late_payments, 'retrieve_refund', correct)
    verified = client.post(f'/api/v3/admin/late-payment-refunds/{entry_id}/verify',
                           params={'gateway_ref': 're_manual'}, headers=auth)
    assert verified.status_code == 200 and verified.json()['status'] == 'refunded'
    with TestingSession() as db:
        assert db.query(LatePaymentRefund).filter_by(order_id=oid).one().gateway_ref == 're_manual'
