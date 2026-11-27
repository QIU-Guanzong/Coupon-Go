"""Bounded interpretation of shopping questions for catalogue-only answers."""
from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from .qbot_places import PlaceLookup, named_place, place_lookup, saved_place

_FLASH = re.compile(r"秒[殺杀]|[閃闪][購购]|^限[時时][？?]?$|限[時时](?:搶購|抢购|優惠|优惠|券|活動|活动)|flash (?:sales?|deals?)", re.I)
_PKG = re.compile(r"套餐|\b(?:packages?|set meals?)\b", re.I)
_ANALYSIS = re.compile(r"比較|比较|對比|对比|分析|最抵|划算|推介|推薦|推荐|有咩|有冇|有嗎|有没有|有什麼|有什么|想食|想吃|"
                       r"\b(?:compare|recommend|best value|find|which|looking for)\b", re.I)
_EXPLICIT_ANALYSIS = re.compile(r"比較|比较|對比|对比|分析|最抵|划算|推介|推薦|推荐|"
                                r"\b(?:compare|recommend|best value|analysis)\b", re.I)
_PREFERENCE_SIGNAL = re.compile(r"喜[歡欢]|[鍾钟]意|偏好|想食|想吃|改食|改吃|[換换]成|唔食|不[吃食要]|"
                                r"\b(?:prefer|like|avoid|looking for|switch to)\b", re.I)
_NEGATION = re.compile(r"唔[食要鍾]|不[吃食要喜]|別|别|避免|\b(?:no|avoid|without|not)\b", re.I)
_CLEAR = re.compile(r"不限口味|[沒没]有偏好|任何口味|\b(?:any cuisine|no preference)\b", re.I)
_USAGE = re.compile(r"退款|[積积]分|點樣用|怎麼用|怎么用|點樣[買搶]|如何[搶抢領领买買]|過敏|过敏|"
                    r"\b(?:refund|points|allerg\w*|how to|how do i)\b", re.I)
_PARTY = re.compile(r"(?<!\d)([1-9]\d?|[一二三四五六七八九十兩两双雙])\s*(?:[個个]?人|位(?![數数碼码])|\bpeople\b|"
                    r"\bpersons?\b|\bdiners?\b)", re.I)
_FOR_PARTY = re.compile(r"\bfor\s+([1-9]\d?)\b", re.I)
_PARTY_RANGE = re.compile(r"\d+\s*(?:至|到|[-–]|to)\s*\d+\s*(?:[個个]?人|位|people|diners)", re.I)
_MONEY = r"(?:HK\$|HKD|港[幣币])?\s*([0-9]{1,8}(?:\.[0-9]{1,2})?)(?![\d.,])\s*(?:港元|元)?"
_TOTAL = re.compile(r"(?:[總总][預预]算|(?:全份|整份)[預预]算|total budget)\s*(?:[:：≤]|under|of|是)?\s*" + _MONEY, re.I)
_PER_PERSON = re.compile(r"(?:人均|每人|per person)\s*(?:[預预]算|budget)?\s*"
                         r"(?:under|不超[過过]|[:：≤])?\s*" + _MONEY, re.I)
_UNSCOPED = re.compile(r"(?:[預预]算|budget)\s*(?:[:：≤]|under|of|是)?\s*" + _MONEY, re.I)
_BUDGET_CHANGE = re.compile(r"(?:[預预]算\s*(?:改[為为做成到]|加[到至]|減[到至]|降[到至])|"
                            r"budget\s+(?:to|is now))\s*" + _MONEY, re.I)
_BUDGET = re.compile(r"[預预]算|上限|不超[過过]|\$\s*\d|[＄￥]|HKD\s*\d|\b(?:budget|under\s+\d|USD|RMB|CNY)\b", re.I)
_PRICE_RANGE = re.compile(r"\d\s*(?:至|到|[-–]|to)\s*(?:HK\$|HKD|港[幣币])?\s*\d", re.I)
_OTHER_CURRENCY = re.compile(r"\b(?:USD|RMB|CNY|GBP|EUR|JPY|US\$)\b|美元|美金|人民[幣币]|台幣|臺幣|日圓|[€£¥]", re.I)
_ENDS_TODAY = re.compile(r"(?:今日|今天)\s*(?:到期|截止|[結结]束)|\b(?:ending|expiring) today\b", re.I)
_NO_BUDGET = re.compile(r"不限[預预]算|[預预]算不限|不設上限|\b(?:no budget limit|any budget)\b", re.I)
_NO_DISTRICT = re.compile(r"不限地[區区]|地[區区]不限|\b(?:any district|any area)\b", re.I)
_NO_PARTY = re.compile(r"不限人[數数]|人[數数]不限|\bany party size\b", re.I)
_BUDGET_SCOPE = re.compile(r"^(整份|全份|[總总][預预]算|total|whole package|每人|人均|per person)[。.!！?？]?$", re.I)
_FOLLOW_UP = re.compile(r"^(?:仲有|[還还]有|再推介|再推[薦荐]|再[搵找]|再比較|more options|any others)", re.I)
_SHOPS = re.compile(r"商[戶户]|餐[廳厅]|\b(?:shops?|stores?|restaurants?)\b", re.I)
_PARTY_DIGITS = dict(zip("一二三四五六七八九十", range(1, 11)))
_CUISINES = {
    "hotpot": ("火鍋", "火锅", "hotpot", "hot pot"),
    "sushi": ("壽司", "寿司", "sushi"),
    "dim_sum": ("點心", "点心", "dim sum"),
    "coffee": ("咖啡", "coffee", "café"),
    "vegetarian": ("素食", "齋菜", "斋菜", "vegetarian"),
    "cantonese": ("粵菜", "粤菜", "廣東菜", "广东菜", "cantonese"),
    "seafood": ("海鮮", "海鲜", "seafood"),
    "japanese": ("日本菜", "日式", "日料", "japanese"),
    "korean": ("韓國菜", "韩国菜", "韓式", "韩式", "korean"),
    "thai": ("泰國菜", "泰国菜", "泰式", "thai"),
    "sichuan": ("川菜", "四川", "sichuan", "szechuan"),
}
_LABELS = {
    "hotpot": ("火鍋", "Hot pot"), "sushi": ("壽司", "Sushi"),
    "dim_sum": ("點心", "Dim sum"), "coffee": ("咖啡", "Coffee"),
    "vegetarian": ("素食", "Vegetarian"), "cantonese": ("粵菜", "Cantonese"),
    "seafood": ("海鮮", "Seafood"), "japanese": ("日式", "Japanese"),
    "korean": ("韓式", "Korean"), "thai": ("泰式", "Thai"),
    "sichuan": ("川菜", "Sichuan"),
}


Cuisine = Literal["hotpot", "sushi", "dim_sum", "coffee", "vegetarian", "cantonese",
                  "seafood", "japanese", "korean", "thai", "sichuan"]
Money = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]


class ShoppingContext(BaseModel):
    package: bool = False
    district: str | None = Field(default=None, max_length=32)
    people: int | None = Field(default=None, ge=1, le=99)
    total: Money | None = None
    per_person: Money | None = None
    likes: tuple[Cuisine, ...] = Field(default=(), max_length=len(_CUISINES))
    dislikes: tuple[Cuisine, ...] = Field(default=(), max_length=len(_CUISINES))
    needs_people: bool = False
    needs_budget: bool = False
    pending_budget: Money | None = None


@dataclass(frozen=True)
class OfferSearch:
    kind: str
    context: ShoppingContext
    place: PlaceLookup | None = None
    ending_today: bool = False


def _keywords(text):
    if _CLEAR.search(text):
        return (), ()
    likes, dislikes = [], []
    for key, words in _CUISINES.items():
        matches = []
        for word in words:
            pattern = re.escape(word)
            if word[0].isascii():
                pattern = rf"\b{pattern}\b"
            matches.extend(re.finditer(pattern, text, re.I))
        if matches:
            match = max(matches, key=lambda item: item.start())
            context = re.split(r"[,，。；;.!?]|\b(?:but|instead|prefer|switch to)\b|但|想食|想吃|改食|改吃|偏好",
                               text[:match.start()], flags=re.I)[-1][-24:]
            (dislikes if _NEGATION.search(context) else likes).append(key)
    return tuple(likes), tuple(dislikes)


def _last(pattern, message):
    return next(reversed(list(pattern.finditer(message))), None)


def _updates(message, previous):
    changes = {}
    district = named_place(message)
    if district or _NO_DISTRICT.search(message):
        changes["district"] = district[0] if district and not _NO_DISTRICT.search(message) else None
    party = _last(_PARTY, message) or _last(_FOR_PARTY, message)
    if _PARTY_RANGE.search(message):
        changes.update(people=None, needs_people=True)
    elif party or _NO_PARTY.search(message):
        label = party[1] if party else ""
        people = int(label) if label.isascii() and label.isdigit() else _PARTY_DIGITS.get(label, 2)
        changes.update(people=None if _NO_PARTY.search(message) else people, needs_people=False)

    total, each = _last(_TOTAL, message), _last(_PER_PERSON, message)
    scope = _BUDGET_SCOPE.fullmatch(message.strip())
    if _NO_BUDGET.search(message):
        changes.update(total=None, per_person=None, needs_budget=False, pending_budget=None)
    elif _OTHER_CURRENCY.search(message) or (_BUDGET.search(message)
                                            and _PRICE_RANGE.search(_PARTY_RANGE.sub("", message))):
        changes.update(total=None, per_person=None, needs_budget=True, pending_budget=None)
    elif total or each:
        changes.update(total=Decimal(total[1]) if total else None,
                       per_person=Decimal(each[1]) if each else None,
                       needs_budget=False, pending_budget=None)
    elif scope and previous.pending_budget is not None:
        each = scope[1].casefold() in ("每人", "人均", "per person")
        changes.update(total=None if each else previous.pending_budget,
                       per_person=previous.pending_budget if each else None,
                       needs_budget=False, pending_budget=None)
    elif _BUDGET.search(message):
        amendment = _last(_BUDGET_CHANGE, message)
        if amendment and (previous.total is None) != (previous.per_person is None):
            amount = Decimal(amendment[1])
            changes.update(total=amount if previous.total is not None else None,
                           per_person=amount if previous.per_person is not None else None,
                           needs_budget=False, pending_budget=None)
        else:
            pending = amendment or _last(_UNSCOPED, message)
            changes.update(total=None, per_person=None, needs_budget=True,
                           pending_budget=Decimal(pending[1]) if pending else None)

    likes, dislikes = _keywords(message)
    if dislikes and not likes:
        likes = tuple(like for like in previous.likes if like not in dislikes)
    if likes or dislikes or _CLEAR.search(message):
        changes.update(likes=likes, dislikes=dislikes)
    return changes


def _previous_context(question):
    if question.shopping_context is not None:
        return question.shopping_context
    context = ShoppingContext()
    for turn in question.history:
        if turn.role != "user" or _USAGE.search(turn.text) or _FLASH.search(turn.text):
            continue
        changes = _updates(turn.text, context)
        if context.package or _PKG.search(turn.text) or _PREFERENCE_SIGNAL.search(turn.text):
            context = context.model_copy(update={**changes, "package": True})
        elif "district" in changes:
            context = context.model_copy(update={"district": changes["district"]})
    return context


def request_for(question, comparison):
    message = question.message
    if _USAGE.search(message):
        return None
    previous = _previous_context(question)
    changes = _updates(message, previous)
    if _FLASH.search(message):
        return OfferSearch("coupon", ShoppingContext(
            likes=changes.get("likes", ()), dislikes=changes.get("dislikes", ())),
            place_lookup(question), bool(_ENDS_TODAY.search(message)))

    if comparison:
        changes.update(people=comparison[0], needs_people=False)
    if question.comparison_form:
        changes.update(total=changes.get("total"), per_person=None, needs_budget=False,
                       pending_budget=None, likes=(), dislikes=())
    if question.preference:
        changes.update(likes=(question.preference,), dislikes=())
    explicit = (comparison or question.preference or question.comparison_form
                or (_PKG.search(message) and (_ANALYSIS.search(message) or changes))
                or (_PREFERENCE_SIGNAL.search(message) and ("likes" in changes))
                or _CLEAR.search(message))
    if not explicit and not (previous.package and not _SHOPS.search(message)
                             and (changes or _FOLLOW_UP.search(message) or _PKG.search(message))):
        return None

    place = place_lookup(question)
    if (place and not previous.package and not comparison and not question.preference
            and not question.comparison_form and not (
                _EXPLICIT_ANALYSIS.search(message) or _BUDGET.search(message)
                or _PREFERENCE_SIGNAL.search(message) or "people" in changes)):
        return None  # A simple "packages nearby" still opens shop discovery.
    if place and place.position is not None and not named_place(message):
        changes["district"] = None
    context = previous.model_copy(update={**changes, "package": True})
    if "district" in changes and changes["district"] is None and not (place and place.position):
        place = None
    if place is None and (district := saved_place(context.district)):
        place = PlaceLookup(place=district)
    return OfferSearch("package", context, place)


def match_terms(keys):
    return [tuple(word.casefold() for word in _CUISINES[key]) for key in keys]


def preference_summary(search, english):
    language = int(english)
    details = []
    if search.likes:
        details.append(("Looks for " if english else "偏好") +
                       " / ".join(_LABELS[key][language] for key in search.likes))
    if search.dislikes:
        details.append(("Excludes listings mentioning " if english else "排除標示") +
                       " / ".join(_LABELS[key][language] for key in search.dislikes))
    return " · ".join(details)
