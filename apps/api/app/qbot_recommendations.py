"""Bounded interpretation of shopping questions for catalogue-only answers."""
from dataclasses import dataclass
from decimal import Decimal
import re

from .qbot_places import PlaceLookup, place_lookup

_FLASH = re.compile(r"秒[殺杀]|[閃闪][購购]|^限[時时][？?]?$|限[時时](?:搶購|抢购|優惠|优惠|券|活動|活动)|flash (?:sales?|deals?)", re.I)
_PKG = re.compile(r"套餐|\b(?:packages?|set meals?)\b", re.I)
_ANALYSIS = re.compile(r"比較|比较|對比|对比|分析|最抵|划算|推介|推薦|推荐|有咩|有什麼|有什么|想食|想吃|"
                       r"\b(?:compare|recommend|best value|find|which|looking for)\b", re.I)
_EXPLICIT_ANALYSIS = re.compile(r"比較|比较|對比|对比|分析|最抵|划算|推介|推薦|推荐|"
                                r"\b(?:compare|recommend|best value|analysis)\b", re.I)
_PREFERENCE_SIGNAL = re.compile(r"喜[歡欢]|[鍾钟]意|偏好|想食|想吃|改食|改吃|[換换]成|唔食|不[吃食要]|"
                                r"\b(?:prefer|like|avoid|looking for|switch to)\b", re.I)
_NEGATION = re.compile(r"唔[食要鍾]|不[吃食要喜]|別|别|避免|\b(?:no|avoid|without|not)\b", re.I)
_CLEAR = re.compile(r"不限口味|[沒没]有偏好|任何口味|\b(?:any cuisine|no preference)\b", re.I)
_USAGE = re.compile(r"退款|[積积]分|點樣用|怎麼用|怎么用|點樣[買搶]|如何[搶抢領领买買]|過敏|过敏|"
                    r"\b(?:refund|points|allerg\w*|how to|how do i)\b", re.I)
_PARTY = re.compile(r"(?<!\d)([1-9]\d?|[一二三四五六七八九十兩两双雙])\s*(?:人|位|\bpeople\b|"
                    r"\bpersons?\b|\bdiners?\b)", re.I)
_FOR_PARTY = re.compile(r"\bfor\s+([1-9]\d?)\b", re.I)
_PARTY_RANGE = re.compile(r"\d+\s*(?:至|到|[-–]|to)\s*\d+\s*(?:人|位|people|diners)", re.I)
_MONEY = r"(?:HK\$|HKD|港[幣币])?\s*([0-9]{1,8}(?:\.[0-9]{1,2})?)(?![\d.,])\s*(?:港元|元)?"
_TOTAL = re.compile(r"(?:[總总][預预]算|(?:全份|整份)[預预]算|total budget)\s*(?:[:：≤]|under|of|是)?\s*" + _MONEY, re.I)
_PER_PERSON = re.compile(r"(?:人均|每人|per person)\s*(?:[預预]算|budget|under|不超[過过]|[:：≤])?\s*" + _MONEY, re.I)
_BUDGET = re.compile(r"[預预]算|上限|不超[過过]|\$\s*\d|[＄￥]|HKD\s*\d|\b(?:budget|under\s+\d|USD|RMB|CNY)\b", re.I)
_PRICE_RANGE = re.compile(r"\d\s*(?:至|到|[-–]|to)\s*(?:HK\$|HKD|港[幣币])?\s*\d", re.I)
_OTHER_CURRENCY = re.compile(r"\b(?:USD|RMB|CNY|GBP|EUR|JPY|US\$)\b|美元|美金|人民[幣币]|台幣|臺幣|日圓|[€£¥]", re.I)
_ENDS_TODAY = re.compile(r"(?:今日|今天)\s*(?:到期|截止|[結结]束)|\b(?:ending|expiring) today\b", re.I)
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


@dataclass(frozen=True)
class OfferSearch:
    kind: str
    people: int | None = None
    total: Decimal | None = None
    per_person: Decimal | None = None
    likes: tuple[str, ...] = ()
    dislikes: tuple[str, ...] = ()
    place: PlaceLookup | None = None
    ending_today: bool = False
    clarify: bool = False


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
            context = re.split(r"[,，。；;.!?]|\b(?:but|instead|prefer)\b|但|想食|想吃|偏好",
                               text[:match.start()], flags=re.I)[-1][-24:]
            (dislikes if _NEGATION.search(context) else likes).append(key)
    return tuple(likes), tuple(dislikes)


def request_for(question, comparison):
    message = question.message
    if _USAGE.search(message):
        return None
    likes, dislikes = _keywords(message)
    if _FLASH.search(message):
        kind = "coupon"
    elif comparison or question.preference or (
        _PKG.search(message) and (_ANALYSIS.search(message) or _BUDGET.search(message))
    ) or (_PREFERENCE_SIGNAL.search(message) and (likes or dislikes)) or _CLEAR.search(message):
        kind = "package"
    else:
        recent = [turn.text for turn in reversed(question.history) if turn.role == "user"]
        follow_up = any(pattern.search(message)
                        for pattern in (_PREFERENCE_SIGNAL, _PARTY, _TOTAL, _PER_PERSON))
        if not (follow_up and recent and _PKG.search(recent[0])):
            return None
        kind = "package"

    if question.preference:
        likes, dislikes = (question.preference,), ()
    elif not (likes or dislikes or _CLEAR.search(message)):
        for turn in reversed(question.history):
            if turn.role != "user" or not (_PREFERENCE_SIGNAL.search(turn.text) or _CLEAR.search(turn.text)):
                continue
            likes, dislikes = _keywords(turn.text)
            if likes or dislikes or _CLEAR.search(turn.text):
                break

    party = _PARTY.search(message) or _FOR_PARTY.search(message)
    people = None
    if party:
        label = party[1]
        people = int(label) if label.isascii() else _PARTY_DIGITS.get(label, 2)
    total, each = _TOTAL.search(message), _PER_PERSON.search(message)
    total = Decimal(total[1]) if total else None
    each = Decimal(each[1]) if each else None
    if comparison:
        people, total = comparison
    clarify = kind == "package" and bool(
        _OTHER_CURRENCY.search(message) or _PARTY_RANGE.search(message)
        or (_BUDGET.search(message) and ((total is None and each is None)
                                        or _PRICE_RANGE.search(message)))
    )
    place = place_lookup(question)
    if (kind == "package" and place and not comparison and not question.preference
            and not (_EXPLICIT_ANALYSIS.search(message) or _BUDGET.search(message)
                     or _PREFERENCE_SIGNAL.search(message))):
        return None  # keep map-led browsing for ordinary "packages nearby" requests
    if kind == "package" and not (_PKG.search(message) or comparison):
        # A preference-only follow-up can preserve the last explicit package budget and party.
        previous = next((t for t in reversed(question.history)
                         if t.role == "user" and _PKG.search(t.text)), None)
        if previous:
            previous_search = request_for(question.model_copy(update={
                "message": previous.text, "history": [], "preference": None,
            }), None)
            if previous_search and previous_search.kind == "package" and not previous_search.clarify:
                if people is None:
                    people = previous_search.people
                if total is None and each is None:
                    total, each = previous_search.total, previous_search.per_person
                place = place or previous_search.place

    return OfferSearch(kind, people, total, each, likes, dislikes, place,
                       bool(_ENDS_TODAY.search(message)), clarify)


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
