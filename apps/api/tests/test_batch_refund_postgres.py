"""Batch consumption and whole-order refunds share the same lock order."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from app import models
from app.models_packages import Order
from tests import test_packages as base
from tests.test_packages import client
from tests.test_paid_points_allocation import make_order

pytestmark = pytest.mark.skipif(not os.environ.get('PACKAGES_TEST_POSTGRES_URL'),
                                reason='requires disposable PostgreSQL')


def test_batch_and_whole_order_refund_serialize(client):
    user, pkg, order, ids = make_order(client, 'batch-refund-race', count=3)
    vendor = base._vendor_headers(client)
    codes = []
    for item_id in ids:
        qr = client.post('/api/v3/store-scaned-qr-values', params={'wallet_item_id': item_id}, headers=user)
        assert qr.status_code == 200, qr.text
        codes.append(qr.json()['code'])
    barrier = Barrier(2)
    def batch():
        barrier.wait(timeout=10)
        return client.post('/api/v3/new-scan-coupon/batch',
                           json={'codes': codes, 'idempotency_key': 'batch-refund-race'}, headers=vendor)
    def refund():
        barrier.wait(timeout=10)
        return client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=user)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(batch), pool.submit(refund)]
        batch_result, refund_result = [future.result(timeout=30) for future in futures]
    assert sorted([batch_result.status_code, refund_result.status_code]) == [200, 400], (batch_result.text, refund_result.text)
    with base.TestingSession() as db:
        final = db.get(Order, order['id'])
        reds = db.query(models.Redemption).filter(models.Redemption.wallet_item_id.in_(ids)).all()
        if batch_result.status_code == 200:
            assert final.refund_status == 'none' and len(reds) == 3
            assert sum(red.face_value for red in reds) == final.paid_amount
        else:
            assert final.refund_status == 'refunded' and reds == []
            assert all(db.get(models.WalletItem, item_id).status == 3 for item_id in ids)
            assert base._stock_left(client, pkg['id']) == 5
