"""Merchant package workspace uses the real account's stores and listing states."""
from datetime import datetime, timedelta

from app import config, models
from tests.test_packages import client, TestingSession, _register_and_login, _vendor_headers


def _body(shop_id, name):
    now = datetime.utcnow()
    return {'shop_id': shop_id, 'name': name, 'items': [{'name': '主菜', 'qty': 1}],
            'person_count': 2, 'original_price': 200, 'package_price': 150,
            'valid_from': (now - timedelta(days=1)).isoformat(),
            'valid_to': (now + timedelta(days=30)).isoformat(),
            'usage_rules': {'need_booking': True}, 'stock_total': 5,
            'refund_policy': 'anytime'}


def test_workspace_lists_only_owned_shops_and_all_listing_states(client, monkeypatch):
    vendor = _vendor_headers(client)
    other = _register_and_login(client, 'workspace-other@example.com')
    with TestingSession() as db:
        user = db.query(models.User).filter_by(email='workspace-other@example.com').one()
        user.role = 'vendor'
        shop = models.Shop(vendor_id=user.id, name='另一間店')
        db.add(shop); db.commit(); other_shop = shop.id
    empty = client.get('/api/v3/vendor/package-workspace', headers=other)
    assert empty.status_code == 200
    assert [s['id'] for s in empty.json()['shops']] == [other_shop]
    assert empty.json()['packages'] == []
    monkeypatch.setattr(config, 'PACKAGE_REVIEW_REQUIRED', False)
    first = client.post('/api/v3/packages', headers=vendor, json=_body(1, '上架套餐'))
    assert first.status_code == 201, first.text
    assert first.json()['package']['status'] == 'active'
    monkeypatch.setattr(config, 'PACKAGE_REVIEW_REQUIRED', True)
    second = client.post('/api/v3/packages', headers=vendor, json=_body(1, '待審套餐'))
    third = client.post('/api/v3/packages', headers=other, json=_body(other_shop, '他人套餐'))
    assert second.status_code == third.status_code == 201
    workspace = client.get('/api/v3/vendor/package-workspace', headers=vendor)
    assert workspace.status_code == 200
    data = workspace.json()
    assert 1 in [s['id'] for s in data['shops']]
    assert other_shop not in [s['id'] for s in data['shops']]
    own = {p['name']: p['status'] for p in data['packages']}
    assert own['上架套餐'] == 'active' and own['待審套餐'] == 'pending_review'
    assert '他人套餐' not in own
    other_data = client.get('/api/v3/vendor/package-workspace', headers=other).json()
    assert [p['name'] for p in other_data['packages']] == ['他人套餐']


def test_workspace_rejects_customer_and_unauthenticated_access(client):
    customer = _register_and_login(client, 'workspace-customer@example.com')
    assert client.get('/api/v3/vendor/package-workspace', headers=customer).status_code == 403
    assert client.get('/api/v3/vendor/package-workspace').status_code == 401
