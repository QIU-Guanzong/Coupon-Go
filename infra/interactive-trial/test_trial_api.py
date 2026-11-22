"""Functional isolated-trial acceptance. Run this file in its own process."""
import os
from pathlib import Path
import secrets
import subprocess
import sys
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


@pytest.fixture(scope='module')
def gateway(tmp_path_factory):
    root = tmp_path_factory.mktemp('coupon-interactive-trial')
    (root / 'uploads').mkdir()
    (root / 'merchant-files').mkdir()
    settings = {
        'TRIAL_STATE_DIR': str(root), 'DATABASE_URL': f'sqlite:///{root}/template.db',
        'APP_ENV': 'dev', 'SCHEDULER_ENABLED': '0', 'SEED_ON_STARTUP': '0',
        'STRIPE_CONNECT_ENABLED': '1', 'UPLOAD_DIR': str(root / 'uploads'),
        'MERCHANT_FILE_DIR': str(root / 'merchant-files'),
        'TRIAL_PUBLIC_ORIGIN': 'http://testserver', 'CORS_ORIGINS': 'http://testserver',
        'TRIAL_COOKIE_PATH': '/', 'TRIAL_COOKIE_SECURE': '0',
        'SECRET_KEY': secrets.token_urlsafe(48),
    }
    forbidden = ('STRIPE_SECRET_KEY', 'STRIPE_PUBLISHABLE_KEY', 'STRIPE_WEBHOOK_SECRET',
                 'DEEPSEEK_API_KEY', 'REDIS_URL', 'COUPON_WEB_DIR', 'TRIAL_QBOT_PROXY_URL')
    for key in forbidden:
        os.environ.pop(key, None)
    os.environ.update(settings)
    sys.path.insert(0, str(REPO / 'apps/api'))
    sys.path.insert(0, str(HERE))
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join([str(REPO / 'apps/api'), str(HERE),
                                         env.get('PYTHONPATH', '')])
    subprocess.run([sys.executable, str(HERE / 'prepare_template.py')], env=env, check=True,
                   capture_output=True, text=True)
    import trial_api
    return trial_api


def role(client, name):
    response = client.post('/api/v3/trial/role', json={'role': name})
    assert response.status_code == 200, response.text
    return {'Authorization': 'Bearer ' + response.json()['access_token']}


def test_browser_sessions_are_independent_and_complete_business_journey(gateway):
    a, b = TestClient(gateway.app), TestClient(gateway.app)
    assert a.post('/api/v3/trial/session').json()['reset'] is True
    assert b.post('/api/v3/trial/session').json()['reset'] is True
    assert a.post('/api/v3/trial/session').json()['reset'] is False
    assert a.get('/api/v3/home').status_code == 200  # anonymous browsing
    ac, bc = role(a, 'customer'), role(b, 'customer')
    assert b.get('/api/v3/me', headers=ac).status_code == 401
    claimed = a.post('/api/v3/new-favorite-coupon', params={'coupon_id': 1}, headers=ac)
    assert claimed.status_code == 200, claimed.text
    assert not b.get('/api/v3/get-favorite-coupons-list', headers=bc).json()['items']

    # Identical database IDs and request refs in two sessions must never hit
    # the same process-global mock-provider idempotency entry.
    provider_ids = []
    for client, customer in ((a, ac), (b, bc)):
        initial = client.get('/api/v3/package-details', params={'package_id': 1}).json()['package']['stock_left']
        created = client.post('/api/v3/packages/1/purchase', headers=customer,
                              json={'payment_method': 'card', 'quantity': 1, 'client_ref': 'same-ref'})
        assert created.status_code == 200, created.text
        order_id = created.json()['order']['id']
        paid = client.post(f'/api/v3/orders/{order_id}/demo-payment', headers=customer)
        assert paid.status_code == 200, paid.text
        item_id = paid.json()['order']['wallet_item_id']
        qr = client.post('/api/v3/store-scaned-qr-values', params={'wallet_item_id': item_id}, headers=customer)
        assert qr.status_code == 200, qr.text
        vendor = role(client, 'vendor')
        scan = client.post('/api/v3/new-scan-coupon', headers=vendor, json={'code': qr.json()['code']})
        assert scan.status_code == 200, scan.text
        balance = client.get('/api/v3/merchant/connect/balance', params={'shop_id': 1}, headers=vendor)
        assert balance.status_code == 200, balance.text
        assert balance.json()['connect_mode'] == 'simulated'
        assert balance.json()['available'] == pytest.approx(268.2)
        entries = client.get('/api/v3/merchant/connect/ledger', params={'shop_id': 1}, headers=vendor)
        if entries.status_code == 200:
            provider_ids.append(entries.json())
        admin = role(client, 'admin')
        settlement = client.post('/api/v3/admin/settlements/generate', headers=admin,
            json={'shop_id': 1, 'period': datetime.now(timezone.utc).strftime('%Y-%m')})
        assert settlement.status_code == 201, settlement.text
        assert settlement.json()['settlement']['gross_amount'] == pytest.approx(298)
        revoked = client.post(f"/api/v3/redemptions/{scan.json()['redemption_id']}/revoke", headers=vendor)
        assert revoked.status_code == 200, revoked.text
        refund = client.post(f'/api/v3/orders/{order_id}/refund', headers=customer, json={})
        assert refund.status_code == 200, refund.text
        assert refund.json()['refund']['status'] == 'refunded'
        details = client.get(f'/api/v3/orders/{order_id}', headers=customer).json()['order']
        assert details['wallet_status'] == 3
        assert client.get('/api/v3/package-details', params={'package_id': 1}).json()['package']['stock_left'] == initial

    # No cross-session stock/settlement or provider operation is shared.
    assert a.get('/api/v3/me', headers=bc).status_code == 401
    assert len(gateway.connect_gateway._SIM_TRANSFERS) >= 2
    keys = list(gateway.connect_gateway._SIM_IDEMPOTENCY)
    assert len({key.split(':', 1)[0] for key in keys if ':' in key}) >= 2


def test_trial_restricts_real_accounts_and_external_side_effects(gateway):
    client = TestClient(gateway.app)
    assert client.get('/api/v3/home').status_code == 401
    assert client.post('/api/v3/trial/session').status_code == 200
    assert client.post('/api/v3/new-register', json={}).status_code == 403
    assert client.post('/api/v3/login', json={}).status_code == 403
    assert client.get('/api/v3/_dev/otp').status_code == 403
    assert client.post('/api/v3/payments/webhook', json={}).status_code == 403
    assert client.post('/api/v3/merchant-files', content=b'data').status_code == 403
    assert client.post('/api/v3/qbot/chat', json={}).status_code == 503
    assert client.post('/api/v3/trial/role', json={'role': 'anything'}).status_code == 400
    assert client.post('/api/v3/trial/role', json={'role': 'admin'},
                       headers={'origin': 'https://other.example'}).status_code == 403
    assert client.post('/api/v3/trial/role', content=b'x' * 16385).status_code == 413


def test_expiry_resets_a_browser_without_touching_another(gateway, monkeypatch):
    a, b = TestClient(gateway.app), TestClient(gateway.app)
    a.post('/api/v3/trial/session'); b.post('/api/v3/trial/session')
    a_token, b_token = role(a, 'customer'), role(b, 'customer')
    raw = a.cookies.get(gateway.COOKIE)
    sid, path = gateway._session_file(raw)
    # Expire only the first browser's file; no production or other trial data.
    old = gateway.time.time() - gateway.TTL - 10
    os.utime(path, (old, old))
    assert a.get('/api/v3/me', headers=a_token).status_code == 401
    assert a.post('/api/v3/trial/session').json()['reset'] is True
    assert a.get('/api/v3/me', headers=a_token).status_code == 401
    assert b.get('/api/v3/me', headers=b_token).status_code == 200


def test_restricted_coupon_requires_the_selected_branch(gateway):
    client = TestClient(gateway.app)
    client.post('/api/v3/trial/session')
    sid, path = gateway._session_file(client.cookies.get(gateway.COOKIE))
    engine, db = gateway._db(path)
    try:
        branch = gateway.models.Branch(shop_id=1, name='示例分店', address='示例地址')
        db.add(branch)
        db.flush()
        db.add(gateway.models.CouponBranch(coupon_id=1, branch_id=branch.id))
        db.commit()
        branch_id = branch.id
    finally:
        db.close(); engine.dispose()
    customer = role(client, 'customer')
    claimed = client.post('/api/v3/new-favorite-coupon', params={'coupon_id': 1}, headers=customer)
    assert claimed.status_code == 200, claimed.text
    item_id = claimed.json()['wallet_item_id']
    qr = client.post('/api/v3/store-scaned-qr-values', params={'wallet_item_id': item_id}, headers=customer)
    vendor = role(client, 'vendor')
    prepare = client.post('/api/v3/new-scan-coupon/prepare', headers=vendor, json={'code': qr.json()['code']})
    assert prepare.status_code == 200 and prepare.json()['branch_selection_required'] is True
    rejected = client.post('/api/v3/new-scan-coupon', headers=vendor, json={'code': qr.json()['code']})
    assert rejected.status_code == 400
    assert client.get(f'/api/v3/wallet/items/{item_id}/status', headers=customer).json()['status'] == 1
    prepare = client.post('/api/v3/new-scan-coupon/prepare', headers=vendor,
                          json={'code': qr.json()['code'], 'branch_id': branch_id})
    assert prepare.json()['can_redeem'] is True
    used = client.post('/api/v3/new-scan-coupon', headers=vendor,
        json={'code': qr.json()['code'], 'branch_id': branch_id, 'prepare_token': prepare.json()['prepare_token']})
    assert used.status_code == 200, used.text


def test_previous_process_cookie_is_invalidated(gateway, monkeypatch):
    client = TestClient(gateway.app)
    client.post('/api/v3/trial/session')
    customer = role(client, 'customer')
    monkeypatch.setattr(gateway, 'BOOT', '0' * 32)
    assert client.get('/api/v3/me', headers=customer).status_code == 401
    assert client.post('/api/v3/trial/session').json()['reset'] is True
    assert client.get('/api/v3/me', headers=customer).status_code == 401


def test_qbot_uses_only_the_existing_local_quota_gateway(gateway, monkeypatch):
    client = TestClient(gateway.app)
    client.post('/api/v3/trial/session')
    role(client, 'customer')
    calls = []
    class Response:
        status_code = 200
        def json(self):
            return {'reply': '來自現有服務'}
    class LocalProxy:
        def __init__(self, **kwargs):
            assert kwargs['trust_env'] is False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, *, content, headers):
            calls.append((url, headers, content))
            return Response()
    monkeypatch.setattr(gateway, 'QBOT_URL', 'http://127.0.0.1:18760/api/v3/qbot/chat')
    monkeypatch.setattr(gateway.httpx, 'AsyncClient', LocalProxy)
    for _ in range(5):
        result = client.post('/api/v3/qbot/chat', json={'message': '優惠'})
        assert result.status_code == 200 and result.json()['reply'] == '來自現有服務'
    assert client.post('/api/v3/qbot/chat', json={'message': '優惠'}).status_code == 429
    assert len(calls) == 5
    assert all(url == gateway.QBOT_URL and headers == {'Content-Type': 'application/json'}
               for url, headers, _ in calls)
