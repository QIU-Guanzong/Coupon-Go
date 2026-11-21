"""Multi-voucher orders conserve paid money, including refund retries."""
from decimal import Decimal

import pytest
from fastapi import HTTPException
from tests import test_packages as base
from tests.test_packages import client
from app import models
from app.models_packages import Order
from app.order_allocation import ensure_order_allocations
from app.models_cashback import CashbackTransaction


def make_order(client, suffix, count=3, price=100.01):
    user = base._register_and_login(client, f'allocation-{suffix}@example.com')
    pkg = base._create_package(client, name=f'allocation-{suffix}', price=price)
    _, paid = base._buy_paid(client, user, pkg['id'])
    with base.TestingSession() as db:
        first = db.get(models.WalletItem, paid['wallet_item_id'])
        for _ in range(count - 1):
            db.add(models.WalletItem(user_id=first.user_id, coupon_id=first.coupon_id,
                                     order_id=first.order_id, source='purchase', status=1))
        db.commit()
        ids = [x.id for x in db.query(models.WalletItem).filter_by(order_id=paid['id']).order_by(models.WalletItem.id)]
    return user, pkg, paid, ids


def scan(client, user, vendor, wallet_id):
    qr = client.post('/api/v3/store-scaned-qr-values', params={'wallet_item_id': wallet_id}, headers=user)
    assert qr.status_code == 200, qr.text
    result = client.post('/api/v3/new-scan-coupon', json={'code': qr.json()['code']}, headers=vendor)
    assert result.status_code == 200, result.text
    return result.json()['redemption_id']


def test_frozen_cents_and_fingerprint(client):
    _, _, order, ids = make_order(client, 'cents')
    with base.TestingSession() as db:
        assert list(ensure_order_allocations(db, order['id']).values()) == [Decimal('33.34'), Decimal('33.34'), Decimal('33.33')]
        db.commit()
        assert sum(ensure_order_allocations(db, order['id']).values()) == Decimal('100.01')
        db.get(Order, order['id']).paid_amount = Decimal('100.02')
        db.flush()
        with pytest.raises(HTTPException) as err:
            ensure_order_allocations(db, order['id'])
        assert err.value.status_code == 409
        db.rollback()
        first = db.get(models.WalletItem, ids[0])
        db.add(models.WalletItem(user_id=first.user_id, coupon_id=first.coupon_id,
                                 order_id=first.order_id, source='purchase', status=1))
        db.flush()
        with pytest.raises(HTTPException):
            ensure_order_allocations(db, order['id'])


def test_mixed_coupon_and_legacy_conflict_rejected(client):
    user, _, order, ids = make_order(client, 'mixed', count=2)
    other = base._create_package(client, name='different-template')
    with base.TestingSession() as db:
        other_coupon = db.query(models.Coupon).filter_by(package_id=other['id']).one()
        db.get(models.WalletItem, ids[1]).coupon_id = other_coupon.id
        db.flush()
        with pytest.raises(HTTPException):
            ensure_order_allocations(db, order['id'])
        db.rollback()
        item = db.get(models.WalletItem, ids[0])
        db.add(models.Redemption(wallet_item_id=item.id, coupon_id=item.coupon_id,
                                 shop_id=1, scanned_by_vendor_id=1, face_value=Decimal('100.01')))
        db.flush()
        with pytest.raises(HTTPException):
            ensure_order_allocations(db, order['id'])
        assert db.get(Order, order['id']).wallet_allocation_fingerprint is None


def test_each_redeem_revoke_rescan_and_cashback_conserve(client):
    user, _, order, ids = make_order(client, 'redeem')
    vendor = base._vendor_headers(client)
    reds = [scan(client, user, vendor, i) for i in ids]
    with base.TestingSession() as db:
        values = [db.get(models.Redemption, r).face_value for r in reds]
        assert values == [Decimal('33.34'), Decimal('33.34'), Decimal('33.33')]
        transactions = db.query(CashbackTransaction).filter(CashbackTransaction.redemption_id.in_(reds)).order_by(CashbackTransaction.id).all()
        assert [t.amount for t in transactions] == [Decimal('1.67')] * 3
        assert [t.status for t in transactions] == ['withdrawable', 'withdrawable', 'pending']
        assert all((t.expires_at - t.created_at).days == 89 for t in transactions)
    assert client.post(f'/api/v3/redemptions/{reds[0]}/revoke', headers=vendor).status_code == 200
    again = scan(client, user, vendor, ids[0])
    with base.TestingSession() as db:
        assert db.get(models.Redemption, again).face_value == Decimal('33.34')
        assert db.get(models.Redemption, reds[0]).revoked_at is not None
        assert db.query(CashbackTransaction).filter_by(redemption_id=reds[0]).one().status == 'void'
    denied = client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=user)
    assert denied.status_code == 400


def test_refund_failure_restores_all_then_success_once(client, monkeypatch):
    from app.routers import packages as service
    user, pkg, order, ids = make_order(client, 'refund', count=2)
    with base.TestingSession() as db:
        payment = db.query(models.Payment).filter_by(order_id=order['id']).one()
        result = dict(gateway_ref='re_alloc', payment_intent=payment.gateway_ref,
                      amount=float(order['paid_amount']), currency='hkd', status='pending')
    calls = []
    def gateway(ref, amount, *, request_key):
        calls.append(request_key)
        return result
    monkeypatch.setattr(service, 'refund_payment', gateway)
    monkeypatch.setattr(service, 'retrieve_refund', lambda ref: result)
    before = base._stock_left(client, pkg['id'])
    url = f"/api/v3/orders/{order['id']}"
    assert client.post(url + '/refund', json={}, headers=user).json()['refund']['status'] == 'requested'
    with base.TestingSession() as db:
        assert [(db.get(models.WalletItem, i).status, db.get(models.WalletItem, i).refund_hold_previous_status) for i in ids] == [(3, 1), (3, 1)]
    result['status'] = 'failed'
    assert client.get(url, headers=user).status_code == 200
    with base.TestingSession() as db:
        assert [(db.get(models.WalletItem, i).status, db.get(models.WalletItem, i).refund_hold_previous_status) for i in ids] == [(1, None), (1, None)]
    result['gateway_ref'], result['status'] = 're_alloc_2', 'succeeded'
    assert client.post(url + '/refund', json={}, headers=user).json()['refund']['status'] == 'refunded'
    assert base._stock_left(client, pkg['id']) == before + 1
    assert client.post(url + '/refund', json={}, headers=user).status_code == 400
    assert base._stock_left(client, pkg['id']) == before + 1
    assert len(calls) == 2 and calls[0] != calls[1]


def test_revoke_every_voucher_then_refund_once(client):
    user, pkg, order, ids = make_order(client, 'revoke-refund', count=2)
    vendor = base._vendor_headers(client)
    before = base._stock_left(client, pkg['id'])
    reds = [scan(client, user, vendor, item_id) for item_id in ids]
    for redemption_id in reds:
        response = client.post(f'/api/v3/redemptions/{redemption_id}/revoke', headers=vendor)
        assert response.status_code == 200, response.text
    response = client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=user)
    assert response.status_code == 200, response.text
    assert response.json()['refund']['status'] == 'refunded'
    with base.TestingSession() as db:
        assert all(row.status == 'void' for row in db.query(CashbackTransaction).filter(CashbackTransaction.redemption_id.in_(reds)))
        assert all(db.get(models.WalletItem, item_id).status == 3 for item_id in ids)
    assert base._stock_left(client, pkg['id']) == before + 1
    assert client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=user).status_code == 400
    assert base._stock_left(client, pkg['id']) == before + 1


def test_cashback_cap_applies_to_each_allocated_share(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, 'CASHBACK_MAX_PER_TXN_HKD', 1.00)
    user, _, _, ids = make_order(client, 'cashback-cap', count=2)
    vendor = base._vendor_headers(client)
    reds = [scan(client, user, vendor, item_id) for item_id in ids]
    with base.TestingSession() as db:
        rows = db.query(CashbackTransaction).filter(CashbackTransaction.redemption_id.in_(reds)).all()
        assert [row.amount for row in rows] == [Decimal('1.00'), Decimal('1.00')]


def test_failed_refund_restores_distinct_prior_statuses(client, monkeypatch):
    from app.routers import packages as service
    _, _, order, ids = make_order(client, 'mixed-previous', count=2)
    with base.TestingSession() as db:
        db.get(models.WalletItem, ids[0]).status = 3
        db.commit()
        payment = db.query(models.Payment).filter_by(order_id=order['id']).one()
        result = dict(gateway_ref='re_distinct', payment_intent=payment.gateway_ref,
                      amount=float(order['paid_amount']), currency='hkd', status='failed')
    monkeypatch.setattr(service, 'refund_payment', lambda *a, **kw: result)
    with base.TestingSession() as db:
        locked = service._lock_order(db, order['id'])
        assert service._process_refund(db, locked, 'test', expired=True).status == 'rejected'
    with base.TestingSession() as db:
        assert [(db.get(models.WalletItem, i).status, db.get(models.WalletItem, i).refund_hold_previous_status) for i in ids] == [(3, None), (1, None)]
