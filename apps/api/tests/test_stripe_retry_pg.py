"""Concurrent Stripe event ordering against an isolated real PostgreSQL DB."""
from uuid import uuid4
import time

import pytest

from app import config, models
from app.models_packages import LatePaymentRefund, Order, Package, Payment
from tests.test_fix3_row_locks import (
    BACKEND, TestingSession, client, _create_package, _register_and_login, _run_two,
)

pytestmark = pytest.mark.skipif(BACKEND != 'postgres', reason='real PostgreSQL row locks required')


@pytest.fixture
def fake_stripe(monkeypatch):
    monkeypatch.setattr(config, 'APP_ENV', 'dev')
    monkeypatch.setattr(config, 'STRIPE_SECRET_KEY', 'sk_test_fixture')
    monkeypatch.setattr(config, 'STRIPE_PUBLISHABLE_KEY', 'pk_test_fixture')
    monkeypatch.setattr('app.routers.packages.verify_webhook_signature', lambda *_: True)
    from app.routers import packages
    lock_order = packages._lock_order_with_account
    def hold_lock(*args, **kwargs):
        order = lock_order(*args, **kwargs)
        time.sleep(0.15)  # Keep the row locked while the other request arrives.
        return order
    monkeypatch.setattr(packages, '_lock_order_with_account', hold_lock)
    def intent(key, order_id, amount, method, currency):
        ref = 'pi_fake_' + uuid4().hex
        return {'gateway_ref': ref, 'client_secret': ref + '_secret_fixture',
                'amount': amount, 'currency': currency, 'method': method}
    monkeypatch.setattr('app.payments_gateway._stripe_create_intent', intent)


def _event(ref, kind):
    return {'type': kind, 'data': {'object': {
        'id': ref, 'amount': 15000, 'amount_received': 15000, 'currency': 'hkd'}}}


def _buy(client, suffix):
    user = _register_and_login(client, f'stripe-retry-pg-{suffix}@example.com')
    pkg = _create_package(client, f'Stripe retry pg {suffix}', stock=3, price=150)
    response = client.post(f"/api/v3/packages/{pkg['id']}/purchase", headers=user,
                           json={'payment_method': 'card'})
    assert response.status_code == 200, response.text
    oid, pid = response.json()['order']['id'], response.json()['payment']['id']
    with TestingSession() as db:
        ref = db.get(Payment, pid).gateway_ref
    return user, pkg['id'], oid, pid, ref


def test_failure_and_success_race_still_fulfills_once(client, fake_stripe):
    _, pkg, oid, pid, ref = _buy(client, 'fail-success')
    kinds = ('payment_intent.payment_failed', 'payment_intent.succeeded')
    results = _run_two(lambda i, c: c.post('/api/v3/payments/webhook',
        json=_event(ref, kinds[i]), headers={'Stripe-Signature': 'fixture'}))
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    with TestingSession() as db:
        assert db.get(Order, oid).payment_status == 'paid'
        assert db.get(Payment, pid).status == 'succeeded'
        assert db.get(Package, pkg).stock_left == 2
        assert db.query(models.WalletItem).filter_by(order_id=oid).count() == 1
        assert db.query(LatePaymentRefund).filter_by(order_id=oid).count() == 0


def test_failure_and_gateway_cancel_race_release_stock_once(client, fake_stripe):
    _, pkg, oid, pid, ref = _buy(client, 'fail-canceled')
    kinds = ('payment_intent.payment_failed', 'payment_intent.canceled')
    results = _run_two(lambda i, c: c.post('/api/v3/payments/webhook',
        json=_event(ref, kinds[i]), headers={'Stripe-Signature': 'fixture'}))
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    again = client.post('/api/v3/payments/webhook',
                        json=_event(ref, 'payment_intent.canceled'),
                        headers={'Stripe-Signature': 'fixture'})
    assert again.status_code == 200
    with TestingSession() as db:
        assert db.get(Order, oid).payment_status == 'failed'
        assert db.get(Payment, pid).status == 'failed'
        assert db.get(Package, pkg).stock_left == 3
        assert db.query(models.WalletItem).filter_by(order_id=oid).count() == 0
        assert db.query(LatePaymentRefund).filter_by(order_id=oid).count() == 0


def test_customer_cancel_races_success_without_double_entitlement(client, fake_stripe, monkeypatch):
    user, pkg, oid, pid, ref = _buy(client, 'cancel-success')
    calls = []
    def refund(ref, amount, **kwargs):
        calls.append(kwargs['request_key'])
        return {'gateway_ref': 're_fixture', 'payment_intent': ref,
                'status': 'succeeded', 'amount': amount, 'currency': 'HKD'}
    monkeypatch.setattr('app.late_payments.refund_payment', refund)
    results = _run_two(lambda i, c: c.post(f'/api/v3/orders/{oid}/cancel', headers=user)
        if i == 0 else c.post('/api/v3/payments/webhook',
                              json=_event(ref, 'payment_intent.succeeded'),
                              headers={'Stripe-Signature': 'fixture'}))
    assert results[1].status_code == 200, results[1].text
    with TestingSession() as db:
        order = db.get(Order, oid)
        vouchers = db.query(models.WalletItem).filter_by(order_id=oid).count()
        refunds = db.query(LatePaymentRefund).filter_by(order_id=oid, status='refunded').count()
        if results[0].status_code == 200:
            assert order.payment_status == 'cancelled' and vouchers == 0 and refunds == 1
            assert db.get(Package, pkg).stock_left == 3 and len(calls) == 1
        else:
            assert results[0].status_code == 400
            assert order.payment_status == 'paid' and vouchers == 1 and refunds == 0
            assert db.get(Package, pkg).stock_left == 2 and not calls
