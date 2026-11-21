"""Real PostgreSQL order/wallet lock races; P1B_PG_URL must be an isolated test DB."""
import threading
from decimal import Decimal
import pytest
from tests import test_p1b_connect_fixes as base
from tests.test_p1b_connect_fixes import client
from app import models

pytestmark = pytest.mark.skipif(base.BACKEND != 'postgres', reason='requires isolated PostgreSQL')


def make_order(client, suffix):
    user = base._register_and_login(client, f'alloc-pg-{suffix}@example.com')
    vendor = base._vendor_headers(client)
    pkg = base._create_package(client, f'alloc-pg-{suffix}', price=100.01)
    response = client.post(f"/api/v3/packages/{pkg['id']}/purchase", json={'quantity': 2, 'payment_method': 'card'}, headers=user)
    assert response.status_code == 200, response.text
    response = client.post(f"/api/v3/orders/{response.json()['order']['id']}/confirm-payment", json={'outcome': 'succeeded'}, headers=user)
    assert response.status_code == 200, response.text
    order = response.json()['order']
    with base._db() as db:
        db.get(models.Order, order['id']).paid_amount = Decimal('100.01')
        db.query(models.Payment).filter_by(order_id=order['id']).one().amount = Decimal('100.01')
        item = db.get(models.WalletItem, order['wallet_item_id'])
        db.add(models.WalletItem(user_id=item.user_id, coupon_id=item.coupon_id,
                                 order_id=item.order_id, source='purchase', status=1))
        db.commit()
        ids = [i.id for i in db.query(models.WalletItem).filter_by(order_id=order['id']).order_by(models.WalletItem.id)]
    codes = []
    for wid in ids:
        r = client.post('/api/v3/store-scaned-qr-values', params={'wallet_item_id': wid}, headers=user)
        assert r.status_code == 200, r.text
        codes.append(r.json()['code'])
    return user, vendor, order, ids, codes


def run_together(*funcs):
    barrier = threading.Barrier(len(funcs))
    outputs = [None] * len(funcs)
    def run(index, fn):
        barrier.wait(timeout=10)
        outputs[index] = fn()
    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(funcs)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=20)
    assert not any(thread.is_alive() for thread in threads), 'lock deadlock'
    assert all(result is not None for result in outputs)
    return outputs


def test_two_vouchers_concurrent_redeem_conserves_order(client):
    user, vendor, order, ids, codes = make_order(client, 'two-scans')
    results = run_together(*[lambda code=code: client.post('/api/v3/new-scan-coupon', json={'code': code}, headers=vendor) for code in codes])
    assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
    with base._db() as db:
        rows = db.query(models.Redemption).filter(models.Redemption.wallet_item_id.in_(ids)).order_by(models.Redemption.wallet_item_id).all()
        assert [r.face_value for r in rows] == [Decimal('50.01'), Decimal('50.00')]
        assert sum(r.face_value for r in rows) == Decimal('100.01')


def test_scan_vs_whole_order_refund_one_wins(client):
    user, vendor, order, ids, codes = make_order(client, 'scan-refund')
    scan, refund = run_together(
        lambda: client.post('/api/v3/new-scan-coupon', json={'code': codes[0]}, headers=vendor),
        lambda: client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=user))
    assert (scan.status_code, refund.status_code) in ((200, 400), (400, 200)), (scan.text, refund.text)
    with base._db() as db:
        rows = db.query(models.Redemption).filter(models.Redemption.wallet_item_id.in_(ids)).all()
        if refund.status_code == 200:
            assert refund.json()['refund']['status'] == 'refunded' and not rows
        else:
            assert len(rows) == 1 and rows[0].face_value == Decimal('50.01')
