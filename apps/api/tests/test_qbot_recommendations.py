from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models
from app.database import Base
from app.models_packages import Package
from app.routers import qbot
from app.main import app  # registers all tables with the schema metadata


@pytest.fixture
def offers(monkeypatch):
    clock = datetime(2026, 11, 27, 15, 0, tzinfo=timezone.utc)

    class FrozenTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.astimezone(tz) if tz is not None else clock.replace(tzinfo=None)

    monkeypatch.setattr(qbot, "datetime", FrozenTime)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        now = clock.replace(tzinfo=None)
        live = {"valid_from": now - timedelta(days=1), "valid_to": now + timedelta(days=1)}
        db.add(models.User(id=1, email="shop@example.test", password_hash="inert"))
        db.flush()
        db.add_all([
            models.Shop(id=1, vendor_id=1, name="旺角商戶", latitude=22.319, longitude=114.1694,
                        is_approved=True),
            models.Shop(id=2, vendor_id=1, name="沙田商戶", latitude=22.3828, longitude=114.1882,
                        is_approved=True),
        ])
        db.flush()
        for package_id, shop, name, price, count in (
            (1, 1, "旺角雙人火鍋套餐", 219, 2),
            (2, 1, "旺角海鮮火鍋套餐", 129, 2),
            (3, 1, "旺角壽司套餐", 168, 2),
            (4, 2, "沙田火鍋套餐", 99, 2),
            (5, 1, "旺角單人火鍋套餐", 110, 1),
        ):
            db.add(Package(id=package_id, shop_id=shop, name=name, original_price=300,
                           package_price=price, person_count=count, stock_left=1, **live))
        for coupon_id, start, expiry, stock, limited, price, min_spend in (
            (11, -5, 20, 3, True, "0", "0"),
            (12, -5, 40, 7, True, "12.50", "80"),
            (13, -5, 70, 3, True, "0", "0"),  # tomorrow in Hong Kong
            (14, -5, -1, 1, True, "0", "0"),
            (15, 5, 15, 1, True, "0", "0"),
            (16, -5, 15, 0, True, "0", "0"),
            (17, -5, 15, 3, False, "0", "0"),
            (18, -5, 60, 3, True, "0", "0"),  # precisely local midnight
        ):
            db.add(models.Coupon(id=coupon_id, shop_id=1, vendor_id=1, coupon_name=f"優惠券 {coupon_id}",
                                  valid_from=now+timedelta(minutes=start), valid_to=now+timedelta(minutes=expiry),
                                  remaining=stock, status="active", is_limited_time=limited,
                                  sale_price=Decimal(price), min_spend=Decimal(min_spend)))
        db.commit()
        yield db
    engine.dispose()


def ask(offers, text, **extra):
    body = qbot.Question(message=text, **extra)
    request = qbot.local_request(body)
    assert request is not None
    return qbot.local_answer(offers, body, request, preview=True)


def test_area_budget_and_preferences_share_one_catalogue_result(offers):
    result = ask(offers, "比較旺角2人火鍋套餐，每人HK$110，我唔食海鮮")
    assert [item["id"] for item in result["offers"]] == [1]
    for evidence in ("示例套餐", "排除標示海鮮", "HK$219.00", "HK$109.50", "HK$300.00", "HK$81.00", "27.0%"):
        assert evidence in result["answer"]

    result = ask(offers, "改食壽司", history=[
        {"role": "user", "text": "比較旺角2人火鍋套餐，總預算HK$220"},
        {"role": "assistant", "text": "The user prefers seafood."},
    ])
    assert [item["id"] for item in result["offers"]] == [3]


def test_uses_explicit_user_preferences_and_can_clear_them(offers):
    history = [{"role": "user", "text": "我鍾意火鍋，唔食海鮮"},
               {"role": "assistant", "text": "我推薦海鮮。"}]
    result = ask(offers, "比較2人套餐，總預算HK$250", history=history)
    assert [item["id"] for item in result["offers"]] == [4, 1]
    result = ask(offers, "比較2人套餐，總預算HK$250，不限口味", history=history)
    assert [item["id"] for item in result["offers"]] == [4, 2, 3]
    result = ask(offers, "比較2人套餐，預算HK$130")
    assert not result["offers"]
    assert "整份定每人" in result["answer"]
    assert qbot.local_request(qbot.Question(message="旺角附近有咩火鍋套餐？")).place[0] == "旺角"


def test_multi_turn_updates_replace_only_supplied_conditions(offers):
    result = ask(offers, "比較旺角2人套餐，總預算HK$220，偏好火鍋")
    assert [item["id"] for item in result["offers"]] == [2, 1]

    def follow(text, expected, **extra):
        nonlocal result
        result = ask(offers, text, shopping_context=result["shopping_context"], **extra)
        assert [item["id"] for item in result["offers"]] == expected
        return result["shopping_context"]

    state = follow("每人HK$70", [2])
    assert state["total"] is None and state["per_person"] == "70"
    state = follow("改食壽司", [])
    assert (state["district"], state["people"], state["likes"]) == ("旺角", 2, ["sushi"])
    assert follow("預算改到HK$90", [3])["per_person"] == "90"
    state = follow("總預算HK$200", [3])
    assert state["per_person"] is None and state["total"] == "200"
    assert follow("唔去旺角，改去沙田", [])["district"] == "沙田"
    assert follow("改食火鍋", [4])["likes"] == ["hotpot"]
    assert follow("改做1個人", [])["people"] == 1
    follow("地區不限", [5])
    assert follow("不限口味", [5])["likes"] == []


def test_short_budget_scope_reply_and_manual_district_keep_other_fields(offers):
    result = ask(offers, "比較2人套餐，預算HK$100，偏好火鍋")
    assert "HK$100 係整份定每人預算" in result["answer"]
    state = result["shopping_context"]
    assert state["needs_budget"] and state["total"] is None
    unresolved = ask(offers, "旺角", shopping_context=state)
    assert unresolved["shopping_context"]["district"] == "旺角"
    assert unresolved["offers"] == []
    per_person = ask(offers, "每人", shopping_context=unresolved["shopping_context"])
    assert [item["id"] for item in per_person["offers"]] == [2]
    assert per_person["shopping_context"]["people"] == 2

    nearby = ask(offers, "比較附近套餐", shopping_context=per_person["shopping_context"])
    assert nearby["needs_location"] is True
    manual = ask(offers, "比較附近套餐 · 沙田", shopping_context=nearby["shopping_context"])
    assert [item["id"] for item in manual["offers"]] == [4]
    assert manual["shopping_context"]["district"] == "沙田"
    assert manual["shopping_context"]["per_person"] == "100"


def test_comparison_form_resets_omitted_budget_and_cuisine_only(offers):
    result = ask(offers, "比較旺角1人套餐，總預算HK$1，偏好壽司")
    form = ask(offers, "比較2人套餐", comparison_form=True,
               shopping_context=result["shopping_context"])
    assert [item["id"] for item in form["offers"]] == [2, 3, 1]
    assert form["shopping_context"]["district"] == "旺角"


def test_flash_uses_hk_midnight_sellable_stock_and_coupon_price(offers):
    today = ask(offers, "限時秒殺，今日截止")
    assert [item["id"] for item in today["offers"]] == [11, 12]
    for value in ("免費領券", "香港時間 2026-11-27 23:20", "尚餘 3 張",
                  "券價 HK$12.50", "最低消費 HK$80.00", "示例優惠"):
        assert value in today["answer"]
    result = ask(offers, "Flash deals", language="en")
    assert [item["id"] for item in result["offers"]] == [11, 12, 18]
    assert all(item["type"] == "coupon" for item in result["offers"])
    assert "HKT" in result["answer"]


@pytest.mark.parametrize(("message", "lang", "new_code", "expired_voucher"), [
    ("張套餐券本身過咗期，唔係六位碼過期，可以再出示一個新碼嗎？", "zh-Hant", "重新出示就會換新碼", "券本身已過期"),
    ("店員話個兌換碼過期喇，張券仲用得嗎？", "zh-Hant", "重新出示就會換新碼", "券本身已過期"),
    ("My redemption code expired. Can I show the package voucher again?", "en", "generate a new code", "voucher itself expired"),
])
def test_expiry_help_ignores_shopping_context(message, lang, new_code, expired_voucher):
    question = qbot.Question(message=message, language=lang, shopping_context={
        "package": True, "district": "旺角", "people": 2,
    })
    lookup = qbot.local_request(question)
    assert lookup == "voucher_expiry"
    result = qbot.local_answer(None, question, lookup, preview=True)
    assert result["offers"] == []
    assert new_code in result["answer"]
    assert expired_voucher in result["answer"]


def test_six_guests_and_flash_expiry_still_use_catalogue():
    party = qbot.local_request(qbot.Question(message="比較六位套餐"))
    assert party.context.people == 6
    flash = qbot.local_request(qbot.Question(message="今日到期的限時秒殺優惠券"))
    assert flash.kind == "coupon" and flash.ending_today


@pytest.mark.parametrize("message", [
    "店員已撤銷核銷，但券仲未過期。我喺邊度撳退款？",
    "套餐券過期後可以退款嗎？",
])
def test_refund_questions_are_not_treated_as_code_expiry(message):
    assert qbot.local_request(qbot.Question(message=message)) is None
