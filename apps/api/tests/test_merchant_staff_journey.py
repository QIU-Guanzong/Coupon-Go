"""Merchant onboarding and multi-shop staff access across role changes."""
from tests.test_dianping_merchant_mgmt import (
    client, _claim_and_present, _make_coupon,
    _register_and_login, _review_application, _submit_application, _uid, _vendor_headers,
)


def test_multi_shop_invitation_role_and_redemption_journey(client):
    owner = _vendor_headers(client)
    employee = _register_and_login(client, 'staff-journey@example.com')
    outsider = _register_and_login(client, 'staff-journey-outsider@example.com')
    employee_id = _uid(client, employee)
    shops = []
    branches = []
    codes = []
    for index in (1, 2):
        created = client.post('/api/v3/merchant/shops',
                              json={'name': f'職員流程商戶 {index}'}, headers=owner)
        assert created.status_code == 201, created.text
        shop_id = created.json()['shop']['id']
        branch = client.post(f'/api/v3/merchant/shops/{shop_id}/branches',
                             json={'name': f'職員流程分店 {index}'}, headers=owner)
        assert branch.status_code == 201, branch.text
        branch_id = branch.json()['branch']['id']
        coupon = _make_coupon(client, shop_id, f'職員流程券 {index}')
        assigned = client.post('/api/v3/assign-branches',
                               json={'coupon_id': coupon, 'branch_ids': [branch_id]}, headers=owner)
        assert assigned.status_code == 200, assigned.text
        _, _, code = _claim_and_present(client, f'staff-journey-buyer-{index}@example.com', coupon)
        shops.append(shop_id)
        branches.append(branch_id)
        codes.append(code)

    def invite(shop_id, role):
        result = client.post(f'/api/v3/admin/shops/{shop_id}/members',
                             json={'email': 'STAFF-JOURNEY@example.com', 'role': role}, headers=owner)
        assert result.status_code == 201, result.text
        assert result.json()['member']['status'] == 'pending'
        return result.json()['member']['id']

    first = invite(shops[0], 'cashier')
    pending = client.get('/api/v3/merchant/shop-invitations', headers=employee)
    assert [(row['id'], row['shop_id']) for row in pending.json()['invitations']] == [(first, shops[0])]
    assert client.get('/api/v3/merchant/shops', headers=employee).json()['shops'] == []
    assert client.get('/api/v3/merchant/shop-invitations', headers=outsider).json()['invitations'] == []
    confirmation = f'/api/v3/admin/shops/{shops[0]}/members/{first}/confirm'
    assert client.post(confirmation, headers=outsider).status_code == 403
    assert client.post(confirmation, headers=employee).status_code == 200
    visible = client.get('/api/v3/merchant/shops', headers=employee).json()['shops']
    assert [(row['id'], row['can_redeem'], row['can_manage_branches']) for row in visible] == [(shops[0], True, False)]
    assert client.get(f'/api/v3/merchant/shops/{shops[1]}/branches', headers=employee).status_code == 403
    assert client.post(f'/api/v3/merchant/shops/{shops[0]}/branches', json={'name': '不應新增'}, headers=employee).status_code == 403

    prepared = client.post('/api/v3/new-scan-coupon/prepare',
                           json={'code': codes[0], 'branch_id': branches[0]}, headers=employee)
    assert prepared.status_code == 200 and prepared.json()['can_redeem'] is True, prepared.text
    wrong_shop = client.post('/api/v3/new-scan-coupon/prepare',
                             json={'code': codes[1], 'branch_id': branches[1]}, headers=employee)
    assert wrong_shop.status_code == 200 and wrong_shop.json()['can_redeem'] is False
    redeemed = client.post('/api/v3/new-scan-coupon', json={
        'code': codes[0], 'branch_id': branches[0],
        'prepare_token': prepared.json()['prepare_token'], 'idempotency_key': 'staff-journey-first',
    }, headers=employee)
    assert redeemed.status_code == 200, redeemed.text

    second = invite(shops[1], 'finance')
    assert client.post(f'/api/v3/admin/shops/{shops[1]}/members/{second}/confirm', headers=employee).status_code == 200
    visible = {row['id']: row for row in client.get('/api/v3/merchant/shops', headers=employee).json()['shops']}
    assert set(visible) == set(shops)
    assert visible[shops[0]]['can_redeem'] is True
    assert visible[shops[1]]['can_redeem'] is False and visible[shops[1]]['can_view_settlement'] is True
    assert client.post('/api/v3/new-scan-coupon/prepare',
                       json={'code': codes[1], 'branch_id': branches[1]}, headers=employee).json()['can_redeem'] is False

    role_url = f'/api/v3/admin/shops/{shops[1]}/members/{employee_id}'
    role_change = client.patch(role_url, json={'role': 'manager'}, headers=owner)
    assert role_change.status_code == 200, role_change.text
    visible = {row['id']: row for row in client.get('/api/v3/merchant/shops', headers=employee).json()['shops']}
    assert visible[shops[1]]['can_redeem'] is True and visible[shops[1]]['can_manage_branches'] is True
    assert visible[shops[1]]['can_view_settlement'] is False
    prepared = client.post('/api/v3/new-scan-coupon/prepare',
                           json={'code': codes[1], 'branch_id': branches[1]}, headers=employee)
    assert prepared.json()['can_redeem'] is True, prepared.text
    assert client.patch(role_url, json={'role': 'finance'}, headers=owner).status_code == 200
    # A pre-check token cannot preserve access after the employee's role changes.
    denied = client.post('/api/v3/new-scan-coupon', json={
        'code': codes[1], 'branch_id': branches[1], 'prepare_token': prepared.json()['prepare_token'],
        'idempotency_key': 'staff-journey-role-change',
    }, headers=employee)
    assert denied.status_code in (400, 403), denied.text

    assert client.delete(f'/api/v3/admin/shops/{shops[0]}/members/{employee_id}', headers=owner).status_code == 200
    assert [row['id'] for row in client.get('/api/v3/merchant/shops', headers=employee).json()['shops']] == [shops[1]]
    assert client.delete(role_url, headers=owner).status_code == 200
    assert client.get('/api/v3/merchant/shops', headers=employee).json()['shops'] == []
    assert client.get(f'/api/v3/merchant/shops/{shops[1]}/branches', headers=employee).status_code == 403


def test_staff_can_join_different_owners_without_cross_shop_control(client):
    owner_a = _vendor_headers(client)
    owner_b = _register_and_login(client, 'staff-journey-second-owner@example.com')
    application = _submit_application(client, owner_b, '第二商戶')
    _review_application(client, application, True)
    shop_a = client.post('/api/v3/merchant/shops', json={'name': '第一商戶'}, headers=owner_a).json()['shop']['id']
    shops_b = client.get('/api/v3/merchant/shops', headers=owner_b).json()['shops']
    shop_b = next(shop['id'] for shop in shops_b if shop['name'] == '第二商戶')
    employee = _register_and_login(client, 'staff-two-owners@example.com')
    employee_id = _uid(client, employee)
    for shop_id, owner, role in ((shop_a, owner_a, 'manager'), (shop_b, owner_b, 'cashier')):
        invited = client.post(f'/api/v3/admin/shops/{shop_id}/members',
                              json={'email': 'staff-two-owners@example.com', 'role': role}, headers=owner)
        assert invited.status_code == 201, invited.text
        member_id = invited.json()['member']['id']
        assert client.post(f'/api/v3/admin/shops/{shop_id}/members/{member_id}/confirm', headers=employee).status_code == 200
    visible = {row['id']: row for row in client.get('/api/v3/merchant/shops', headers=employee).json()['shops']}
    assert set(visible) == {shop_a, shop_b}
    assert visible[shop_a]['can_manage_branches'] is True
    assert visible[shop_b]['can_manage_branches'] is False
    cross_change = client.patch(f'/api/v3/admin/shops/{shop_b}/members/{employee_id}',
                                json={'role': 'manager'}, headers=owner_a)
    assert cross_change.status_code == 403, cross_change.text
    assert client.delete(f'/api/v3/admin/shops/{shop_a}/members/{employee_id}', headers=owner_a).status_code == 200
    remaining = client.get('/api/v3/merchant/shops', headers=employee).json()['shops']
    assert [(row['id'], row['can_redeem']) for row in remaining] == [(shop_b, True)]
