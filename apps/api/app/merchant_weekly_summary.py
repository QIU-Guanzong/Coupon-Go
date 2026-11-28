"""Optional selector for a short weekly summary, never a source of report facts."""

from hashlib import sha256
import json
import os
from decimal import Decimal
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field


class WeeklySummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: Literal["zh-Hant", "zh-Hans", "en"] = "zh-Hant"
    summary_revision: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")


_FACTS = {
    "claims": ("領取券", "领取券", "Claims"),
    "redemptions": ("核銷次數", "核销次数", "Redemptions"),
    "redeemed_claims": ("同期領取並核銷", "同期领取并核销", "Claimed and redeemed in the same week"),
    "gross_amount": ("結算口徑總額", "结算口径总额", "Settlement-basis gross"),
    "commission": ("結算口徑佣金", "结算口径佣金", "Settlement-basis commission"),
    "net_amount": ("結算口徑淨額", "结算口径净额", "Settlement-basis net"),
    "conversion_rate": ("同期領取轉化率", "同期领取转化率", "Same-week claim conversion"),
}
_MONEY_FACTS = frozenset({"gross_amount", "commission", "net_amount"})
_LANGUAGE = {"zh-Hant": 0, "zh-Hans": 1, "en": 2}


def enabled():
    return os.getenv("MERCHANT_WEEKLY_SUMMARY_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def provider_key():
    return (os.getenv("DEEPSEEK_API_KEY") or "").strip()


def aggregate_payload(report):
    """Build an explicit allowlist; never send a whole report or user text."""
    current, previous = report["current"], report["previous"]
    facts = {}
    for field in _FACTS:
        new, prior = current["totals"][field], previous["totals"][field]
        if field == "conversion_rate":
            if new is None or prior is None:
                continue
            comparison = {"percentage_points": report["changes"]["conversion_percentage_points"]}
        else:
            if not (new or prior):
                continue
            comparison = {k: report["changes"][field][k] for k in ("delta", "percent")}
        facts[field] = {"current": new, "previous": prior, **comparison}

    def period(row):
        return {k: row["period"][k] for k in ("from", "to", "days")}

    return {"timezone": "UTC", "currency": "HKD",
            "current_period": period(current), "previous_period": period(previous), "facts": facts}


def revision(shop_id, payload):
    """Keep a summary paired to the same numeric view after data/UTC changes."""
    source = json.dumps({"shop_id": shop_id, "aggregate": payload},
                        sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(source.encode("utf-8")).hexdigest()


def _strict_object(pairs):
    obj = {}
    for k, v in pairs:
        if k in obj:
            raise ValueError("duplicate JSON key")
        obj[k] = v
    return obj


def _selected_ids(content, available):
    if not isinstance(content, str) or len(content) > 512:
        return None
    parsed = json.loads(content, object_pairs_hook=_strict_object)
    ids = parsed.get("facts") if isinstance(parsed, dict) and set(parsed) == {"facts"} else None
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 2
            or any(not isinstance(field, str) or field not in available for field in ids)
            or len(set(ids)) != len(ids)):
        return None
    return ids


def _format_value(field, value):
    if field in _MONEY_FACTS:
        return f"HK${Decimal(str(value)):,.2f}"
    if field == "conversion_rate":
        return f"{Decimal(str(value)) * 100:.2f}%"
    return f"{int(value):,}"


def _render(payload, selected, language):
    parts = []
    for field in selected:
        fact = payload["facts"][field]
        title = _FACTS[field][_LANGUAGE[language]]
        new, prior = (_format_value(field, fact[key]) for key in ("current", "previous"))
        if language == "en":
            parts.append(f"{title}: {new} this period, {prior} last period.")
        else:
            parts.append(f"{title}：本期 {new}，上期 {prior}。")
    return (" " if language == "en" else "").join(parts)


def summarize(payload, language, *, key):
    """The provider can select metrics only; server renders all user text."""
    if not key or not payload["facts"]:
        return None
    try:
        response = httpx.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": "deepseek-flash",
                "messages": [
                    {"role": "system", "content": (
                        "Select one or two relevant aggregate metrics from the keys of facts. "
                        'Output only a JSON object like {"facts":["redemptions","claims"]}. '
                        "Only use keys present in the supplied facts, with no duplicates. "
                        "Never return numbers, dates, explanations, recommendations, or extra keys."
                    )},
                    {"role": "user", "content": json.dumps(payload, allow_nan=False, separators=(",", ":"))},
                ],
                "response_format": {"type": "json_object"}, "thinking": {"type": "disabled"},
                "max_tokens": 96,
            },
            timeout=httpx.Timeout(5.0, connect=2.0),
        )
        response.raise_for_status()
        choice = response.json()["choices"][0]
        if choice["finish_reason"] != "stop":
            return None
        selected = _selected_ids(choice["message"]["content"], payload["facts"])
        return _render(payload, selected, language) if selected else None
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, RecursionError):
        return None
