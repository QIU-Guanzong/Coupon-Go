"""Demo seed data: 20 shops, 36 coupons.

These are DEMO data invented for development, in the same shape as the MVP
demo data. They are NOT real merchants of Coupon GO! — the live backend is
down and no real merchant data exists. Do not present them as real.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from . import models, security

NOW = datetime.now(timezone.utc).replace(tzinfo=None)

CATEGORIES = [
    ("飲食", "Food & Drink", "ic_category.png"),
    ("產品零售", "Retail", "ic_collect.png"),
    ("服務", "Services", "ic_target.png"),
    ("更多", "More", "ic_category.png"),
]

AREAS = ["香港", "九龍", "新界"]

# (name, name_en, category_idx, area_idx, address, phone, description, lat, lng)
# lat/lng are APPROXIMATE district-level demo coordinates (line A, map):
# good enough for nearby sorting demos, NOT surveyed shop positions.
SHOPS = [
    ("櫻花壽司屋", "Sakura Sushi", 0, 0, "灣仔軒尼詩道 88 號", "2123 4567", "新鮮刺身、壽司放題", 22.2783, 114.1747),
    ("蜀香麻辣火鍋城", "Shu Xiang Hotpot", 0, 1, "旺角彌敦道 610 號", "2388 1122", "四川麻辣火鍋，任食放題", 22.3190, 114.1694),
    ("Glow Beauty", "Glow Beauty", 2, 0, "銅鑼灣羅素街 8 號", "2888 3344", "美容護理、面部療程", 22.2781, 114.1840),
    ("星野咖啡", "Hoshino Coffee", 0, 1, "尖沙咀加連威老道 22 號", "2366 7788", "手沖咖啡、輕食甜點", 22.3027, 114.1750),
    ("海港海鮮酒家", "Harbour Seafood", 0, 0, "中環干諾道中 168 號", "2525 9900", "海鮮酒家、點心午市", 22.2845, 114.1580),
    ("綠活有機超市", "Green Life Organic", 1, 2, "沙田新城市廣場 3 樓", "2699 0011", "有機食品、日用品", 22.3828, 114.1882),
    ("時尚眼鏡坊", "Trendy Optics", 1, 1, "旺角西洋菜南街 45 號", "2399 2233", "眼鏡、太陽眼鏡、隱形眼鏡", 22.3179, 114.1704),
    ("FitZone", "FitZone", 2, 1, "觀塘開源道 55 號", "2345 6677", "健身室、瑜伽、團體課", 22.3126, 114.2236),
    ("悅容 Spa", "Bliss Spa", 2, 0, "灣仔莊士敦道 120 號", "2838 4455", "水療按摩、香薰療程", 22.2764, 114.1735),
    ("髮匠 Salon Pro", "Salon Pro", 2, 1, "九龍城衙前圍道 30 號", "2718 8899", "剪髮、染髮、造型", 22.3303, 114.1916),
    ("ZEPPELIN", "ZEPPELIN", 0, 0, "中環雲咸街 33 號", "2544 5566", "港式熱狗、輕食", 22.2808, 114.1557),
    ("金龍燒味", "Golden Dragon BBQ", 0, 2, "元朗青山公路 88 號", "2477 1100", "燒味、燒臘飯", 22.4453, 114.0222),
    ("天天茶餐廳", "Everyday Cafe", 0, 1, "深水埗欽州街 66 號", "2361 2233", "茶餐廳、港式奶茶", 22.3300, 114.1606),
    ("好味點心", "Tasty Dim Sum", 0, 0, "北角英皇道 200 號", "2566 7788", "點心、粥麵", 22.2930, 114.2006),
    ("優品藥房", "Prime Pharmacy", 1, 2, "荃灣青山公路 300 號", "2498 9900", "藥房、保健品", 22.3707, 114.1172),
    ("樂活健身室", "Active Life Gym", 2, 2, "大埔安邦路 12 號", "2666 1122", "健身、游泳、羽毛球", 22.4500, 114.1650),
    ("美甲工坊", "Nail Atelier", 2, 0, "銅鑼灣波斯富街 18 號", "2890 3344", "美甲、手足護理", 22.2787, 114.1854),
    ("潮流眼鏡", "Vision Trend", 1, 1, "尖沙咀彌敦道 100 號", "2739 4455", "潮流眼鏡、驗眼服務", 22.3005, 114.1722),
    ("親子樂園", "Kids Wonderland", 2, 2, "屯門屯門市廣場 2 樓", "2455 6677", "親子遊樂、派對場地", 22.3950, 113.9767),
    ("寵物天地", "Pet Paradise", 1, 0, "西環堅尼地城爹核士街 9 號", "2819 5566", "寵物用品、美容", 22.2810, 114.1300),
]

# (shop_idx, type, name, amount-kw, days_valid, total)
# amount-kw: cash amount | percentage | gift text | discount price
COUPONS = [
    (0, "Discount", "壽司放題 85 折", {"percentage": 85}, 30, 120),
    (0, "Cash", "滿 $200 減 $30", {"cash_amount": 30, "min_spend": 200}, 30, 200),
    (1, "Discount", "火鍋任食 9 折", {"percentage": 90}, 21, 150),
    (1, "Gift", "免費送麻辣牛小排一份", {"gift": "麻辣牛小排一份"}, 14, 80),
    (2, "Cash", "美容療程減 $100", {"cash_amount": 100, "min_spend": 500}, 30, 60),
    (2, "Discount", "首次體驗 7 折", {"percentage": 70}, 30, 100),
    (3, "Discount", "咖啡買一送一", {"percentage": 50}, 7, 300),
    (3, "Cash", "滿 $80 減 $15", {"cash_amount": 15, "min_spend": 80}, 14, 200),
    (4, "Discount", "海鮮晚市 88 折", {"percentage": 88}, 30, 100),
    (4, "Price", "點心拼盤 $138", {"discount_price": 138, "regular_price": 188}, 30, 120),
    (5, "Cash", "有機食品滿 $300 減 $50", {"cash_amount": 50, "min_spend": 300}, 30, 150),
    (5, "Discount", "會員日 9 折", {"percentage": 90}, 30, 200),
    (6, "Discount", "配眼鏡 8 折", {"percentage": 80}, 60, 80),
    (6, "Gift", "免費驗眼一次", {"gift": "驗眼一次"}, 60, 100),
    (7, "Price", "月費 $299 任玩", {"discount_price": 299, "regular_price": 499}, 30, 60),
    (7, "Discount", "首月 7 折", {"percentage": 70}, 30, 90),
    (8, "Cash", "Spa 療程減 $150", {"cash_amount": 150, "min_spend": 800}, 30, 50),
    (8, "Discount", "二人同行 85 折", {"percentage": 85}, 30, 80),
    (9, "Discount", "剪髮染髮 9 折", {"percentage": 90}, 30, 120),
    (9, "Gift", "免費頭皮護理一次", {"gift": "頭皮護理一次"}, 30, 60),
    (10, "Price", "孖腸港狗換領券", {"discount_price": 0, "regular_price": 38}, 14, 240),
    (10, "Cash", "滿 $60 減 $10", {"cash_amount": 10, "min_spend": 60}, 14, 200),
    (11, "Discount", "燒味飯 9 折", {"percentage": 90}, 21, 180),
    (11, "Cash", "滿 $100 減 $20", {"cash_amount": 20, "min_spend": 100}, 21, 150),
    (12, "Discount", "午市套餐 88 折", {"percentage": 88}, 14, 200),
    (12, "Gift", "免費凍檸茶一杯", {"gift": "凍檸茶一杯"}, 14, 260),
    (13, "Discount", "點心全日 9 折", {"percentage": 90}, 30, 150),
    (13, "Cash", "滿 $150 減 $25", {"cash_amount": 25, "min_spend": 150}, 30, 120),
    (14, "Cash", "保健品滿 $400 減 $60", {"cash_amount": 60, "min_spend": 400}, 30, 100),
    (15, "Price", "羽毛球場 $80/小時", {"discount_price": 80, "regular_price": 120}, 30, 100),
    (16, "Discount", "美甲套餐 85 折", {"percentage": 85}, 30, 90),
    (17, "Discount", "太陽眼鏡 75 折", {"percentage": 75}, 30, 80),
    (18, "Gift", "免費入場券一張", {"gift": "入場券一張"}, 30, 100),
    (18, "Discount", "生日派對 9 折", {"percentage": 90}, 30, 60),
    (19, "Discount", "寵物美容 9 折", {"percentage": 90}, 30, 100),
    (19, "Cash", "滿 $200 減 $30", {"cash_amount": 30, "min_spend": 200}, 30, 150),
]

# 限時搶購：前 13 張券打上 limited 標記（與 MVP 限時 13 張口徑一致）
LIMITED_TIME_COUNT = 13


def seed(db: Session) -> dict:
    if db.query(models.Shop).count() > 0:
        return {"shops": 0, "coupons": 0, "skipped": True}
    cats = []
    for name, name_en, icon in CATEGORIES:
        c = models.Category(name=name, name_en=name_en, icon=icon)
        db.add(c)
        cats.append(c)
    areas = []
    for name in AREAS:
        a = models.Area(name=name)
        db.add(a)
        areas.append(a)
    db.flush()

    # demo accounts (passwords are demo-only; change before any real use)
    demo = models.User(
        email="demo@coupon-go.example", name="Demo 用戶",
        password_hash=security.hash_password("Demo123!"), role="customer",
    )
    vendor = models.User(
        email="vendor@coupon-go.example", name="Demo 商戶",
        password_hash=security.hash_password("Demo123!"), role="vendor",
    )
    admin = models.User(
        email="admin@coupon-go.example", name="Admin",
        password_hash=security.hash_password("Demo123!"), role="admin",
    )
    db.add_all([demo, vendor, admin])
    db.flush()

    shops = []
    for name, name_en, cat_i, area_i, address, phone, desc, lat, lng in SHOPS:
        s = models.Shop(
            vendor_id=vendor.id, name=name, name_en=name_en,
            category_id=cats[cat_i].id, area_id=areas[area_i].id,
            address=address, phone=phone, description=desc,
            latitude=lat, longitude=lng,
            image="assets/images/dummy_image_1.png", is_approved=True,
        )
        db.add(s)
        shops.append(s)
    db.flush()

    for i, (shop_i, ctype, cname, amt, days, total) in enumerate(COUPONS):
        c = models.Coupon(
            shop_id=shops[shop_i].id, vendor_id=vendor.id,
            coupon_type=ctype, coupon_name=cname,
            category_id=shops[shop_i].category_id,
            cash_amount=amt.get("cash_amount"),
            percentage=amt.get("percentage"),
            gift=amt.get("gift", ""),
            discount_price=amt.get("discount_price"),
            regular_price=amt.get("regular_price"),
            min_spend=amt.get("min_spend", 0),
            image="assets/images/dummy_image_1.png",
            valid_from=NOW - timedelta(days=1),
            valid_to=NOW + timedelta(days=days),
            total=total, remaining=total,
            status="active",
            additional_terms="每人限領一次；不可與其他優惠同時使用；圖片只供參考。",
            is_limited_time=(i < LIMITED_TIME_COUNT),
        )
        db.add(c)
    db.flush()

    # 9 積分兌換券（積分換券區置頂，cost 9 分；不計入 36 張常規券）
    db.add(models.Coupon(
        shop_id=shops[10].id, vendor_id=vendor.id,
        coupon_type="Gift", coupon_name="9 分分券",
        category_id=shops[10].category_id,
        gift="孖腸港狗一份",
        image="assets/images/dummy_image_1.png",
        valid_from=NOW - timedelta(days=1),
        valid_to=NOW + timedelta(days=30),
        total=1000, remaining=1000,
        status="active",
        additional_terms="憑 9 積分兌換，每人限換一次。",
        is_points_exchange=True,
    ))
    db.flush()

    # --- line C (套餐 2A) demo packages: one refundable, one special
    # non-refundable. Demo data, not real merchants. ---
    if db.query(models.Package).count() == 0:
        import json as _json
        _demo_packages = [
            {
                "shop": shops[0], "name": "櫻花壽司雙人套餐",
                "items": [{"name": "特選壽司盛合（12 件）", "qty": 1},
                          {"name": "三文魚刺身（6 片）", "qty": 1},
                          {"name": "味噌湯 x2", "qty": 2}],
                "person_count": 2, "original_price": 428, "package_price": 298,
                "days": 30, "stock": 50, "refund_policy": "anytime",
                "rules": {"weekday_only": False, "holiday": True,
                          "need_booking": True, "includes": ["加一服務費"],
                          "excludes": ["酒水"]},
            },
            {
                "shop": shops[1], "name": "麻辣火鍋四人特價套餐",
                "items": [{"name": "麻辣鍋底", "qty": 1},
                          {"name": "牛小排拼盤", "qty": 1},
                          {"name": "時蔬拼盤", "qty": 1},
                          {"name": "手工丸子拼盤", "qty": 1}],
                "person_count": 4, "original_price": 820, "package_price": 588,
                "days": 7, "stock": 20, "refund_policy": "non_refundable",
                "rules": {"weekday_only": True, "holiday": False,
                          "need_booking": True, "includes": ["鍋底", "醬料"],
                          "excludes": ["飲品", "加一服務費"]},
            },
        ]
        for _p in _demo_packages:
            _pkg = models.Package(
                shop_id=_p["shop"].id, name=_p["name"],
                items=_json.dumps(_p["items"], ensure_ascii=False),
                person_count=_p["person_count"],
                original_price=_p["original_price"],
                package_price=_p["package_price"],
                valid_from=NOW - timedelta(days=1),
                valid_to=NOW + timedelta(days=_p["days"]),
                usage_rules=_json.dumps(_p["rules"], ensure_ascii=False),
                stock_total=_p["stock"], stock_left=_p["stock"],
                refund_policy=_p["refund_policy"], status="active")
            db.add(_pkg)
            db.flush()
            db.add(models.Coupon(
                shop_id=_p["shop"].id, vendor_id=vendor.id,
                coupon_type="Price", coupon_name=_p["name"],
                discount_price=_p["package_price"],
                regular_price=_p["original_price"],
                image="assets/images/dummy_image_1.png",
                valid_from=NOW - timedelta(days=1),
                valid_to=NOW + timedelta(days=_p["days"]),
                total=_p["stock"], remaining=_p["stock"],
                status="active",
                additional_terms="套餐券：到店出示 QR 碼核銷；整張一次核銷，不可拆分。",
                kind="package", package_id=_pkg.id,
                person_count=_p["person_count"],
                usage_rules=_json.dumps(_p["rules"], ensure_ascii=False),
                refund_policy=_p["refund_policy"]))
        db.flush()

    for name, fee, limit in [
        ("基本月費計劃", 0, 5),
        ("進階月費計劃", 299, 20),
        ("尊尚月費計劃", 799, 100),
    ]:
        db.add(models.SubscriptionPlan(
            name=name, monthly_fee=fee, coupon_limit=limit,
            description="月費計劃可隨時取消，每月從帳戶扣除，無隱藏費用。",
        ))
    db.commit()
    return {"shops": len(shops), "coupons": len(COUPONS), "skipped": False}
