"""Real PostgreSQL concurrency for late payment compensation."""
import threading
import time

import pytest

from app import late_payments, models
from app.models_packages import LatePaymentRefund, Order, Package, Payment
from tests.test_fix3_row_locks import (BACKEND, TestingSession, client, _create_package,
                                       _register_and_login, _run_two)


@pytest.mark.skipif(BACKEND != 'postgres', reason='real PostgreSQL row locks required')
def test_duplicate_late_callbacks_send_one_refund(client, monkeypatch):
    h = _register_and_login(client, 'late-pg-duplicate@example.com')
    pkg = _create_package(client, 'late pg duplicate', stock=3)
    response = client.post(f"/api/v3/packages/{pkg['id']}/purchase", headers=h, json={'payment_method': 'fps'})
    oid = response.json()['order']['id']
    with TestingSession() as db:
        ref = db.query(Payment).filter_by(order_id=oid).one().gateway_ref
    assert client.post(f'/api/v3/orders/{oid}/cancel', headers=h).status_code == 200
    original = late_payments.refund_payment
    calls = []
    call_lock = threading.Lock()
    def delayed(*args, **kwargs):
        with call_lock:
            calls.append(kwargs['request_key'])
        time.sleep(0.2)
        return original(*args, **kwargs)
    monkeypatch.setattr(late_payments, 'refund_payment', delayed)
    payload = {'type': 'payment_intent.succeeded', 'data': {'object': {'id': ref}}}
    results = _run_two(lambda i, tc: tc.post('/api/v3/payments/webhook', json=payload))
    assert all(r.status_code == 200 for r in results)
    assert len(calls) == 1
    with TestingSession() as db:
        assert db.query(LatePaymentRefund).filter_by(order_id=oid, status='refunded').count() == 1
        assert db.get(Order, oid).payment_status == 'cancelled'
        assert db.get(Package, pkg['id']).stock_left == 3
        assert db.query(models.WalletItem).filter_by(order_id=oid).count() == 0


@pytest.mark.skipif(BACKEND != 'postgres', reason='real PostgreSQL row locks required')
def test_cancel_racing_success_never_issues_and_refunds_same_payment(client):
    h = _register_and_login(client, 'late-pg-cancel-race@example.com')
    pkg = _create_package(client, 'late pg race', stock=3)
    response = client.post(f"/api/v3/packages/{pkg['id']}/purchase", headers=h, json={'payment_method': 'fps'})
    oid = response.json()['order']['id']
    with TestingSession() as db:
        ref = db.query(Payment).filter_by(order_id=oid).one().gateway_ref
    payload = {'type': 'payment_intent.succeeded', 'data': {'object': {'id': ref}}}
    cancel, webhook = _run_two(lambda i, tc: tc.post(f'/api/v3/orders/{oid}/cancel', headers=h)
                               if i == 0 else tc.post('/api/v3/payments/webhook', json=payload))
    assert webhook.status_code == 200
    with TestingSession() as db:
        order = db.get(Order, oid)
        count = db.query(models.WalletItem).filter_by(order_id=oid).count()
        late = db.query(LatePaymentRefund).filter_by(order_id=oid, status='refunded').count()
        if cancel.status_code == 200:
            assert order.payment_status == 'cancelled' and count == 0 and late == 1
            assert db.get(Package, pkg['id']).stock_left == 3
        else:
            assert cancel.status_code == 400 and order.payment_status == 'paid'
            assert count == 1 and late == 0 and db.get(Package, pkg['id']).stock_left == 2
