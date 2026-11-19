"""Fictional merchants and packages for the local MVP catalogue."""
from datetime import datetime, timedelta, timezone

from . import config, models
from .cashback_service import lock_account
from .routers.packages import store_package
from .schemas_packages import PackageCreateIn

# Each offer specifies its own portions, contents, price and service window.
MERCHANTS = [
    ("汐岸小蒸房", "Tide Steam Kitchen", "飲食", "香港", "西營盤", 22.286, 114.142,
     "開放式蒸點吧，以小竹籠分批蒸製。午市供應點心，晚市轉為家庭圍餐。", [
        ("午市雙人蒸點茶席", 2, 268, 198, "星期一至五 11:30–14:30", False,
         [("海蝦餃（三件）", 1), ("陳皮牛肉球（三件）", 1), ("荷葉珍珠雞", 2), ("壽眉茶位", 2)], "堂食茶位", "外賣包裝"),
        ("四人蒸鮮家宴", 4, 728, 568, "星期二至日 18:00–20:30", True,
         [("豉汁蒸魚柳", 1), ("北菇滑雞", 1), ("瑤柱蒸蛋", 1), ("絲苗飯", 4)], "白飯及熱茶", "另加海鮮")]),
    ("巷尾慢焙室", "Lane End Roastery", "飲食", "九龍", "深水埗", 22.330, 114.160,
     "小批次烘豆，吧枱可看手沖。早上供應麵包，下午的拼配體驗按場次入座。", [
        ("單人手沖早餐", 1, 108, 78, "每日 08:30–11:00", False,
         [("當日單品手沖", 1), ("牛油酸種多士", 1), ("季節果醬", 1)], "手沖咖啡一杯", "轉購咖啡豆"),
        ("雙人咖啡拼配桌", 2, 420, 328, "星期六及日 14:00–15:30", True,
         [("三款產區咖啡試飲", 2), ("拼配豆帶走（100 克）", 2), ("風味筆記卡", 2)], "器具及導師費", "額外咖啡豆")]),
    ("禾埕米食", "Rice Yard", "飲食", "新界", "大埔", 22.448, 114.170,
     "以瓦鍋即煮絲苗米，供應客家小菜。每鍋約需二十分鐘，可按桌上人數選份量。", [
        ("雙人瓦鍋晚飯", 2, 248, 188, "每日 17:30–20:00", False,
         [("臘味瓦鍋飯", 1), ("蒜蓉菜心", 1), ("老火例湯", 2)], "例湯", "另加配料"),
        ("六人客家圍桌", 6, 1288, 998, "星期五至日 18:00–20:30", True,
         [("鹽焗雞（半隻）", 1), ("梅菜扣肉", 1), ("煎釀豆腐", 1), ("時蔬", 2), ("瓦鍋白飯", 2)], "六位茶芥", "自攜酒水開瓶費")]),
    ("泥曜日陶房", "Clay Day Studio", "服務", "九龍", "新蒲崗", 22.336, 114.196,
     "以手捏及釉色練習為主的小型陶室。作品須風乾及燒製，完成後約三星期到店自取。", [
        ("單人手捏早餐杯", 1, 380, 298, "星期三及六 10:00–12:00", True,
         [("陶泥（500 克）", 1), ("手捏指導（兩小時）", 1), ("素燒及透明釉燒", 1)], "一件作品燒製", "郵寄費"),
        ("雙人釉彩小碟班", 2, 620, 480, "星期六及日 15:00–16:30", True,
         [("素燒碟坯", 2), ("釉下彩示範", 1), ("完成品窯燒", 2)], "工具及圍裙借用", "額外碟坯")]),
    ("青頁植作", "Leaf Page Workshop", "產品零售", "新界", "荃灣", 22.371, 114.115,
     "專做小型室內植栽，設有換盆工作枱。到店可按採光挑選植物，再學習澆水及修剪。", [
        ("書桌小盆栽配搭", 1, 168, 128, "每日 12:00–18:00", False,
         [("耐陰小苗", 1), ("排水陶盆", 1), ("介質及護理卡", 1)], "即場換盆", "送貨"),
        ("雙人玻璃苔景工作坊", 2, 568, 438, "星期六 14:00–15:30", True,
         [("玻璃瓶連蓋", 2), ("苔蘚及造景石材料包", 2), ("佈景教學", 1)], "兩份完成品", "大型擺設")]),
    ("海圖裝幀室", "Harbour Bindery", "服務", "香港", "北角", 22.291, 114.200,
     "紙品工作枱按預約接待，每節由裁紙到穿線完成一本小書。店內備有棉線及紙版供選色。", [
        ("單人線裝口袋本", 1, 250, 188, "星期二至五 14:00–15:30", True,
         [("內頁紙（48 頁）", 1), ("布面封皮", 1), ("穿線裝訂教學", 1)], "紙材及工具", "燙金文字"),
        ("雙人旅行相簿裝幀", 2, 690, 528, "星期日 10:00–12:30", True,
         [("硬皮相簿材料包", 2), ("無酸內頁（20 張）", 2), ("書角布及裝訂指導", 1)], "兩本完成相簿", "相片沖印")]),
]


def seed_showcase(db):
    if config.APP_ENV != "dev":
        return
    vendor = db.query(models.User).filter_by(email="vendor@coupon-go.example", role="vendor").first()
    if not vendor:
        return
    lock_account(db, vendor.id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for name, en, category, area, district, lat, lng, intro, offers in MERCHANTS:
        if db.query(models.Shop.id).filter_by(vendor_id=vendor.id, name_en=en).first():
            continue
        shop = models.Shop(vendor_id=vendor.id, name=name, name_en=en,
            category_id=db.query(models.Category.id).filter_by(name=category).scalar(),
            area_id=db.query(models.Area.id).filter_by(name=area).scalar(), latitude=lat, longitude=lng,
            address=f"{district}示範店", description=f"{intro}（MVP 示範商戶）", phone="",
            image="assets/images/mvp-" + en.lower().replace(" ", "-") + ".jpg", is_approved=True)
        db.add(shop)
        db.flush()
        db.add(models.Branch(shop_id=shop.id, name=f"{name} · {district}店", address=shop.address))
        for title, people, original, price, slot, booking, contents, includes, excludes in offers:
            _, coupon = store_package(db, shop, PackageCreateIn(shop_id=shop.id, name=title, person_count=people,
                original_price=original, package_price=price, valid_from=now, valid_to=now+timedelta(days=90),
                items=[{"name": n, "qty": q} for n, q in contents], stock_total=24 if booking else 60,
                refund_policy="anytime", usage_rules={"time_slots": [slot], "need_booking": booking,
                    "weekday_only": slot.startswith("星期一至五") or slot.startswith("星期二至五"),
                    "stackable": False, "includes": [includes], "excludes": [excludes]}))
            coupon.image = shop.image
    db.commit()
