"""Functional isolated-trial acceptance. Run this file in its own process."""
import os
from pathlib import Path
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone

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
    options = a.get('/api/v3/payment-options')
    assert options.status_code == 200 and options.json()['mode'] == 'demo'
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
    from app.models_packages import Package
    _, path = gateway._session_file(client.cookies.get(gateway.COOKIE))
    engine, db = gateway._db(path)
    try:
        package = db.query(Package).filter_by(person_count=2, status="active").first()
        package.package_price = 0.73
        package_id = package.id
        db.commit()
    finally:
        db.close()
        engine.dispose()
    prompt = {"message": "比較雙人套餐，總預算HK$1"}
    match = client.post("/api/v3/qbot/chat", json=prompt).json()
    assert [offer["id"] for offer in match["offers"]] == [package_id]
    assert "HK$0.73" in match["answer"]
    other = trial_browser(gateway)
    assert other.post("/api/v3/qbot/chat", json=prompt).json()["offers"] == []
    for _ in range(4):
        result = client.post('/api/v3/qbot/chat', json={'message': '優惠'})
        assert result.status_code == 200 and result.json()['reply'] == '來自現有服務'
    assert client.post('/api/v3/qbot/chat', json={'message': '優惠'}).status_code == 429
    assert len(calls) == 6
    assert all(url == gateway.QBOT_URL and headers == {'Content-Type': 'application/json'}
               for url, headers, _ in calls)


def trial_browser(gateway):
    client = TestClient(gateway.app)
    assert client.post('/api/v3/trial/session').status_code == 200
    return client


def test_review_and_point_exchange_share_only_current_browser(gateway):
    a, b = trial_browser(gateway), trial_browser(gateway)
    customer = role(a, 'customer')
    other = role(b, 'customer')
    vendor = role(a, 'vendor')
    first_points = a.get('/api/v3/points', headers=customer).json()['balance']
    claim = a.post('/api/v3/new-favorite-coupon', params={'coupon_id': 1}, headers=customer)
    assert claim.status_code == 200, claim.text
    item = claim.json()['wallet_item_id']
    qr = a.post('/api/v3/store-scaned-qr-values', params={'wallet_item_id': item}, headers=customer)
    assert qr.status_code == 200, qr.text
    scanned = a.post('/api/v3/new-scan-coupon', headers=vendor, json={'code': qr.json()['code']})
    assert scanned.status_code == 200, scanned.text
    details = dict(wallet_item_id=item, taste=5, ambience=4, service=4, content='味道不錯')
    assert a.post('/api/v3/reviews', headers=vendor, json=details).status_code == 403
    assert a.post('/api/v3/reviews', headers=customer, json=dict(details, images=['file'])).status_code == 422
    review = a.post('/api/v3/reviews', headers=customer, json=details)
    assert review.status_code == 200, review.text
    mine = '/api/v3/reviews/mine'
    assert a.get(mine).status_code == 401
    assert a.get(mine, headers=vendor).status_code == 403
    reply = a.get(mine, headers=customer)
    assert reply.headers['cache-control'] == 'no-store'
    rows = reply.json()['reviews']
    row = next(row for row in rows if row['id'] == review.json()['review']['id'])
    assert row['content'] == '味道不錯'
    assert row['created_at'].endswith('Z')
    assert row['points_given'] is True
    assert set(row) == {'id', 'shop_name', 'content', 'created_at', 'overall', 'reply_text', 'points_given'}
    assert all(r['content'] != '味道不錯' for r in b.get(mine, headers=other).json()['reviews'])
    assert b.get(mine, headers=customer).status_code == 401
    before_exchange = review.json()['balance']
    assert before_exchange == first_points + 45
    assert a.post('/api/v3/reviews', headers=customer, json=details).status_code == 400
    assert a.get('/api/v3/points/exchange-options').status_code == 401
    assert a.get('/api/v3/points/exchange-options', headers=vendor).status_code == 403
    options = a.get('/api/v3/points/exchange-options', headers=customer)
    assert options.headers['cache-control'] == 'no-store'
    data = options.json()
    assert data['cost'] == 9
    coupon = data['coupons'][0]
    assert set(coupon) == {'id', 'coupon_name', 'shop_name'}
    exchanged = a.post('/api/v3/points/exchange', headers=customer, params={'coupon_id': coupon['id']})
    assert exchanged.status_code == 200, exchanged.text
    assert exchanged.json()['balance'] == before_exchange - data['cost']
    assert coupon not in a.get('/api/v3/points/exchange-options', headers=customer).json()['coupons']
    assert coupon in b.get('/api/v3/points/exchange-options', headers=other).json()['coupons']
    assert b.get('/api/v3/points/exchange-options', headers=customer).status_code == 401


def test_bargain_start_only_and_no_other_browser_persona(gateway):
    client = trial_browser(gateway)
    customer = role(client, 'customer')
    vendor = role(client, 'vendor')
    path = '/api/v3/bargain-campaigns/1/start'
    assert client.post(path, headers=vendor).status_code == 403
    result = client.post(path, headers=customer)
    assert result.status_code == 200, result.text
    session = result.json()['session']
    for action in ('help', 'purchase'):
        assert client.post(f"/api/v3/bargain-sessions/{session['id']}/{action}",
                           headers=customer).status_code == 403


def test_application_only_fixed_shop_choices_and_no_admin_role(gateway):
    client = trial_browser(gateway)
    customer = role(client, 'customer')
    vendor = role(client, 'vendor')
    target = '/api/v3/admin/merchant-applications'
    sample = {'shop_name': '海港小店'}
    assert client.post(target, json=sample).status_code == 401
    assert client.post(target, headers=vendor, json=sample).status_code == 403
    for extra in ({'contact_name': 'Name'}, {'br_file_id': 1}, {'phone': '12345678'}):
        assert client.post(target, headers=customer, json={**sample, **extra}).status_code == 422
    assert client.post(target, headers=customer, json={'shop_name': 'Other'}).status_code == 422
    result = client.post(target, headers=customer, json=sample)
    assert result.status_code == 201, result.text
    assert result.json()['application']['shop_name'] == sample['shop_name']
    assert client.get('/api/v3/merchant-applications/mine', headers=customer).status_code == 200
    assert client.get(target, headers=customer).status_code == 403
    assert client.post('/api/v3/trial/role', json={'role': 'admin'}).status_code == 400
    assert client.post('/api/v3/trial/role', json={'role': ['admin']}).status_code == 400
    assert client.post('/api/v3/merchant/connect/payouts', headers=vendor, json={}).status_code == 403
    assert client.get('/api/v3/merchant-files/1', headers=vendor).status_code == 403


def test_vendor_coupon_lifecycle_and_package_operations(gateway):
    client = trial_browser(gateway)
    customer = role(client, 'customer')
    vendor = role(client, 'vendor')
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    dates = {'valid_from': (now - timedelta(hours=1)).isoformat(),
             'valid_to': (now + timedelta(days=7)).isoformat()}
    coupon = {'shop_id': 1, 'coupon_type': 'Discount', 'coupon_name': '新優惠',
              'cash_amount': 10, 'total': 12, **dates}
    for endpoint in ('add-coupon-preview', 'coupon-previewed'):
        route = '/api/v3/' + endpoint
        assert client.post(route, json=coupon, headers=customer).status_code == 403
        assert client.post(route, json={**coupon, 'image': '/uploads/a'}, headers=vendor).status_code == 422
        result = client.post(route, json=coupon, headers=vendor)
        assert result.status_code == 200, result.text
    coupon_id = result.json()['coupon']['id']
    assert client.post('/api/v3/assign-branches', headers=vendor, json={
        'coupon_id': coupon_id, 'branch_ids': []}).status_code == 200
    new = client.patch(f'/api/v3/vendor/coupons/{coupon_id}', headers=vendor, json={'name_en': 'Special'})
    assert new.status_code == 200, new.text

    payload = {'shop_id': 1, 'name': '二人套餐', 'original_price': 138,
               'package_price': 100, 'stock_total': 5, **dates}
    package = client.post('/api/v3/packages', headers=vendor, json=payload)
    assert package.status_code == 201, package.text
    pkg_id = package.json()['package']['id']
    edit = client.patch(f'/api/v3/vendor/packages/{pkg_id}', headers=vendor, json={'name_en': 'New menu'})
    assert edit.status_code == 200, edit.text
    assert client.patch(f'/api/v3/vendor/packages/{pkg_id}', headers=vendor, content=b'x' * 16385).status_code == 413

    # The template contains ordinary already-approved active listings.
    # Make no admin exception to force a newly submitted product live.
    for namespace in ('coupons', 'packages'):
        for action in ('pause', 'resume', 'unlist', 'submit'):
            route = f'/api/v3/vendor/{namespace}/1/{action}'
            changed = client.post(route, headers=vendor)
            assert changed.status_code == 200, changed.text
        assert client.post(f'/api/v3/vendor/{namespace}/1/approve', headers=vendor, json={}).status_code == 403


def test_real_trial_accepts_paid_coupon_actions_without_upload(gateway):
    client = trial_browser(gateway)
    vendor = role(client, 'vendor')
    now = datetime.now(timezone.utc)
    base = {
        'shop_id': 1, 'coupon_type': 'Price', 'coupon_name': 'Local test voucher #61',
        'sale_price': 88.5, 'regular_price': 125, 'discount_price': 105,
        'total': 2, 'refund_policy': 'non_refundable', 'valid_from': (now - timedelta(hours=1)).isoformat(),
        'valid_to': (now + timedelta(days=2)).isoformat(),
    }
    assert client.post('/api/v3/uploads/merchant-images', headers=vendor,
                       files={'files': ('test.webp', bytes([0]), 'image/webp')}).status_code == 403
    for suffix in ('add-coupon-preview','coupon-previewed'):
        path = '/api/v3/' + suffix
        assert client.post(path, headers=vendor,
                           json=dict(base, image='/uploads/merchant-images/existing.webp')).status_code == 422
        r = client.post(path, headers=vendor, json=dict(base, image=''))
        assert r.status_code == 200, r.text
    cid = r.json()['coupon']['id']
    details = client.get(f'/api/v3/vendor/coupons/{cid}', headers=vendor)
    assert details.status_code == 200
    coupon = details.json()['coupon']
    assert coupon['sale_price'] == 88.5 and coupon['regular_price'] == 125 and coupon['discount_price'] == 105
    edited = client.patch(f'/api/v3/vendor/coupons/{cid}', headers=vendor,
                          json={'coupon_name': 'Local updated test #61'})
    assert edited.status_code == 200, edited.text


@pytest.mark.parametrize('state', ['paid', 'future', 'expired', 'sold-out', 'paused'])
def test_exchange_options_and_write_reject_the_same_ineligible_coupon(gateway, state):
    from datetime import timedelta

    client = trial_browser(gateway)
    customer = role(client, 'customer')
    path = gateway._session_file(client.cookies.get(gateway.COOKIE))[1]
    data = client.get('/api/v3/points/exchange-options', headers=customer).json()
    coupon_id = data['coupons'][0]['id']
    engine, db = gateway._db(path)
    try:
        coupon = db.get(gateway.models.Coupon, coupon_id)
        today = datetime.now(timezone.utc).replace(tzinfo=None)
        if state == 'paid':
            coupon.sale_price = 1
        elif state == 'future':
            coupon.valid_from = today + timedelta(days=1)
        elif state == 'expired':
            coupon.valid_to = today - timedelta(days=1)
        elif state == 'sold-out':
            coupon.remaining = 0
        else:
            coupon.status = 'paused'
        db.commit()
    finally:
        db.close()
        engine.dispose()
    before = client.get('/api/v3/points', headers=customer).json()['balance']
    choices = client.get('/api/v3/points/exchange-options', headers=customer).json()
    assert coupon_id not in {row['id'] for row in choices['coupons']}
    assert client.post('/api/v3/points/exchange', params={'coupon_id': coupon_id},
                       headers=customer).status_code == 400
    assert client.get('/api/v3/points', headers=customer).json()['balance'] == before


def test_trial_seed_labels_are_neutral_without_overwriting_a_custom_name(gateway):
    client = trial_browser(gateway)
    customer = role(client, 'customer')
    vendor = role(client, 'vendor')
    for headers, label in ((customer, '顧客'), (vendor, '商戶')):
        profile = client.get('/api/v3/me', headers=headers).json()
        assert profile['name'] == profile['registered_name'] == label
    raw = client.cookies.get(gateway.COOKIE)
    _, session_path = gateway._session_file(raw)
    engine, db = gateway._db(session_path)
    try:
        db.query(gateway.models.User).filter_by(role='customer').one().name = 'Demo 用戶'
        db.query(gateway.models.User).filter_by(role='vendor').one().name = '海邊店主'
        db.commit()
    finally:
        db.close()
        engine.dispose()
    assert client.post('/api/v3/trial/session').json()['reset'] is False
    assert client.cookies.get(gateway.COOKIE) == raw
    for choice, label in (('customer', '顧客'), ('vendor', '海邊店主')):
        profile = client.get('/api/v3/me', headers=role(client, choice)).json()
        assert profile['name'] == profile['registered_name'] == label
    source = gateway.sqlite3.connect(f'file:{gateway.TEMPLATE}?mode=ro', uri=True)
    try:
        assert source.execute("SELECT name FROM users WHERE role='customer'").fetchone() == ('Demo 用戶',)
    finally:
        source.close()
