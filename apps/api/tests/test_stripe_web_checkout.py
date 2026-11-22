"""Web test payment: no test card or provider result grants a voucher by itself."""
from uuid import uuid4

import pytest
from tests.test_packages import client, _register_and_login, _create_package
from app import config, models
from app.models_packages import Order, Payment
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
