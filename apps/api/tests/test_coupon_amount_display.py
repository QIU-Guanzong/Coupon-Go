from decimal import Decimal

import pytest

from app.models import Coupon
from app.serializers import amount_text


@pytest.mark.parametrize(('percentage', 'label'), [
    (Decimal('90.00'), '9 折'),
    (Decimal('85.00'), '8.5 折'),
    (Decimal('87.25'), '8.725 折'),
    (Decimal('100.00'), '10 折'),
])
def test_merchant_preview_displays_percentage_as_chinese_discount(percentage, label):
    assert amount_text(Coupon(coupon_type='Discount', percentage=percentage)) == label


def test_merchant_preview_preserves_cash_and_fixed_price_amounts():
    assert amount_text(Coupon(coupon_type='Cash', cash_amount=Decimal('12.50'))) == '減 $12.50'
    assert amount_text(Coupon(coupon_type='Price', discount_price=Decimal('12.50'))) == '$12.50'
