"""Web test payment: no test card or provider result grants a voucher by itself."""
from uuid import uuid4
from datetime import timedelta

import pytest
from tests.test_packages import client, _register_and_login, _create_package
from app import config, models
from app.models_packages import LatePaymentRefund, Order, Package, Payment
from app.timeutil import utcnow
from tests.test_packages import TestingSession


@pytest.fixture
def stripe_test(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "dev")
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_fixture")
    monkeypatch.setattr(config, "STRIPE_PUBLISHABLE_KEY", "pk_test_fixture")

    def fake_intent(key, order_id, amount, method, currency):
        ref = "pi_fake_" + uuid4().hex
        return {"gateway_ref": ref, "client_secret": ref + "_secret_fixture",
                "amount": amount, "currency": currency, "method": method}
    monkeypatch.setattr("app.payments_gateway._stripe_create_intent", fake_intent)


def test_payment_configuration_is_honest(client, monkeypatch):
    h = _register_and_login(client, 'checkout-options@example.com')
    monkeypatch.setattr(config, "APP_ENV", "prod")
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "")
    monkeypatch.setattr(config, "STRIPE_PUBLISHABLE_KEY", "")
    assert client.get('/api/v3/payment-options').json() == {
        "mode": "unavailable", "card": False, "fps": False}
    assert client.post('/api/v3/packages/1/purchase', json={"payment_method": "card"}, headers=h).status_code == 503


def test_current_owner_can_resume_test_payment_and_server_confirms(client, stripe_test, monkeypatch):
    owner = _register_and_login(client, 'checkout-owner@example.com')
    other = _register_and_login(client, 'checkout-other@example.com')
    pkg = _create_package(client, name='Stripe web test', price=150)
    assert client.get('/api/v3/payment-options').json() == {
        'mode': 'stripe_test', 'card': True, 'fps': False}
    assert client.post(f"/api/v3/packages/{pkg['id']}/purchase", json={'payment_method': 'fps'}, headers=owner).status_code == 400
    bought = client.post(f"/api/v3/packages/{pkg['id']}/purchase",
                         json={'payment_method': 'card', 'client_ref': 'stripe-web-resume'}, headers=owner)
    assert bought.status_code == 200, bought.text
    order_id, payment_id = bought.json()['order']['id'], bought.json()['payment']['id']
    path = f'/api/v3/orders/{order_id}/stripe-test-payment'
    assert client.get(path, headers=other).status_code == 403
    data = client.get(path, headers=owner)
    assert data.status_code == 200 and data.headers['cache-control'] == 'no-store'
    assert data.json()['payment_id'] == payment_id
    assert data.json()['client_secret'] == bought.json()['client_secret']
    assert client.get(path, headers=owner).json() == data.json()

    def intent(status, amount=15000, currency='HKD', suffix=''):
        return lambda ref: {'gateway_ref': ref + suffix, 'amount': amount,
                            'currency': currency, 'status': status}
    confirm = f'/api/v3/orders/{order_id}/stripe-test-confirm'
    monkeypatch.setattr('app.routers.packages.retrieve_payment_intent', intent('processing'))
    assert client.post(confirm, json={'payment_id': payment_id}, headers=owner).json()['status'] == 'pending'
    for bad in (intent('succeeded', amount=14999), intent('succeeded', currency='USD'),
                intent('succeeded', suffix='_other')):
        monkeypatch.setattr('app.routers.packages.retrieve_payment_intent', bad)
        assert client.post(confirm, json={'payment_id': payment_id}, headers=owner).status_code == 409
    with TestingSession() as db:
        assert db.query(models.WalletItem).filter_by(order_id=order_id).count() == 0
    monkeypatch.setattr('app.routers.packages.retrieve_payment_intent', intent('succeeded'))
    result = client.post(confirm, json={'payment_id': payment_id}, headers=owner)
    assert result.status_code == 200 and result.json()['status'] == 'paid'
    assert client.post(confirm, json={'payment_id': payment_id}, headers=owner).json()['status'] == 'paid'
    assert client.get(path, headers=owner).status_code == 409
    with TestingSession() as db:
        assert db.query(models.WalletItem).filter_by(order_id=order_id).count() == 1


def test_old_attempt_cannot_confirm_new_payment(client, stripe_test):
    owner = _register_and_login(client, 'checkout-old-attempt@example.com')
    pkg = _create_package(client, name='Stripe attempt retry', price=120)
    result = client.post(f"/api/v3/packages/{pkg['id']}/purchase", json={'payment_method': 'card'}, headers=owner).json()
    order_id, old_id = result['order']['id'], result['payment']['id']
    with TestingSession() as db:
        order, payment = db.get(Order, order_id), db.get(Payment, old_id)
        order.payment_status, payment.status = 'failed', 'failed'
        # A failed attempt releases its reservation before a retry.
        package = db.get(__import__('app.models_packages', fromlist=['Package']).Package, pkg['id'])
        package.stock_left += 1
        db.commit()
    retry = client.post(f'/api/v3/orders/{order_id}/retry-payment', json={'payment_method': 'card'}, headers=owner)
    assert retry.status_code == 200, retry.text
    new_id = retry.json()['payment']['id']
    assert new_id != old_id
    assert client.post(f'/api/v3/orders/{order_id}/stripe-test-confirm', json={'payment_id': old_id}, headers=owner).status_code == 409
    assert client.get(f'/api/v3/orders/{order_id}/stripe-test-payment', headers=owner).json()['payment_id'] == new_id


def test_gateway_success_with_wrong_amount_never_issues_voucher(client, stripe_test, monkeypatch):
    owner = _register_and_login(client, 'checkout-amount@example.com')
    pkg = _create_package(client, name='Stripe mismatch', price=150)
    result = client.post(f"/api/v3/packages/{pkg['id']}/purchase", json={'payment_method': 'card'}, headers=owner).json()
    order_id, payment_id = result['order']['id'], result['payment']['id']
    with TestingSession() as db:
        gateway_ref = db.get(Payment, payment_id).gateway_ref
    monkeypatch.setattr('app.routers.packages.retrieve_payment_intent',
                        lambda ref: {'gateway_ref': ref, 'status': 'succeeded', 'amount': 14999, 'currency': 'HKD'})
    assert client.post(f'/api/v3/orders/{order_id}/confirm-payment',
                       json={'outcome': 'succeeded'}, headers=owner).status_code == 409
    monkeypatch.setattr('app.routers.packages.verify_webhook_signature', lambda *_: True)
    event = {'type': 'payment_intent.succeeded', 'data': {'object': {
        'id': gateway_ref, 'amount': 15000, 'amount_received': 14999, 'currency': 'hkd'}}}
    assert client.post('/api/v3/payments/webhook', json=event,
                       headers={'Stripe-Signature': 'fixture'}).status_code == 409
    with TestingSession() as db:
        assert db.get(Order, order_id).payment_status == 'pending'
        assert db.query(models.WalletItem).filter_by(order_id=order_id).count() == 0


def _stripe_event(client, ref, kind, **fields):
    return client.post('/api/v3/payments/webhook', json={
        'type': kind, 'data': {'object': {'id': ref, **fields}}},
        headers={'Stripe-Signature': 'fixture'})


def test_declined_card_can_retry_same_intent_without_releasing_stock(client, stripe_test, monkeypatch):
    """A decline webhook is an attempt failure, not a terminal intent."""
    monkeypatch.setattr('app.routers.packages.verify_webhook_signature', lambda *_: True)
    owner = _register_and_login(client, 'stripe-decline-retry@example.com')
    pkg = _create_package(client, name='Stripe decline retry', stock=5, price=150)
    bought = client.post(f"/api/v3/packages/{pkg['id']}/purchase", json={
        'payment_method': 'card'}, headers=owner).json()
    oid, pid = bought['order']['id'], bought['payment']['id']
    with TestingSession() as db:
        ref = db.get(Payment, pid).gateway_ref
        assert db.get(Package, pkg['id']).stock_left == 4
    for _ in range(2):
        result = _stripe_event(client, ref, 'payment_intent.payment_failed',
                               status='requires_payment_method', amount=15000, currency='hkd')
        assert result.status_code == 200 and result.json()['outcome'] == 'retryable'
    with TestingSession() as db:
        assert db.get(Order, oid).payment_status == 'pending'
        assert db.get(Payment, pid).status == 'requires_payment'
        assert db.get(Package, pkg['id']).stock_left == 4
        assert db.query(models.WalletItem).filter_by(order_id=oid).count() == 0
    assert client.get(f'/api/v3/orders/{oid}/stripe-test-payment', headers=owner).status_code == 200
    for _ in range(2):
        success = _stripe_event(client, ref, 'payment_intent.succeeded',
                                amount=15000, amount_received=15000, currency='hkd')
        assert success.status_code == 200
    # A delayed or replayed failure must never undo a later success.
    assert _stripe_event(client, ref, 'payment_intent.payment_failed',
                         status='requires_payment_method').status_code == 200
    with TestingSession() as db:
        assert db.get(Order, oid).payment_status == 'paid'
        assert db.get(Payment, pid).status == 'succeeded'
        assert db.get(Package, pkg['id']).stock_left == 4
        assert db.query(models.WalletItem).filter_by(order_id=oid).count() == 1
        assert db.query(LatePaymentRefund).filter_by(order_id=oid).count() == 0


@pytest.mark.parametrize('ending', ['canceled', 'user_cancel', 'timeout'])
def test_declined_attempt_releases_stock_once_on_terminal_event(client, stripe_test, monkeypatch, ending):
    monkeypatch.setattr('app.routers.packages.verify_webhook_signature', lambda *_: True)
    owner = _register_and_login(client, f'stripe-decline-{ending}@example.com')
    pkg = _create_package(client, name=f'Stripe decline {ending}', stock=5, price=150)
    bought = client.post(f"/api/v3/packages/{pkg['id']}/purchase", json={
        'payment_method': 'card'}, headers=owner).json()
    oid, pid = bought['order']['id'], bought['payment']['id']
    with TestingSession() as db:
        ref = db.get(Payment, pid).gateway_ref
    assert _stripe_event(client, ref, 'payment_intent.payment_failed',
                         status='requires_payment_method').status_code == 200
    if ending == 'timeout':
        with TestingSession() as db:
            db.get(Payment, pid).created_at = utcnow() - timedelta(minutes=config.PAYMENT_TIMEOUT_MINUTES + 1)
            db.commit()
        kind, expected = 'payment_intent.payment_failed', 'timeout'
    elif ending == 'canceled':
        kind, expected = 'payment_intent.canceled', 'failed'
    else:
        assert client.post(f'/api/v3/orders/{oid}/cancel', headers=owner).status_code == 200
        kind, expected = 'payment_intent.payment_failed', 'cancelled'
    for _ in range(2):
        assert _stripe_event(client, ref, kind, status=ending).status_code == 200
    with TestingSession() as db:
        assert db.get(Order, oid).payment_status == expected
        assert db.get(Payment, pid).status == expected
        assert db.get(Package, pkg['id']).stock_left == 5
        assert db.query(models.WalletItem).filter_by(order_id=oid).count() == 0
    if ending != 'canceled':
        # Stripe can report a success after our reservation timed out or the
        # customer canceled locally. It must follow the existing compensation
        # path, not resurrect the order or release stock a second time.
        refunds = []
        def refund(ref, amount, **kwargs):
            refunds.append((ref, amount, kwargs['request_key']))
            return {'gateway_ref': 're_fixture', 'payment_intent': ref,
                    'status': 'succeeded', 'amount': amount, 'currency': 'HKD'}
        monkeypatch.setattr('app.late_payments.refund_payment', refund)
        for _ in range(2):
            result = _stripe_event(client, ref, 'payment_intent.succeeded',
                                   amount=15000, amount_received=15000, currency='hkd')
            assert result.status_code == 200
            assert result.json()['late_payment_refund']['status'] == 'refunded'
        assert len(refunds) == 1
        with TestingSession() as db:
            assert db.get(Order, oid).payment_status == expected
            assert db.get(Package, pkg['id']).stock_left == 5
            assert db.query(models.WalletItem).filter_by(order_id=oid).count() == 0
            assert db.query(LatePaymentRefund).filter_by(order_id=oid, status='refunded').count() == 1
