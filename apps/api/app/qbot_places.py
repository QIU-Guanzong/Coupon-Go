"""Catalogue-backed Q仔 place lookup. Device coordinates never enter the AI prompt."""
import re
from dataclasses import dataclass

from .routers.nearby import published_shops

# Neighbourhood centres are search reference points, never shop coordinates.
_NEIGHBOURHOODS = (
    ("中環", "Central", 22.2845, 114.1580, ()),
    ("上環", "Sheung Wan", 22.2867, 114.1517, ()),
    ("灣仔", "Wan Chai", 22.2775, 114.1722, ("湾仔", "wanchai")),
    ("銅鑼灣", "Causeway Bay", 22.2804, 114.1858, ("铜锣湾", "cwb")),
    ("北角", "North Point", 22.2911, 114.2006, ()),
    ("堅尼地城", "Kennedy Town", 22.2818, 114.1281, ("坚尼地城", "西環", "西环")),
    ("尖沙咀", "Tsim Sha Tsui", 22.2976, 114.1722, ("尖沙嘴", "tst")),
    ("油麻地", "Yau Ma Tei", 22.3131, 114.1707, ()),
    ("旺角", "Mong Kok", 22.3190, 114.1694, ("mongkok",)),
    ("深水埗", "Sham Shui Po", 22.3308, 114.1622, ("深水埔",)),
    ("九龍城", "Kowloon City", 22.3302, 114.1894, ("九龙城",)),
    ("觀塘", "Kwun Tong", 22.3121, 114.2263, ("观塘",)),
    ("將軍澳", "Tseung Kwan O", 22.3074, 114.2599, ("将军澳", "tko")),
    ("沙田", "Sha Tin", 22.3828, 114.1882, ("shatin",)),
    ("大埔", "Tai Po", 22.4500, 114.1650, ()),
    ("荃灣", "Tsuen Wan", 22.3714, 114.1189, ("荃湾",)),
    ("屯門", "Tuen Mun", 22.3950, 113.9767, ("屯门",)),
    ("元朗", "Yuen Long", 22.4453, 114.0222, ()),
    ("東涌", "Tung Chung", 22.2890, 113.9410, ("东涌",)),
)
_REGIONS = (
    ("香港島", "Hong Kong Island", ("香港", "香港岛", "港島", "港岛", "hong kong island")),
    ("九龍", "Kowloon", ("九龍", "九龙", "kowloon")),
    ("新界", "New Territories", ("新界", "new territories")),
)
_CATEGORIES = (
    (("飲食", "饮食", "餐廳", "餐厅", "食肆", "美食", "食飯", "吃飯", "吃饭", "搵食",
      "food", "restaurant", "restaurants", "dining"), ("飲食", "food & drink", "餐飲", "餐饮")),
    (("產品零售", "产品零售", "零售", "購物", "购物", "retail", "shopping"),
     ("產品零售", "retail", "零售")),
    (("服務", "服务", "services"), ("服務", "services")),
)
_SPECIALTIES = (
    ("咖啡", "coffee", "café", "cafe"),
    ("火鍋", "火锅", "hotpot", "hot pot"),
    ("壽司", "寿司", "sushi"),
    ("點心", "点心", "dim sum"),
    ("美容", "beauty", "spa"),
    ("美甲", "nail", "nails"),
    ("健身", "gym", "fitness"),
    ("親子", "亲子", "kids"),
    ("寵物", "宠物", "pet", "pets"),
)
_NEARBY = ("附近", "周邊", "周边", "就近", "我附近", "離我最近", "near me", "nearby", "around me", "closest")
_OFFERS = ("優惠", "优惠", "抵食", "deals", "offers", "discounts")
_COUPONS = ("優惠券", "优惠券", "有券", "coupon", "coupons", "voucher", "vouchers")
_PACKAGES = ("套餐", "package", "packages", "set meal", "set meals")
_USAGE = ("點樣用", "怎麼用", "怎么用", "點樣領", "怎麼領", "怎么领", "點樣核銷",
          "how do i", "how to", "refund", "退款", "積分", "积分")


def _contains(text, terms):
    for term in terms:
        if term.isascii():
            if re.search(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])", text):
                return True
        elif term in text:
            return True
    return False


_PLACES = {
    **{zh: (zh, en, (lat, lng), ()) for zh, en, lat, lng, _ in _NEIGHBOURHOODS},
    **{zh: (zh, en, None, aliases) for zh, en, aliases in _REGIONS},
}
_PLACE_ALIASES = {
    alias.casefold(): zh
    for zh, en, *rest in (*_NEIGHBOURHOODS, *_REGIONS)
    for alias in (zh, en, *rest[-1]) if alias != "香港"
}
_PLACE_PATTERN = re.compile("|".join(
    (rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])" if alias.isascii() else re.escape(alias))
    for alias in sorted(_PLACE_ALIASES, key=len, reverse=True)
))


def named_place(text):
    # Nonoverlapping matches prefer the more specific name, then the last mention.
    match = next(iter(reversed(list(_PLACE_PATTERN.finditer(text.casefold())))), None)
    return _PLACES[_PLACE_ALIASES[match[0]]] if match else None


def saved_place(district):
    return _PLACES.get(district)


@dataclass(frozen=True)
class PlaceLookup:
    place: tuple | None = None
    position: tuple[float, float] | None = None
    category: tuple[str, ...] = ()
    specialty: tuple[str, ...] = ()
    offer: str | None = None


def place_lookup(question):
    text = question.message.casefold().strip()
    if _contains(text, _USAGE) and question.location is None:
        return None
    place = named_place(text)
    position = ((question.location.lat, question.location.lng)
                if question.location is not None else None)
    nearby = _contains(text, _NEARBY)
    category = next((names for terms, names in _CATEGORIES if _contains(text, terms)), ())
    specialty = next((terms for terms in _SPECIALTIES if _contains(text, terms)), ())
    offer = ("package" if _contains(text, _PACKAGES)
             else "coupon" if _contains(text, _COUPONS)
             else "any" if _contains(text, _OFFERS) else None)
    if position is not None or nearby or (place and (category or specialty or offer
                                                    or len(text) < 16
                                                    or _contains(text, ("商戶", "商户", "shops", "places")))):
        return PlaceLookup(place, position, category, specialty, offer)
    # Follow-up such as “有咖啡嗎？” can reuse the latest named area.
    # Explicit new locations and device coordinates are never inferred from the assistant.
    if (specialty or category or offer) and len(text) < 40:
        if question.shopping_context is not None:
            place = saved_place(question.shopping_context.district)
        else:
            last = next((turn for turn in reversed(question.history) if turn.role == "user"), None)
            place = named_place(last.text) if last else None
        if place:
            return PlaceLookup(place, None, category, specialty, offer)
    return None


def place_answer(db, lookup, language, preview):
    en = language == "en"
    if lookup.place:
        zh, english, center, area_terms = lookup.place
        label = english if en else zh
    elif lookup.position:
        label = "your location" if en else "你的位置"
        center, area_terms = lookup.position, ()
    else:
        return {
            "answer": ("Choose a district or use your location to find nearby shops."
                       if en else "選擇地區或使用你的位置，就可以查附近商戶。"),
            "points_balance": None, "offers": [], "places": [], "needs_location": True,
        }
    radius = 2.0 if lookup.position and lookup.place is None else 1.5
    places = published_shops(db, center=center, radius_km=radius, area_terms=area_terms,
                            category_terms=lookup.category, shop_terms=lookup.specialty,
                            offer=lookup.offer, limit=6)
    if places and center:
        answer = (f"Near {label} · within {radius:g} km by straight-line distance."
                  if en else f"{label}附近 · 直線距離 {radius:g} 公里內。")
    elif places:
        answer = f"Coupon GO shops in {label}." if en else f"{label}的 Coupon GO 商戶。"
    else:
        answer = (f"No matching shops near {label}. Try a nearby district or a different category."
                  if en else f"{label}暫時未有符合條件的商戶。可試試其他地區或分類。")
    if preview and places:
        answer += " Demo shops." if en else " 以下為示例商戶。"
    fields = ("shop_id", "shop_name", "name_en", "category_name", "area_name", "address",
              "latitude", "longitude", "image", "distance_km", "rating",
              "has_coupons", "has_packages")
    return {"answer": answer, "points_balance": None, "offers": [],
            "places": [{key: shop[key] for key in fields} for shop in places],
            "needs_location": False}
