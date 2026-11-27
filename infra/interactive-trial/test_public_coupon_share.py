"""Public coupon links resolve against the synthetic template without a session."""
import hashlib
import re
import shutil
import sqlite3

from fastapi.testclient import TestClient

from test_trial_api import gateway  # noqa: F401 - pytest fixture


PATH = '/api/v3/trial/public-coupons/'
COUPON_ID = 1


def test_shared_coupon_ignores_browser_inventory_and_does_not_start_sessions(gateway):
    sender, recipient = TestClient(gateway.app), TestClient(gateway.app)
    db_hash = hashlib.sha256(gateway.TEMPLATE.read_bytes()).digest()
    sessions = len(list(gateway.SESSIONS.glob('*.db')))
    shared = recipient.get(f'{PATH}{COUPON_ID}', headers={'Authorization': 'Bearer ignored'})
    assert shared.status_code == 200
    assert shared.headers['cache-control'] == 'no-store'
    assert 'set-cookie' not in shared.headers
    assert len(list(gateway.SESSIONS.glob('*.db'))) == sessions
    payload = shared.json()
    assert set(payload) == {'coupon', 'share_key'}
    assert re.fullmatch(r'[0-9a-f]{24}', payload['share_key'])
    assert payload['coupon']['status'] == 'active'
    assert 'coupon_name' in payload['coupon']
    assert not ({'vendor_id', 'vendor_name', 'user_id', 'wallet_item_id', 'code',
                 'qr_payload', 'is_coupon_favorite'} & payload['coupon'].keys())

    assert sender.post('/api/v3/trial/session').status_code == 200
    assert recipient.post('/api/v3/trial/session').status_code == 200
    session_file = gateway._session_file(sender.cookies.get(gateway.COOKIE))[1]
    with sqlite3.connect(session_file) as browser_db:
        browser_db.execute(
            "UPDATE coupons SET coupon_name='Private browser name', remaining=0 WHERE id=?",
            (COUPON_ID,),
        )
    private = sender.get(f'/api/v3/coupon-details-new?coupon_id={COUPON_ID}')
    assert private.json()['coupon']['coupon_name'] == 'Private browser name'
    bound = f'{PATH}{COUPON_ID}?key={payload["share_key"]}'
    assert sender.get(bound).json() == recipient.get(bound).json() == payload
    assert hashlib.sha256(gateway.TEMPLATE.read_bytes()).digest() == db_hash


def test_public_coupon_availability_and_private_transfer_routes(gateway, tmp_path, monkeypatch):
    template = tmp_path / 'coupon template.db'
    shutil.copy2(gateway.TEMPLATE, template)
    monkeypatch.setattr(gateway, 'TEMPLATE', template)
    client = TestClient(gateway.app)
    assert client.post('/api/v3/trial/session').status_code == 200
    response = client.post('/api/v3/trial/role', json={'role': 'customer'})
    assert response.status_code == 200
    auth = {'Authorization': f"Bearer {response.json()['access_token']}"}
    cases = (('GET', f'/api/v3/public-coupons/{COUPON_ID}'),
             ('POST', '/api/v3/wallet/items/1/gift'),
             ('DELETE', '/api/v3/wallet/items/1/gift'),
             ('GET', '/api/v3/wallet/gifts/preview'),
             ('POST', '/api/v3/wallet/gifts/preview'),
             ('POST', '/api/v3/wallet/gifts/accept'))
    for method, route in cases:
        assert client.request(method, route, headers=auth).status_code == 403

    route = f'{PATH}{COUPON_ID}'
    key = client.get(route).json()['share_key']
    bound = f'{route}?key={key}'
    for invalid in (f'{PATH}0', f'{PATH}2147483648', f'{route}?id=2',
                    f'{route}/accept', f'{route}?key=', f'{bound}&key={key}',
                    f'{bound}&extra=1', f'{route}?key={"0" * 24}'):
        assert client.get(invalid).status_code == 404
    response = client.post(route)
    assert response.status_code == 405 and response.headers['allow'] == 'GET'
    for update, expected in (
        ("remaining=0", 'sold_out'),
        ("valid_from='2020-01-01', valid_to='2020-02-01'", 'expired'),
        ("status='paused'", None),
    ):
        with sqlite3.connect(template) as db:
            db.execute(f'UPDATE coupons SET {update} WHERE id=?', (COUPON_ID,))
        response = client.get(bound)
        if expected is None:
            assert response.status_code == 404
        else:
            assert response.status_code == 200
            assert response.json()['coupon']['status'] == expected
    # A rebuilt catalogue may put a different offer under the same row id.
    with sqlite3.connect(template) as db:
        db.execute("UPDATE coupons SET status='active', coupon_name='Replacement offer' WHERE id=?",
                   (COUPON_ID,))
    replacement = client.get(route)
    assert replacement.status_code == 200 and replacement.json()['share_key'] != key
    assert client.get(bound).status_code == 404
