"""Stripe Connect adapter — Accounts v2 connected accounts (P1-B).

Same conventions as app.payments_gateway (line C, FIX-1):

* If STRIPE_SECRET_KEY is set, the adapter talks to the real Stripe
  test-mode API via httpx — test keys only; a live key is refused
  (a live key must never be used by this dev build).
* If no key is set (the normal dev state), a deterministic local
  test-mode simulator stands in: it mints ``acct_test_…`` /
  ``tr_test_…`` / ``trr_test_…`` / ``po_test_…`` ids with the same
  shapes Stripe returns, so onboarding -> transfer -> payout works
  end-to-end without any secret and without a real merchant account.

Stripe API surface used (all read from the official docs 2026-11-20,
see docs/P1B_API.md):

Accounts v2 (account identity + onboarding):
* POST /v2/core/accounts — JSON body, ``Stripe-Version:
  2026-11-18.preview`` header. ``dashboard: "express"`` (the light
  Express dashboard), ``identity.country: "hk"``, configurations
  ``merchant`` (``card_payments`` + ``stripe_balance.payouts``) and
  ``recipient`` (``stripe_balance.stripe_transfers``), and
  ``defaults.responsibilities`` fees/losses = ``application``
  (required with the express dashboard; the platform is merchant of
  record and bears fees/losses). ``include`` must list
  ``configuration.merchant``, ``configuration.recipient`` and
  ``requirements`` — v2 returns anything not included as null.
* POST /v2/core/account_links — JSON body, same preview header.
  ``use_case: {type: "account_onboarding", account_onboarding:
  {configurations: ["merchant", "recipient"], collection_options:
  {fields: "eventually_due"}, return_url, refresh_url}}`` — single-use
  URL, hosted onboarding only in real browsers (not embedded webviews).
* GET /v2/core/accounts/{id}?include=... — readiness is derived from
  capability ``status`` (``active`` / ``pending`` / ``restricted`` /
  ``unsupported``), NOT from v1 ``charges_enabled`` flags, which do
  not exist on v2 objects: charges_enabled =
  merchant.card_payments.status == "active"; payouts_enabled =
  merchant.stripe_balance.payouts.status == "active";
  details_submitted = no requirements entry is currently/past due
  and awaiting action from the user. The return_url redirect is NOT a
  success event — same rule as v1.

Money movement stays on v1 (deliberate, per Stripe docs): "you can
still pass the ID of a v2 Account to an Accounts v1 API endpoint"
(docs.stripe.com/connect/accounts-v2), and the Balance Settings API
manages payouts of Accounts v2 sellers. So these are unchanged v1
calls with a v2 account id as destination / Stripe-Account:
* POST /v1/transfers {amount, currency, destination, transfer_group}
* POST /v1/transfers/{id}/reversals {amount}
* POST /v1/payouts (Stripe-Account header) {amount, currency}
* POST /v1/balance_settings (Stripe-Account header)
  {payments[payouts][schedule][interval]: manual}

Prerequisites outside this code: Accounts v2 must be enabled for the
platform in the Stripe Dashboard (Connect settings) — otherwise the
v2 endpoints return ``accounts_v2_access_blocked``. v2 emits thin
events (``v2.core.account[requirements].updated``) to event
destinations instead of classic webhooks; this PR does not add an
event listener — the sync endpoint remains the source of truth.

This module never touches FIX-1 webhook verification.
"""
import hashlib
import uuid
from decimal import Decimal, ROUND_HALF_UP

import httpx

from . import config
from .payments_gateway import GatewayError

STRIPE_API = "https://api.stripe.com/v1"
STRIPE_API_V2 = "https://api.stripe.com/v2"
# Accounts v2 API version: read from config at call time
# (config.STRIPE_V2_VERSION, env STRIPE_V2_VERSION, default
# "2026-11-18.preview"). Still a preview per Stripe's own docs
# (2026-11-21) — see docs/P1B_API.md §F5 for the risk + upgrade flow.
CENT = Decimal("0.01")

# Idempotency store for the local simulator only: Stripe dedupes on
# Idempotency-Key server-side; the simulator has no server, so it
# remembers {key: first result} in-process. Real Stripe calls never
# touch this. Tests reset it via _reset_simulator_idempotency().
_SIM_IDEMPOTENCY: dict = {}


def _reset_simulator_idempotency():
    """Test helper: clear the simulator's idempotency memory."""
    _SIM_IDEMPOTENCY.clear()


def _to_cents(amount) -> int:
    """HKD -> integer cents, Decimal only, never float. Amounts are
    already quantized to cents by the service layer; HALF_UP only
    matters for a stray sub-cent value, where it must not silently
    truncate (float 1.005 * 100 is 100.4999… -> 100)."""
    dec = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    return int((dec * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _sim_idempotent(key, prefix, seed, build):
    """Simulator-side idempotency: same key -> the first result,
    exactly like Stripe (same key + same params returns the original
    response instead of executing again)."""
    if key is not None:
        hit = _SIM_IDEMPOTENCY.get(key)
        if hit is not None:
            return dict(hit)
    result = build()
    if key is not None:
        _SIM_IDEMPOTENCY[key] = dict(result)
    return result

# Configurations applied to every merchant account: merchant (accept
# payments + payouts) and recipient (receive platform transfers).
V2_CONFIGURATIONS = ["merchant", "recipient"]
V2_INCLUDE = ["configuration.merchant", "configuration.recipient",
              "identity", "requirements"]


def _is_test_key(key: str) -> bool:
    return key.startswith("sk_test_")


def _sim_ref(prefix: str, seed: str) -> str:
    digest = hashlib.sha256(f"{prefix}:{seed}:{uuid.uuid4().hex}".encode()).hexdigest()[:24]
    return f"{prefix}_test_{digest}"


def _require_test_key() -> str:
    """Return the configured test key, or "" for simulator mode.
    Raises when a live key is configured (never use one here)."""
    key = config.STRIPE_SECRET_KEY
    if key and not _is_test_key(key):
        raise GatewayError("只接受 Stripe 测试密钥（sk_test_…），拒绝正式密钥")
    return key


def _post(key, path, data, headers=None):
    try:
        res = httpx.post(f"{STRIPE_API}{path}", data=data,
                         auth=(key, ""), headers=headers or {}, timeout=15)
    except httpx.HTTPError as exc:
        raise GatewayError(f"Stripe 测试网关连接失败：{exc}")
    if res.status_code >= 400:
        raise GatewayError(f"Stripe 测试网关拒绝：{res.text[:200]}")
    return res.json()


def _get(key, path, headers=None):
    try:
        res = httpx.get(f"{STRIPE_API}{path}", auth=(key, ""),
                        headers=headers or {}, timeout=15)
    except httpx.HTTPError as exc:
        raise GatewayError(f"Stripe 测试网关连接失败：{exc}")
    if res.status_code >= 400:
        raise GatewayError(f"Stripe 测试网关拒绝：{res.text[:200]}")
    return res.json()


def _v2_headers(key: str) -> dict:
    # v2 uses Bearer auth + an explicit preview API version header.
    # The version comes from config (env STRIPE_V2_VERSION), never
    # hard-coded here — see config.STRIPE_V2_VERSION.
    return {"Authorization": f"Bearer {key}",
            "Stripe-Version": config.STRIPE_V2_VERSION}


def _post_v2(key, path, payload):
    try:
        res = httpx.post(f"{STRIPE_API_V2}{path}", json=payload,
                         headers=_v2_headers(key), timeout=15)
    except httpx.HTTPError as exc:
        raise GatewayError(f"Stripe Accounts v2 网关连接失败：{exc}")
    if res.status_code >= 400:
        raise GatewayError(f"Stripe Accounts v2 网关拒绝：{res.text[:200]}")
    return res.json()


def _get_v2(key, path, params=None):
    try:
        res = httpx.get(f"{STRIPE_API_V2}{path}", headers=_v2_headers(key),
                        params=params or [], timeout=15)
    except httpx.HTTPError as exc:
        raise GatewayError(f"Stripe Accounts v2 网关连接失败：{exc}")
    if res.status_code >= 400:
        raise GatewayError(f"Stripe Accounts v2 网关拒绝：{res.text[:200]}")
    return res.json()


def _cap_status(configuration: dict, *path: str) -> str:
    """Capability status from a v2 configuration hash, "" when absent
    (v2 returns non-included properties as null, so be defensive)."""
    node = configuration or {}
    for key in path:
        node = node.get(key) or {}
    return node.get("status") or ""


def _parse_v2_account(data: dict) -> dict:
    """Map a v2 Account payload onto the readiness flags the service
    layer consumes (same return contract as the old v1 retrieve):
    {account_id, charges_enabled, payouts_enabled,
    details_submitted, requirements, capabilities, simulated}.

    Mapping (docs.stripe.com/connect/track-account-onboarding):
    charges_enabled = merchant card_payments active; payouts_enabled =
    merchant stripe_balance.payouts active (fallback: the same
    capability under recipient, if a payload ever carries it there);
    details_submitted = no requirements entry is awaiting action from
    the user with a currently/past due deadline.
    """
    configuration = data.get("configuration") or {}
    merchant = configuration.get("merchant") or {}
    recipient = configuration.get("recipient") or {}
    merchant_caps = merchant.get("capabilities") or {}
    recipient_caps = recipient.get("capabilities") or {}
    card_status = _cap_status(merchant_caps, "card_payments")
    payouts_status = _cap_status(merchant_caps, "stripe_balance", "payouts")
    if not payouts_status:
        payouts_status = _cap_status(recipient_caps, "stripe_balance", "payouts")
    transfers_status = _cap_status(recipient_caps, "stripe_balance",
                                   "stripe_transfers")
    requirements = data.get("requirements") or {}
    entries = requirements.get("entries") or []
    due = []
    for entry in entries:
        deadline = entry.get("minimum_deadline") or {}
        if (entry.get("awaiting_action_from") == "user"
                and deadline.get("status") in ("currently_due", "past_due")):
            due.append(entry.get("description") or "")
    return {"account_id": data.get("id", ""),
            "charges_enabled": card_status == "active",
            "payouts_enabled": payouts_status == "active",
            "details_submitted": not due,
            "requirements": due,
            "capabilities": {"card_payments": card_status,
                             "payouts": payouts_status,
                             "stripe_transfers": transfers_status},
            "simulated": False}


def create_account(email: str, country: str = "HK") -> dict:
    """Create an Accounts v2 connected account (merchant + recipient).
    Returns a Stripe-shaped dict: {account_id, charges_enabled,
    payouts_enabled, details_submitted, requirements, capabilities,
    simulated}. Fresh accounts have pending capabilities, so the
    readiness flags are False until onboarding completes."""
    key = _require_test_key()
    if not key:
        return {"account_id": _sim_ref("acct", f"{email}:{country}"),
                "charges_enabled": False, "payouts_enabled": False,
                "details_submitted": False, "requirements": [],
                "capabilities": {"card_payments": "pending",
                                 "payouts": "pending",
                                 "stripe_transfers": "pending"},
                "simulated": True}
    data = _post_v2(key, "/core/accounts", {
        "contact_email": email,
        "display_name": email,
        "dashboard": "express",
        "identity": {"country": country.lower()},
        "configuration": {
            "merchant": {"capabilities": {
                "card_payments": {"requested": True},
                "stripe_balance": {"payouts": {"requested": True}},
            }},
            "recipient": {"capabilities": {
                "stripe_balance": {"stripe_transfers": {"requested": True}},
            }},
        },
        "defaults": {
            "currency": "hkd",
            "responsibilities": {
                # Required with dashboard=express: the platform
                # collects fees and bears losses (merchant of record).
                "fees_collector": "application",
                "losses_collector": "application",
            },
        },
        "include": V2_INCLUDE,
    })
    return _parse_v2_account(data)


def create_account_link(account_id: str, refresh_url: str,
                        return_url: str) -> dict:
    """Create a single-use v2 onboarding Account Link.
    Returns {url, expires_at, simulated}."""
    key = _require_test_key()
    if not key:
        return {"url": f"https://accounts.stripe.test/r/{account_id}",
                "expires_at": None, "simulated": True}
    data = _post_v2(key, "/core/account_links", {
        "account": account_id,
        "use_case": {
            "type": "account_onboarding",
            "account_onboarding": {
                "configurations": V2_CONFIGURATIONS,
                "collection_options": {"fields": "eventually_due"},
                "refresh_url": refresh_url,
                "return_url": return_url,
            },
        },
    })
    return {"url": data.get("url", ""), "expires_at": data.get("expires_at"),
            "simulated": False}


def retrieve_account(account_id: str) -> dict:
    """Retrieve a connected account's onboarding state — the source of
    truth for charges/payouts readiness. The simulator assumes
    onboarding completed (tests monkeypatch this to simulate the
    incomplete state). Returns {account_id, charges_enabled,
    payouts_enabled, details_submitted, requirements, capabilities,
    simulated}."""
    key = _require_test_key()
    if not key:
        return {"account_id": account_id, "charges_enabled": True,
                "payouts_enabled": True, "details_submitted": True,
                "requirements": [],
                "capabilities": {"card_payments": "active",
                                 "payouts": "active",
                                 "stripe_transfers": "active"},
                "simulated": True}
    data = _get_v2(key, f"/core/accounts/{account_id}",
                   params=[("include", name) for name in V2_INCLUDE])
    return _parse_v2_account(data)


def set_manual_payouts(account_id: str) -> dict:
    """Set the connected account's payout schedule to manual, so
    withdrawals only happen through our payout endpoint. Returns
    {manual, simulated}. Uses the v1 Balance Settings API (Stripe
    docs: it manages payouts of Accounts v2 sellers; the v2 account
    id is passed as Stripe-Account)."""
    key = _require_test_key()
    if not key:
        return {"manual": True, "simulated": True}
    _post(key, "/balance_settings",
          {"payments[payouts][schedule][interval]": "manual"},
          headers={"Stripe-Account": account_id})
    return {"manual": True, "simulated": False}


def create_transfer(account_id: str, amount_hkd,
                    transfer_group: str, currency: str = "HKD",
                    idempotency_key: str = None) -> dict:
    """Transfer funds from the platform balance to a connected account
    (separate charges and transfers). v1 Transfers API with the v2
    account id as destination (Stripe v2/v1 interop). Returns
    {transfer_id, status, amount, currency, simulated}.

    Idempotency (queue §F item 1): callers pass a stable key derived
    from the ledger entry id (``transfer-{entry_id}``); the same key
    is reused on every retry, so a first request that timed out at
    Stripe but already executed never transfers twice. The real path
    sends ``Idempotency-Key``; the simulator dedupes in-process."""
    key = _require_test_key()
    amount_dec = (amount_hkd if isinstance(amount_hkd, Decimal)
                  else Decimal(str(amount_hkd)))
    if not key:
        return _sim_idempotent(
            idempotency_key, "tr", f"{account_id}:{transfer_group}",
            lambda: {"transfer_id": _sim_ref(
                "tr", f"{account_id}:{transfer_group}"),
                "status": "paid", "amount": float(amount_dec),
                "currency": currency, "simulated": True})
    data = _post(key, "/transfers", {
        "amount": _to_cents(amount_dec),
        "currency": currency.lower(),
        "destination": account_id,
        "transfer_group": transfer_group,
    }, headers=({"Idempotency-Key": idempotency_key}
                if idempotency_key else None))
    return {"transfer_id": data.get("id", ""), "status": data.get("status", ""),
            "amount": (data.get("amount") or 0) / 100,
            "currency": (data.get("currency") or currency).upper(),
            "simulated": False}


def reverse_transfer(transfer_id: str, amount_hkd,
                     currency: str = "HKD",
                     idempotency_key: str = None) -> dict:
    """Reverse a transfer (full or partial). v1 Transfer Reversals API.
    Returns {reversal_id, status, amount, currency, simulated}.
    Idempotency key: ``reversal-{entry_id}`` (see create_transfer)."""
    key = _require_test_key()
    amount_dec = (amount_hkd if isinstance(amount_hkd, Decimal)
                  else Decimal(str(amount_hkd)))
    if not key:
        return _sim_idempotent(
            idempotency_key, "trr", f"{transfer_id}:{amount_dec}",
            lambda: {"reversal_id": _sim_ref(
                "trr", f"{transfer_id}:{amount_dec}"),
                "status": "succeeded", "amount": float(amount_dec),
                "currency": currency, "simulated": True})
    data = _post(key, f"/transfers/{transfer_id}/reversals", {
        "amount": _to_cents(amount_dec),
    }, headers=({"Idempotency-Key": idempotency_key}
                if idempotency_key else None))
    return {"reversal_id": data.get("id", ""), "status": data.get("status", ""),
            "amount": (data.get("amount") or 0) / 100,
            "currency": (data.get("currency") or currency).upper(),
            "simulated": False}


def create_payout(account_id: str, amount_hkd,
                  currency: str = "HKD",
                  idempotency_key: str = None) -> dict:
    """Pay out from a connected account's balance to its external
    (bank) account. v1 Payouts API with the v2 account id as
    Stripe-Account. Returns {payout_id, status, amount, currency,
    simulated}. Idempotency key: ``payout-{entry_id}``."""
    key = _require_test_key()
    amount_dec = (amount_hkd if isinstance(amount_hkd, Decimal)
                  else Decimal(str(amount_hkd)))
    if not key:
        return _sim_idempotent(
            idempotency_key, "po", f"{account_id}:{amount_dec}",
            lambda: {"payout_id": _sim_ref(
                "po", f"{account_id}:{amount_dec}"),
                "status": "paid", "amount": float(amount_dec),
                "currency": currency, "simulated": True})
    data = _post(key, "/payouts", {
        "amount": _to_cents(amount_dec),
        "currency": currency.lower(),
    }, headers={"Stripe-Account": account_id, **(
        {"Idempotency-Key": idempotency_key} if idempotency_key else {})})
    return {"payout_id": data.get("id", ""), "status": data.get("status", ""),
            "amount": (data.get("amount") or 0) / 100,
            "currency": (data.get("currency") or currency).upper(),
            "simulated": False}
