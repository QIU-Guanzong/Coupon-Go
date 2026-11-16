"""Demo bargain campaigns for the seeded shops (line E).

Demo data only, like the rest of seed.py — not real merchants.
Idempotent: does nothing if campaigns already exist.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from . import models
from .models_bargain import BargainCampaign

NOW = datetime.now(timezone.utc).replace(tzinfo=None)

# (shop_index_0_based, name, items, person_count, original, floor, stock)
CAMPAIGNS = [
    (0, "櫻花壽司屋 雙人放題（砍價）", "刺身拼盤、壽司放題、味噌湯", 2,
     Decimal("268.00"), Decimal("128.00"), 5),
    (1, "蜀香麻辣火鍋城 雙人餐（砍價）", "麻辣鍋底、牛小排、蔬菜拼盤、飲品", 2,
     Decimal("388.00"), Decimal("188.00"), 3),
]


def seed_bargain_campaigns(db: Session) -> dict:
    if db.query(BargainCampaign).count() > 0:
        return {"campaigns": 0, "skipped": True}
    shops = db.query(models.Shop).order_by(models.Shop.id).all()
    if not shops:
        return {"campaigns": 0, "skipped": True}
    for shop_i, name, items, persons, original, floor, stock in CAMPAIGNS:
        if shop_i >= len(shops):
            continue
        db.add(BargainCampaign(
            package_id=None, shop_id=shops[shop_i].id, name=name,
            items=items, person_count=persons,
            original_price=original, floor_price=floor,
            stock_total=stock, stock_left=stock,
            helper_limit=5, duration_minutes=1440, lock_stock=True,
            status="active",
            expires_at=NOW + timedelta(days=7)))
    db.commit()
    return {"campaigns": len(CAMPAIGNS), "skipped": False}
