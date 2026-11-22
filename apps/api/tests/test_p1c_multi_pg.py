"""Multi-voucher points, refund and scan races on disposable PostgreSQL."""
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


def test_multi_voucher_scan_and_whole_refund_serialize(client):
    user, pkg, order, ids = make_order(client, 'pg-points-race', count=3)
    vendor = base._vendor_headers(client)
    qr = client.post('/api/v3/store-scaned-qr-values',
                     params={'wallet_item_id': ids[0]}, headers=user)
    assert qr.status_code == 200, qr.text
    body = {'code': qr.json()['code'], 'idempotency_key': 'points-multi-pg-race'}
    start = Barrier(2)
    def scan():
        start.wait(timeout=10)
        return client.post('/api/v3/new-scan-coupon', json=body, headers=vendor)
    def refund():
        start.wait(timeout=10)
        return client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=user)
    with ThreadPoolExecutor(max_workers=2) as pool:
        scan_result = pool.submit(scan)
        refund_result = pool.submit(refund)
        scan_response = scan_result.result(timeout=20)
        refund_response = refund_result.result(timeout=20)
    assert sorted((scan_response.status_code, refund_response.status_code)) == [200, 400]
    with base.TestingSession() as db:
        final = db.get(Order, order['id'])
        awards = db.query(models.PointTransaction).filter(
            models.PointTransaction.reason == 'redeem', models.PointTransaction.ref_id.in_(ids)).all()
        if scan_response.status_code == 200:
            assert final.refund_status == 'none'
            assert [t.delta for t in awards] == [33]
            assert db.query(models.Redemption).filter_by(wallet_item_id=ids[0]).count() == 1
        else:
            assert final.refund_status == 'refunded'
            assert awards == []
            assert all(db.get(models.WalletItem, i).status == 3 for i in ids)
    if scan_response.status_code == 200:
        replay = client.post('/api/v3/new-scan-coupon', json=body, headers=vendor)
        assert replay.status_code == 200 and replay.json()['duplicate'] is True
        with base.TestingSession() as db:
            assert db.query(models.PointTransaction).filter_by(reason='redeem', ref_id=ids[0]).count() == 1
    else:
        assert base._stock_left(client, pkg['id']) == 5
        assert client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=user).status_code == 400
