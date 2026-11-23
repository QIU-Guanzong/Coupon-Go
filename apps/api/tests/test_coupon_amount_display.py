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


def test_seed_discount_titles_and_cover_aliases_match_their_percentages():
    import re

    from app.demo_covers import OFFER_COVERS
    from app.seed import COUPONS

    for shop_index, kind, name, values, _, _ in COUPONS:
        if kind != 'Discount':
            continue
        match = re.search(r'(\d+(?:\.\d+)?)\s*折', name)
        if match:
            assert Decimal(match.group(1)) * 10 == Decimal(str(values['percentage'])), name
    for shop, old, new in [
        ('Sakura Sushi', '壽司放題 85 折', '壽司放題 8.5 折'),
        ('Harbour Seafood', '海鮮晚市 88 折', '海鮮晚市 8.8 折'),
        ('Bliss Spa', '二人同行 85 折', '二人同行 8.5 折'),
        ('Everyday Cafe', '午市套餐 88 折', '午市套餐 8.8 折'),
        ('Nail Atelier', '美甲套餐 85 折', '美甲套餐 8.5 折'),
        ('Vision Trend', '太陽眼鏡 75 折', '太陽眼鏡 7.5 折'),
    ]:
        assert OFFER_COVERS[(shop, old)] == OFFER_COVERS[(shop, new)]
