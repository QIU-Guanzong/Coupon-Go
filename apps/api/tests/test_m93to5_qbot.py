import os
from datetime import datetime, timedelta, timezone
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app import config, models, qbot_budget
from app.database import Base, get_db
from app.deps import get_optional_user
from app.models_bargain import BargainCampaign
from app.models_packages import Package
from app.routers import qbot
from app.serve import trusted_proxies


def mock_provider(url, **kwargs):
    return httpx.Response(200, request=httpx.Request("POST", url),
                          json={"choices": [{"message": {"content": "Hello"}}]})


def build_app(monkeypatch, user):
    app = FastAPI()
    app.include_router(qbot.router)
    app.dependency_overrides[get_db] = lambda: type("DB", (), {"close": lambda self: None})()
    app.dependency_overrides[get_optional_user] = lambda: user
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-provider-test")
    monkeypatch.setattr(qbot.httpx, "post", mock_provider)
    monkeypatch.setattr(qbot, "catalog", lambda *_: [])
    return app


def test_two_users_through_same_proxy_have_independent_user_budgets(monkeypatch):
    monkeypatch.setattr(qbot_budget.config, "REDIS_URL", "")
    monkeypatch.setattr(qbot_budget, "_counts", qbot_budget.Counter())
    user = models.User(id=51)
    app = build_app(monkeypatch, user)
    with TestClient(app) as client:
        for _ in range(8):
            assert client.post("/qbot/chat", json={"message": "hello"}).status_code == 200
        assert client.post("/qbot/chat", json={"message": "hello"}).status_code == 429
        user.id = 52
        assert client.post("/qbot/chat", json={"message": "hello"}).status_code == 200
    assert sorted(qbot_budget._counts.values()) == [1, 8]


def test_forwarded_ip_only_from_explicit_trusted_peers(monkeypatch):
    app = build_app(monkeypatch, None)
    identities = []
    monkeypatch.setattr(qbot, "admit", identities.append)
    headers = {"X-Forwarded-For": "192.0.2.19, 198.51.100.2"}
    proxied = ProxyHeadersMiddleware(app, trusted_hosts=trusted_proxies("198.51.100.0/24"))
    with TestClient(proxied, client=("203.0.113.3", 10100)) as client:
        assert client.post("/qbot/chat", json={"message": "hello"}, headers=headers).status_code == 200
    with TestClient(proxied, client=("198.51.100.3", 10100)) as client:
        assert client.post("/qbot/chat", json={"message": "hello"}, headers=headers).status_code == 200
    assert identities == ["ip:203.0.113.3", "ip:192.0.2.19"]
    for unsafe in ("*", "0.0.0.0/0", "::/0", "unknown-host"):
        with pytest.raises(ValueError):
            trusted_proxies(unsafe)


def test_fallback_warns_once_and_keeps_bounded_memory(monkeypatch, caplog):
    monkeypatch.setattr(qbot_budget.config, "REDIS_URL", "redis://placeholder")
    monkeypatch.setattr(qbot_budget, "_retry_after", 0)
    monkeypatch.setattr(qbot_budget, "_counts", qbot_budget.Counter())
    class DeadRedis:
        def eval(self, *_):
            raise redis.ConnectionError("must not leak redis error")
    monkeypatch.setattr(qbot_budget, "_redis", lambda *_: DeadRedis())
    for i in range(60):
        qbot_budget.admit(f"guest:{i}")
    for i in range(200):
        with pytest.raises(HTTPException) as exc:
            qbot_budget.admit(f"denied:{i}")
        assert exc.value.status_code == 429
    assert len(qbot_budget._counts) == 60
    assert caplog.text.count("bounded local budget") == 1
    assert "must not leak redis error" not in caplog.text


def test_public_offer_limit_and_catalog_visibility(tmp_path):
    engine = create_engine(os.getenv("QBOT_ISOLATED_TEST_PG_URL") or f"sqlite:///{tmp_path / 'qbot.sqlite'}")
    with Session(engine) as db:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        window = dict(valid_from=now - timedelta(days=1), valid_to=now + timedelta(days=1))
        Base.metadata.create_all(engine)
        db.add(models.User(id=1, email="fixture@example.test", password_hash="inert"))
        db.flush()
        db.add_all([models.Shop(id=1, vendor_id=1, name="好味餐廳", is_approved=True),
                    models.Shop(id=2, vendor_id=1, name="非公開", is_approved=False)])
        db.flush()
        db.add_all([models.Coupon(id=i, shop_id=1, vendor_id=1, coupon_name=f"優惠券{i}", remaining=2,
                                  status="active", **window) for i in range(1, 11)])
        db.add_all([Package(id=51, shop_id=1, name="花雕雞套餐", name_en="Chicken feast", original_price=150,
                            package_price=99, stock_left=2, **window),
                    Package(id=58, shop_id=2, name="不公開套餐", original_price=150,
                            package_price=99, stock_left=2, **window),
                    BargainCampaign(id=88, shop_id=1, name="甜品砍價", original_price=50,
                                    floor_price=20, stock_total=2, stock_left=2, expires_at=window["valid_to"]),
                    BargainCampaign(id=89, shop_id=1, name="過期砍價", original_price=50,
                                    floor_price=20, stock_total=2, stock_left=2, expires_at=window["valid_from"])])
        db.commit()
        public = qbot.catalog(db, "有花雕雞套餐或甜品砍價嗎")
        assert len(public) == 8
        assert {row["type"] for row in public} == {"coupon", "package", "bargain"}
        assert next(row for row in public if row["id"] == 51)["price_hkd"] == "99.00"
        # Filtering and price ordering must precede sampling the first eight IDs.
        for i in range(10):
            db.add(Package(id=100+i, shop_id=1, name=f"二人餐{i}", original_price=200,
                           person_count=2, package_price=105-i, stock_left=1, **window))
        db.add(Package(id=111, shop_id=1, name="限定套餐", original_price=200,
                       person_count=2, package_price=95.01, stock_left=1,
                       refund_policy="expiry_only", usage_rules='{"need_booking":true}', **window))
        db.add(Package(id=112, shop_id=1, name="多人餐", original_price=200,
                       person_count=4, package_price=40, stock_left=1, **window))
        db.commit()
        hits = qbot.catalog(db, "", qbot.comparison_request("比較2人套餐，總預算HK$99"))
        assert [x["id"] for x in hits] == [111, 109, 108, 107, 51, 106]
        assert hits[0]["per_person_hkd"] == "47.51"
        assert hits[0]["need_booking"] is True
        assert "need_booking" not in hits[1]
        reply = qbot.comparison_answer(hits, "zh-Hant", False)
        assert [x["id"] for x in reply["offers"]] == [111, 109, 108]
        assert "須預約" in reply["answer"] and "僅過期自動退" in reply["answer"]
        assert "預約要求未列明" in reply["answer"]
        assert {58, 89}.isdisjoint({row["id"] for row in public})
        assert qbot.mentioned_offers("Try Chicken feast and imaginary offer 89.", public, "en") == [
            {"type": "package", "id": 51, "name": "Chicken feast", "shop": "好味餐廳"}]
        assert not qbot.mentioned_offers("有無關的花雕雞選擇和 88 號優惠。", public, "zh-Hant")
        assert not qbot.mentioned_offers("優惠券10", [{"type":"coupon","id":1,"name":"優惠券1",
                                                      "name_en":"","shop":"好味餐廳"}], "zh-Hant")
    engine.dispose()


def test_redis_limits_are_atomic_and_keys_expire(monkeypatch):
    socket = os.getenv("QBOT_ISOLATED_TEST_REDIS_SOCKET")
    if not socket:
        pytest.skip("requires an isolated local Redis Unix socket")
    monkeypatch.setattr(config, "REDIS_URL", f"unix://{socket}")
    monkeypatch.setattr(qbot_budget, "_retry_after", 0)
    monkeypatch.setattr(qbot_budget, "time", lambda: 600)
    gate = Barrier(16)

    def consume(_):
        gate.wait()
        try:
            qbot_budget.admit("user:600")
            return True
        except HTTPException as error:
            assert error.status_code == 429
            return False

    with ThreadPoolExecutor(max_workers=16) as workers:
        assert sum(workers.map(consume, range(16))) == 8
    client = redis.Redis(unix_socket_path=socket)
    keys = list(client.scan_iter(match="coupon-go:{qbot}:10:*"))
    assert len(keys) == 2
    assert all(1 <= client.ttl(key) <= 60 for key in keys)


@pytest.mark.parametrize("question", [
    "比較2人套餐，每人預算HK$100", "比較2至4人套餐", "compare packages for 2 under USD 50",
    "compare packages for 2, total budget HK$100 near Central",
])
def test_ambiguous_conditions_use_normal_conversation(question):
    assert qbot.comparison_request(question) is None
