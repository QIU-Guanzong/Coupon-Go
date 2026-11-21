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
    response = client.post(f"/api/v3/packages/{pkg['id']}/purchase", json={'quantity': count, 'payment_method': 'fps'}, headers=user)
    assert response.status_code == 200, response.text
    paid = base._pay(client, user, response.json()['order']['id'])
    with base.TestingSession() as db:
        # Simulate a discounted order with a cent remainder while preserving
        # the actual reservation of `count` units and authoritative payment.
        db.get(Order, paid['id']).paid_amount = Decimal(str(price))
        db.query(models.Payment).filter_by(order_id=paid['id']).one().amount = Decimal(str(price))
        first = db.get(models.WalletItem, paid['wallet_item_id'])
        for _ in range(count - 1):
            db.add(models.WalletItem(user_id=first.user_id, coupon_id=first.coupon_id,
                                     order_id=first.order_id, source='purchase', status=1))
        db.commit()
        ids = [x.id for x in db.query(models.WalletItem).filter_by(order_id=paid['id']).order_by(models.WalletItem.id)]
    paid['paid_amount'] = price
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
    assert base._stock_left(client, pkg['id']) == before + len(ids)
    assert client.post(url + '/refund', json={}, headers=user).status_code == 400
    assert base._stock_left(client, pkg['id']) == before + len(ids)
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
    assert base._stock_left(client, pkg['id']) == before + len(ids)
    assert client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=user).status_code == 400
    assert base._stock_left(client, pkg['id']) == before + len(ids)


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


def test_three_vouchers_connect_statement_revoke_and_refund_conserve(client):
    """Combined #12/#19/#16 path: no extra receivable or refund."""
    from datetime import datetime, timezone
    from app.models_connect import ConnectLedgerEntry
    from app.models_line_f import Settlement, SettlementItem, SettlementReversal

    user, pkg, order, ids = make_order(client, 'combined-finance', count=3)
    vendor = base._vendor_headers(client)
    admin = base._admin_headers(client)
    before_stock = base._stock_left(client, pkg['id'])
    reds = [scan(client, user, vendor, item_id) for item_id in ids]
    with base.TestingSession() as db:
        entries = db.query(ConnectLedgerEntry).filter(ConnectLedgerEntry.redemption_id.in_(reds)).all()
        assert len(entries) == 3
        assert sum(e.gross_amount for e in entries) == Decimal('100.01')
        assert sum(e.commission for e in entries) == Decimal('9.99')
        assert sum(e.net_amount for e in entries) == Decimal('90.02')

    period = datetime.now(timezone.utc).strftime('%Y-%m')
    response = client.post('/api/v3/admin/settlements/generate', json={'shop_id': 1, 'period': period}, headers=admin)
    assert response.status_code == 201, response.text
    statement = response.json()['settlement']
    with base.TestingSession() as db:
        lines = db.query(SettlementItem).filter(SettlementItem.redemption_id.in_(reds)).all()
        assert sum(i.amount for i in lines) == Decimal('100.01')
        assert sum(i.commission for i in lines) == Decimal('9.99')
    assert statement['payment_mode'] == 'connect'
    projection = client.get(f"/api/v3/orders/{order['id']}", headers=user).json()['order']
    assert projection['settlement_amount_complete'] is True
    assert (projection['commission_amount'], projection['net_amount']) == (9.99, 90.02)
    denied = client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=user)
    assert denied.status_code == 400
    for red in reds:
        result = client.post(f'/api/v3/redemptions/{red}/revoke', headers=vendor)
        assert result.status_code == 200, result.text
    result = client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=user)
    assert result.status_code == 200, result.text
    assert result.json()['refund']['status'] == 'refunded'
    with base.TestingSession() as db:
        s = db.get(Settlement, statement['id'])
        assert s.gross_amount == Decimal(str(statement['gross_amount'])) - Decimal('100.01')
        assert s.commission == Decimal(str(statement['commission'])) - Decimal('9.99')
        assert s.net_amount == Decimal(str(statement['net_amount'])) - Decimal('90.02')
        lines = db.query(SettlementItem).filter(SettlementItem.redemption_id.in_(reds)).all()
        assert len(lines) == 3
        reversals = db.query(SettlementReversal).filter(SettlementReversal.item_id.in_([i.id for i in lines])).all()
        assert len(reversals) == 3
        assert sum(r.amount for r in reversals) == Decimal('-100.01')
        entries = db.query(ConnectLedgerEntry).filter(ConnectLedgerEntry.redemption_id.in_(reds)).all()
        assert all(e.status == 'reversed' for e in entries)
        assert all(db.get(models.WalletItem, item_id).status == 3 for item_id in ids)
    assert base._stock_left(client, pkg['id']) == before_stock + len(ids)


def test_order_and_wallet_refundability_follow_every_voucher(client):
    user, _, order, ids = make_order(client, 'client-projection', count=2)
    vendor = base._vendor_headers(client)
    url = f"/api/v3/orders/{order['id']}"

    def projections():
        body = client.get(url, headers=user).json()['order']
        rows = client.get('/api/v3/get-favorite-coupons-list', headers=user).json()['items']
        return body, [row for row in rows if row['id'] in ids]

    before, wallets = projections()
    assert before['wallet_item_ids'] == ids and before['refundable'] is True
    assert len(wallets) == 2
    assert all(w['quantity'] == 1 and w['order_quantity'] == 2 and w['order_refundable'] for w in wallets)
    assert all(w['order_paid_amount'] == 100.01 for w in wallets)
    red = scan(client, user, vendor, ids[1])
    used, wallets = projections()
    assert used['voucher_redeemed'] is True and used['refundable'] is False
    assert all(w['order_refundable'] is False for w in wallets)
    assert client.post(f'/api/v3/redemptions/{red}/revoke', headers=vendor).status_code == 200
    restored, wallets = projections()
    assert restored['voucher_redeemed'] is False and restored['refundable'] is True
    assert all(w['order_refundable'] is True for w in wallets)


def test_single_wallet_keeps_actual_purchase_quantity(client):
    user = base._register_and_login(client, 'allocation-actual-purchase-three@example.com')
    package = base._create_package(client, name='Single voucher three portions', stock=5)
    result = client.post(f"/api/v3/packages/{package['id']}/purchase", json={
        'payment_method': 'fps', 'quantity': 3}, headers=user)
    assert result.status_code == 200, result.text
    order = base._pay(client, user, result.json()['order']['id'])
    rows = client.get('/api/v3/get-favorite-coupons-list', headers=user).json()['items']
    wallet = next(row for row in rows if row['id'] == order['wallet_item_id'])
    assert wallet['quantity'] == 3 and wallet['order_quantity'] == 3
    assert wallet['order_wallet_quantity_status'] == 'ok' and wallet['order_refundable'] is True
    assert base._stock_left(client, package['id']) == 2
    refund = client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=user)
    assert refund.status_code == 200, refund.text
    assert base._stock_left(client, package['id']) == 5


def test_refunded_legacy_redemption_does_not_create_untracked_balance(client):
    from app.merchant_finance import shop_balance
    from app.models_connect import ConnectLedgerEntry

    user, _, order, ids = make_order(client, 'legacy-refunded', count=1)
    vendor = base._vendor_headers(client)
    red = scan(client, user, vendor, ids[0])
    with base.TestingSession() as db:
        shop = db.get(models.Shop, 1)
        previous = shop_balance(db, shop)['legacy_untracked_redemptions']
        db.query(ConnectLedgerEntry).filter_by(redemption_id=red).delete()
        db.get(Order, order['id']).refund_status = 'refunded'
        db.flush()
        assert shop_balance(db, shop)['legacy_untracked_redemptions'] == previous
@pytest.mark.parametrize('quantity', [1, 3])
def test_inconsistent_wallet_quantity_blocks_allocation_and_refund(client, quantity):
    from app.routers import packages as service
    user, pkg, order, ids = make_order(client, f'quantity-{quantity}', count=2)
    before = base._stock_left(client, pkg['id'])
    with base.TestingSession() as db:
        stored = db.get(Order, order['id'])
        stored.quantity = quantity
        db.commit()
        with pytest.raises(HTTPException) as exc:
            ensure_order_allocations(db, order['id'])
        assert exc.value.status_code == 409
        db.rollback()
        # The non-refundable expiry path must not release stock either.
        coupon = db.get(models.WalletItem, ids[0]).coupon
        from datetime import datetime, timedelta, timezone
        coupon.refund_policy = 'non_refundable'
        coupon.valid_to = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        db.commit()
        with pytest.raises(HTTPException) as exc:
            service._settle_expired(db, order['id'])
        assert exc.value.status_code == 409
        db.rollback()
    response = client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=user)
    assert response.status_code == 409, response.text
    assert base._stock_left(client, pkg['id']) == before


@pytest.mark.parametrize('corruption', ['owner', 'source', 'coupon'])
def test_wallet_identity_mismatch_cannot_release_stock(client, corruption):
    user, pkg, order, ids = make_order(client, f'identity-{corruption}', count=2)
    other = base._create_package(client, name=f'other-{corruption}')
    before = base._stock_left(client, pkg['id'])
    with base.TestingSession() as db:
        item = db.get(models.WalletItem, ids[1])
        if corruption == 'source': item.source = 'claim'
        elif corruption == 'owner': item.user_id = db.query(models.User).filter(models.User.id != item.user_id).first().id
        else: item.coupon_id = db.query(models.Coupon).filter_by(package_id=other['id']).one().id
        db.commit()
        with pytest.raises(HTTPException) as exc:
            ensure_order_allocations(db, order['id'])
        assert exc.value.status_code == 409
    response = client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=user)
    assert response.status_code == 409, response.text
    assert base._stock_left(client, pkg['id']) == before


def test_single_legacy_voucher_can_represent_three_units(client):
    user, pkg, order, ids = make_order(client, 'single-three', count=3)
    before = base._stock_left(client, pkg['id'])
    with base.TestingSession() as db:
        for item_id in ids[1:]: db.delete(db.get(models.WalletItem, item_id))
        db.commit()
        assert ensure_order_allocations(db, order['id']) == {ids[0]: Decimal('100.01')}
        db.commit()
    response = client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=user)
    assert response.status_code == 200, response.text
    assert response.json()['refund']['status'] == 'refunded'
    assert base._stock_left(client, pkg['id']) == before + 3


def test_pending_refund_with_changed_quantity_does_not_retry_gateway(client, monkeypatch):
    from app.routers import packages as service
    user, pkg, order, _ = make_order(client, 'quantity-retry', count=2)
    with base.TestingSession() as db:
        payment = db.query(models.Payment).filter_by(order_id=order['id']).one()
        result = dict(gateway_ref='re_pending_quantity', payment_intent=payment.gateway_ref,
                      amount=float(order['paid_amount']), currency='hkd', status='pending')
    monkeypatch.setattr(service, 'refund_payment', lambda *args, **kwargs: result)
    calls = []
    monkeypatch.setattr(service, 'retrieve_refund', lambda ref: calls.append(ref) or result)
    before = base._stock_left(client, pkg['id'])
    response = client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=user)
    assert response.status_code == 200 and response.json()['refund']['status'] == 'requested'
    with base.TestingSession() as db:
        db.get(Order, order['id']).quantity = 3
        db.commit()
        with pytest.raises(HTTPException) as exc:
            service._resume_refund(db, order['id'])
        assert exc.value.status_code == 409
    assert calls == []
    assert base._stock_left(client, pkg['id']) == before


def test_inconsistent_voucher_identity_is_reported_to_both_clients(client):
    from app.serializers import wallet_out
    from app.serializers_packages import order_out

    _, _, order, ids = make_order(client, 'projection-bad-identity', count=2)
    with base.TestingSession() as db:
        stored = db.get(Order, order['id'])
        db.get(models.WalletItem, ids[1]).source = 'claim'
        db.flush()
        projection = order_out(db, stored)
        wallet = wallet_out(db, db.get(models.WalletItem, ids[0]))
        bad_wallet = wallet_out(db, db.get(models.WalletItem, ids[1]))
        assert bad_wallet['order_wallet_quantity_status'] == 'needs_review'
        assert projection['wallet_quantity_status'] == 'needs_review'
        assert projection['refundable'] is False
        assert wallet['order_wallet_quantity_status'] == 'needs_review'
        assert wallet['order_refundable'] is False
