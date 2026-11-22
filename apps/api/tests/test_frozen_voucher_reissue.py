"""Issuance never rewrites a frozen paid order's voucher history."""
import pytest
from fastapi import HTTPException

from app import models
from app.models_packages import Order, Package, Payment
from app.order_allocation import ensure_order_allocations
from app.routers.packages import _issue_voucher
from tests import test_packages as base
from tests import test_p1a_paid_coupons as paid_coupon
from tests.test_packages import client
from tests.test_paid_points_allocation import make_order


def _paid_order(client, kind, suffix):
    if kind == 'package':
        _, _, paid, ids = make_order(client, f'issue-{suffix}', count=2)
        return paid['id'], ids
    user = base._register_and_login(client, f'issue-{suffix}@example.com')
    coupon = paid_coupon._create_coupon(client, name=f'issue-{suffix}')
    purchased = paid_coupon._purchase(client, user, coupon['id'])
    order = base._pay(client, user, purchased['order']['id'])
    return order['id'], [order['wallet_item_id']]


@pytest.mark.parametrize('kind', ['package', 'coupon'])
def test_frozen_complete_set_replays_without_issuing(client, kind):
    order_id, ids = _paid_order(client, kind, f'complete-{kind}')
    with base.TestingSession() as db:
        ensure_order_allocations(db, order_id)
        db.commit()
        order = db.get(Order, order_id)
        package = db.get(Package, order.package_id) if order.package_id else None
        result = _issue_voucher(db, order, package)
        assert result.id == ids[0]
        assert db.query(models.WalletItem).filter_by(order_id=order_id).count() == len(ids)


@pytest.mark.parametrize('kind,removed', [('package', 'all'), ('package', 'partial'), ('coupon', 'all')])
def test_frozen_missing_vouchers_cannot_be_recreated(client, kind, removed):
    order_id, ids = _paid_order(client, kind, f'missing-{kind}-{removed}')
    with base.TestingSession() as db:
        ensure_order_allocations(db, order_id)
        db.commit()
        order = db.get(Order, order_id)
        original_fingerprint = order.wallet_allocation_fingerprint
        deleted = ids if removed == 'all' else ids[1:]
        db.query(models.WalletItem).filter(models.WalletItem.id.in_(deleted)).delete(synchronize_session=False)
        db.flush()
        before = db.query(models.WalletItem).filter_by(order_id=order_id).count()
        package = db.get(Package, order.package_id) if order.package_id else None
        with pytest.raises(HTTPException) as error:
            _issue_voucher(db, order, package)
        assert error.value.status_code == 409
        assert db.query(models.WalletItem).filter_by(order_id=order_id).count() == before
        assert order.wallet_allocation_fingerprint == original_fingerprint
        db.rollback()


@pytest.mark.parametrize('kind', ['package', 'coupon'])
def test_unfrozen_paid_legacy_order_can_reissue_once(client, kind):
    order_id, ids = _paid_order(client, kind, f'legacy-{kind}')
    with base.TestingSession() as db:
        order = db.get(Order, order_id)
        assert order.wallet_allocation_fingerprint is None
        db.query(models.WalletItem).filter(models.WalletItem.id.in_(ids)).delete(synchronize_session=False)
        db.flush()
        package = db.get(Package, order.package_id) if order.package_id else None
        item = _issue_voucher(db, order, package)
        db.flush()
        assert item.order_id == order_id and item.user_id == order.user_id
        assert db.query(models.WalletItem).filter_by(order_id=order_id).count() == 1
        assert _issue_voucher(db, order, package).id == item.id
        db.rollback()


@pytest.mark.parametrize('kind', ['package', 'coupon'])
@pytest.mark.parametrize('payment,refund,settlement', [
    ('pending', 'none', 'pending'), ('paid', 'refunded', 'void'),
    ('paid', 'requested', 'pending'), ('paid', 'none', 'void'),
])
def test_nonissuable_status_cannot_recreate_voucher(client, kind, payment, refund, settlement):
    order_id, ids = _paid_order(client, kind, f'state-{kind}-{payment}-{refund}-{settlement}')
    with base.TestingSession() as db:
        order = db.get(Order, order_id)
        db.query(models.WalletItem).filter(models.WalletItem.id.in_(ids)).delete(synchronize_session=False)
        order.payment_status, order.refund_status, order.settlement_status = payment, refund, settlement
        db.flush()
        package = db.get(Package, order.package_id) if order.package_id else None
        with pytest.raises(HTTPException) as error:
            _issue_voucher(db, order, package)
        assert error.value.status_code == 409
        assert db.query(models.WalletItem).filter_by(order_id=order_id).count() == 0
        db.rollback()


@pytest.mark.parametrize('kind', ['package', 'coupon'])
def test_repeated_payment_webhook_does_not_reissue_frozen_missing_set(client, kind):
    order_id, ids = _paid_order(client, kind, f'event-{kind}')
    with base.TestingSession() as db:
        ensure_order_allocations(db, order_id)
        db.commit()
        reference = db.query(Payment).filter_by(order_id=order_id).one().gateway_ref
        # Simulate a damaged legacy database; replay must not recreate vouchers.
        db.query(models.WalletItem).filter(models.WalletItem.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    result = client.post('/api/v3/payments/webhook', json={
        'type': 'payment_intent.succeeded', 'data': {'object': {'id': reference}}})
    assert result.status_code == 200, result.text
    with base.TestingSession() as db:
        assert db.query(models.WalletItem).filter_by(order_id=order_id).count() == 0
        assert db.get(Order, order_id).wallet_allocation_fingerprint is not None
