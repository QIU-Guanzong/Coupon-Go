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


def test_refunded_order_alone_never_creates_a_missing_month_statement(client):
    from datetime import datetime
    from app.merchant_finance import catch_up_monthly_settlements
    from app.models_line_f import Settlement
    from app.models_packages import Order
    user, _, order, vouchers = make_order(client, 'merchant-refunded-month', count=1, price=50)
    redemption_id = scan(client, user, base._vendor_headers(client), vouchers[0])
    # Model a historical inconsistent record whose order was refunded but
    # whose redemption was not revoked; the sweep must still skip it.
    with base.TestingSession() as db:
        db.get(Order, order['id']).refund_status = 'refunded'
        db.get(models.Redemption, redemption_id).scanned_at = datetime(2017, 4, 1)
        db.commit()
    with base.TestingSession() as db:
        result = catch_up_monthly_settlements(db)
        assert not db.query(Settlement).filter_by(shop_id=1, period='2017-04').first()
        assert {'shop_id': 1, 'period': '2017-04'} not in result['blocked_existing_periods']
