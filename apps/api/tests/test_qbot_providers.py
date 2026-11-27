import httpx
import pytest

from app import qbot_providers as providers


@pytest.fixture
def configured(monkeypatch):
    for name in ("DEEPSEEK_API_KEY", "QBOT_GEMINI_API_KEY", "QBOT_OPENAI_API_KEY"):
        monkeypatch.setenv(name, f"fixture-{name}")
    return providers.configured_providers(True)


def test_old_clients_and_missing_keys(configured, monkeypatch):
    assert [item[0] for item in providers.configured_providers(False)] == ["DeepSeek"]
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    assert [item[0] for item in providers.configured_providers(True)] == ["Gemini", "OpenAI"]
    assert providers.configured_providers(False) == []


def test_provider_timeout_and_billing_error_fall_through(configured, monkeypatch):
    requests = []

    def send(url, **options):
        requests.append(options)
        req = httpx.Request("POST", url)
        if len(requests) == 1:
            raise httpx.ReadTimeout("test timeout", request=req)
        if len(requests) == 2:
            return httpx.Response(402, request=req)
        return httpx.Response(200, request=req, json={
            "choices": [{"message": {"content": "打開 Q Wallet。"}, "finish_reason": "stop"}],
        })

    monkeypatch.setattr(providers.httpx, "post", send)
    assert providers.answer(configured, "test policy", "public test question") == "打開 Q Wallet。"
    assert [request["json"]["model"] for request in requests] == [p[3] for p in configured]
    assert requests[-1]["json"]["store"] is False
    assert all(request["json"]["messages"][1]["content"] == "public test question" for request in requests)


def test_content_refusal_does_not_call_another_provider(configured, monkeypatch):
    calls = []

    def send(url, **_):
        calls.append(url)
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"message": {"content": None}, "finish_reason": "content_filter"}],
        })

    monkeypatch.setattr(providers.httpx, "post", send)
    with pytest.raises(providers.ProviderUnavailable):
        providers.answer(configured, "test policy", "public test question")
    assert len(calls) == 1
