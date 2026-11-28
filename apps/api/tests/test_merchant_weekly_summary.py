"""D summary is explicit, private, and uses the service's numbers only."""
import json

import httpx
import pytest
from fastapi import HTTPException

from app import merchant_weekly_summary as service
from app.routers import merchant_stats
from tests.test_merchant_weekly_report import endpoint, weekly  # noqa: F401 (shared isolated fixture)


@pytest.fixture(autouse=True)
def no_real_provider(monkeypatch):
    monkeypatch.delenv("MERCHANT_WEEKLY_SUMMARY_ENABLED", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    calls = []

    def post(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("unexpected provider call in offline fixture")

    monkeypatch.setattr(service.httpx, "post", post)
    return calls


def enable(monkeypatch):
    monkeypatch.setenv("MERCHANT_WEEKLY_SUMMARY_ENABLED", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unused-test-fixture")
    monkeypatch.setattr(merchant_stats, "admit", lambda *_: None)


def mock_post(monkeypatch, model_output, *, reason="stop"):
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200,
            request=httpx.Request("POST", url),
            json={"choices": [{"finish_reason": reason, "message": {"content": model_output}}]})

    monkeypatch.setattr(service.httpx, "post", post)
    return calls


def request_body(client, shop_id, headers, **kwargs):
    # Reports without an available summary never have an actual revision.
    rev = client.get(endpoint(shop_id), headers=headers).json()["summary_revision"]
    return {"summary_revision": rev or "0" * 64, **kwargs}


def test_default_off_and_missing_key_never_call_provider(weekly, monkeypatch, no_real_provider):
    client, headers, (a, _), _ = weekly
    h = headers["owner"]
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unused-test-fixture")
    numeric = client.get(endpoint(a), headers=h)
    assert numeric.status_code == 200
    assert numeric.json()["summary_available"] is False
    off = client.post(endpoint(a) + "/summary", json=request_body(client, a, h), headers=h)
    assert off.status_code == 200 and off.json() == {"summary": None}
    monkeypatch.setenv("MERCHANT_WEEKLY_SUMMARY_ENABLED", "1")
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    assert client.get(endpoint(a), headers=h).json()["summary_available"] is False
    assert client.post(endpoint(a) + "/summary", json=request_body(client, a, h), headers=h).json() == {"summary": None}
    assert no_real_provider == []


def test_only_authorized_explicit_post_sends_whitelisted_two_week_numbers(weekly, monkeypatch):
    enable(monkeypatch)
    admissions = []
    monkeypatch.setattr(merchant_stats, "admit", admissions.append)
    calls = mock_post(monkeypatch, '{"facts":["redemptions","net_amount"]}')
    client, headers, (a, _), _ = weekly
    h = headers["owner"]
    numeric = client.get(endpoint(a), headers=h).json()
    assert numeric["summary_available"] is True and calls == []
    url = endpoint(a) + "/summary"
    assert client.post(url, json={"summary_revision": "0" * 64}, headers=headers["other"]).status_code == 403
    assert client.post(url, json={"summary_revision": "0" * 64}).status_code == 401
    assert client.post(url, json={**request_body(client, a, h), "language": "en", "claims": 99}, headers=h).status_code == 422
    assert calls == [] and admissions == []

    selected = client.post(url, json=request_body(client, a, h, language="en"), headers=h)
    assert selected.status_code == 200, selected.text
    assert selected.headers["cache-control"] == "no-store"
    assert selected.json() == {"summary":
        "Redemptions: 3 this period, 1 last period. Settlement-basis net: HK$149.00 this period, HK$72.00 last period."}
    assert len(calls) == len(admissions) == 1
    request_url, transport = calls[0]
    assert request_url == "https://api.deepseek.com/chat/completions"
    parsed = transport["json"]
    assert parsed["thinking"] == {"type": "disabled"} and parsed["max_tokens"] == 96
    assert "tools" not in parsed
    payload = json.loads(parsed["messages"][1]["content"])
    assert set(payload) == {"currency", "timezone", "current_period", "previous_period", "facts"}
    for key in ("current", "previous"):
        assert payload[f"{key}_period"] == numeric[key]["period"]
    assert payload["facts"]["redemptions"] == {"current": 3, "previous": 1, "delta": 2, "percent": 200.0}
    assert payload["facts"]["net_amount"] == {"current": 149, "previous": 72, "delta": 77,
                                              "percent": numeric["changes"]["net_amount"]["percent"]}
    content = parsed["messages"][1]["content"]
    assert not any(value in content for value in ("shop_id", "Shop A", "email", "buyer",
                                                   "PRIVATE_COUPON", "order_id", "wallet_item"))
    assert client.get(endpoint(a), headers=h).json() == numeric
    assert len(calls) == 1


@pytest.mark.parametrize("language,wanted", [
    ("zh-Hant", "核銷次數：本期 3，上期 1。同期領取轉化率：本期 50.00%，上期 50.00%。"),
    ("zh-Hans", "核销次数：本期 3，上期 1。同期领取转化率：本期 50.00%，上期 50.00%。"),
])
def test_localized_server_template_uses_present_values_only(weekly, monkeypatch, language, wanted):
    enable(monkeypatch)
    mock_post(monkeypatch, '{"facts":["redemptions","conversion_rate"]}')
    client, headers, (a, _), _ = weekly
    assert client.post(endpoint(a) + "/summary", json=request_body(client, a, headers["owner"], language=language),
                       headers=headers["owner"]).json() == {"summary": wanted}


@pytest.mark.parametrize("bad_response,reason", [
    ("", "stop"),
    ('{"facts":["redemptions",99]}', "stop"),
    ('{"facts":["redemptions"],"gross_amount":9999}', "stop"),
    ('{"facts":["redemptions"],"date":"2026-11-22"}', "stop"),
    ('{"facts":["redemptions","shop_private_records"]}', "stop"),
    ('{"facts":["redemptions","redemptions"]}', "stop"),
    ('{"facts":["redemptions"],"facts":["claims"]}', "stop"),
    ('{"facts":["redemptions","claims","gross_amount"]}', "stop"),
    ('{"facts":["redemptions"]}', "length"),
    ('本期核銷 90 次', "stop"),
])
def test_unsupported_model_output_returns_quiet_null(weekly, monkeypatch, bad_response, reason):
    enable(monkeypatch)
    mock_post(monkeypatch, bad_response, reason=reason)
    client, headers, (a, _), _ = weekly
    h = headers["owner"]
    r = client.post(endpoint(a) + "/summary", json=request_body(client, a, h), headers=h)
    assert r.status_code == 200 and r.json() == {"summary": None}
    assert client.get(endpoint(a), headers=h).json()["current"]["totals"]["redemptions"] == 3


def test_zero_denominator_never_supplied_or_rendered_as_zero_conversion(weekly, monkeypatch):
    enable(monkeypatch)
    calls = mock_post(monkeypatch, '{"facts":["conversion_rate"]}')
    client, headers, (_, b), _ = weekly
    h = headers["other"]
    assert client.post(endpoint(b) + "/summary", headers=h, json=request_body(client, b, h)).json() == {"summary": None}
    payload = json.loads(calls[0][1]["json"]["messages"][1]["content"])
    assert "conversion_rate" not in payload["facts"]
    assert payload["facts"]["redemptions"]["percent"] is None
    assert client.get(endpoint(b), headers=h).json()["current"]["totals"]["conversion_rate"] is None


def test_quota_model_error_and_empty_data_never_drop_numeric_report(weekly, monkeypatch, no_real_provider):
    enable(monkeypatch)
    client, headers, (a, b), _ = weekly

    def reject(_key):
        raise HTTPException(429, "local budget")

    monkeypatch.setattr(merchant_stats, "admit", reject)
    assert client.post(endpoint(a) + "/summary", headers=headers["owner"], json=request_body(client, a, headers["owner"])).json() == {
        "summary": None}
    assert no_real_provider == []

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("details not reflected")

    monkeypatch.setattr(merchant_stats, "admit", lambda *_: None)
    monkeypatch.setattr(service.httpx, "post", timeout)
    assert client.post(endpoint(a) + "/summary", headers=headers["owner"], json=request_body(client, a, headers["owner"])).json() == {
        "summary": None}
    assert client.get(endpoint(a), headers=headers["owner"]).json()["current"]["totals"]["redemptions"] == 3

    # Shift to two periods before any claims or redemptions.
    from datetime import datetime
    from app import merchant_finance
    monkeypatch.setattr(merchant_finance, "_now", lambda: datetime.fromisoformat("2026-10-12T00:00:00+00:00"))
    monkeypatch.setattr(service.httpx, "post", lambda *a, **kw: pytest.fail("no useful facts"))
    assert client.get(endpoint(b), headers=headers["other"]).json()["summary_available"] is False
    assert client.post(endpoint(b) + "/summary", headers=headers["other"], json=request_body(client, b, headers["other"])).json() == {
        "summary": None}



def test_week_change_and_refund_revision_mismatch_never_reach_provider(
        weekly, monkeypatch, no_real_provider):
    from datetime import datetime
    from app import merchant_finance
    from app.models_packages import Order
    enable(monkeypatch)
    client, headers, (a, _), sessions = weekly
    h = headers["owner"]
    body = request_body(client, a, h)
    with sessions.begin() as db:
        db.query(Order).filter_by(refund_status="refunded").one().refund_status = "none"
    updated = request_body(client, a, h)
    assert updated["summary_revision"] != body["summary_revision"]
    assert client.post(endpoint(a) + "/summary", json=body, headers=h).json() == {"summary": None}
    monkeypatch.setattr(merchant_finance, "_now", lambda: datetime.fromisoformat("2026-11-16T00:00:00+00:00"))
    assert client.get(endpoint(a), headers=h).json()["current"]["period"]["from"] == "2026-11-09"
    assert client.post(endpoint(a) + "/summary", json=updated, headers=h).json() == {"summary": None}
    assert no_real_provider == []
