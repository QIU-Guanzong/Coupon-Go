"""Merchant totals preserve the paid amount across multiple voucher lines."""
from decimal import Decimal
import pytest

from tests import test_packages as base
from tests.test_packages import client
from tests.test_paid_points_allocation import make_order, scan
from app import models
from app.merchant_finance import shop_dashboard


def test_multiple_vouchers_count_as_one_redeemed_order(client):
    with base.TestingSession() as db:
        before = shop_dashboard(db, db.get(models.Shop, 1), days=1)['totals']
    user, _, _, vouchers = make_order(client, 'merchant-dashboard', count=3, price=100.01)
    vendor = base._vendor_headers(client)
    for voucher in vouchers:
        scan(client, user, vendor, voucher)
    with base.TestingSession() as db:
        after = shop_dashboard(db, db.get(models.Shop, 1), days=1)['totals']
    assert after['redemptions'] - before['redemptions'] == 3
    assert after['redeemed_orders'] - before['redeemed_orders'] == 1
    assert Decimal(str(after['gross_amount'])) - Decimal(str(before['gross_amount'])) == Decimal('100.01')
    assert after['avg_order_value'] == pytest.approx(after['gross_amount'] / after['redeemed_orders'])
