"""Payment gateway adapter — Stripe test mode (line C, 套餐 2A).

Hong Kong market: FPS （转数快） + credit card (report §14 #5).
The gateway is Stripe in TEST MODE only: no real merchant account is
connected, no real keys are stored in the repo. Behaviour:

* If STRIPE_SECRET_KEY is set, the adapter talks to the real Stripe
  test-mode API (api.stripe.com) via httpx — test keys only; a live key
  is refused (a live key must never be used by this dev build).
* If no key is set (the normal dev state), a deterministic local
  test-mode simulator stands in: it mints ``pi_test_…`` payment intents
  and ``re_test_…`` refunds with the same shapes Stripe returns, so the
  order → payment → voucher chain works end-to-end without any secret.

Payment confirmation in this build is explicit: the App (or a test)
calls POST /orders/{id}/confirm-payment with the gateway outcome
(succeeded / failed / timeout), which stands in for the Stripe webhook
until a real test key + webhook endpoint secret exist. A minimal
POST /payments/webhook accepts the same Stripe test-mode event shapes
(payment_intent.succeeded / payment_intent.payment_failed) and routes
them through the same apply function; without a webhook secret it only
accepts events whose payment_intent id was minted by this adapter.
"""
import hashlib
import hmac
import os
import time
import uuid

import httpx

from . import config

STRIPE_API = "https://api.stripe.com/v1"


def _is_test_key(key: str) -> bool:
    return key.startswith("sk_test_")


def _sim_ref(prefix: str, seed: str) -> str:
    digest = hashlib.sha256(f"{prefix}:{seed}:{uuid.uuid4().hex}".encode()).hexdigest()[:24]
    return f"{prefix}_test_{digest}"


class GatewayError(Exception):
    pass


def create_payment_intent(order_id: int, amount_hkd: float, method: str,
                          currency: str = "HKD") -> dict:
    """Create a payment intent for an order. Returns a Stripe-shaped dict:
    {gateway_ref, client_secret, status, method, amount, currency}."""
    key = config.STRIPE_SECRET_KEY
    if key:
        if not _is_test_key(key):
            raise GatewayError("只接受 Stripe 测试密钥（sk_test_…），拒绝正式密钥")
        return _stripe_create_intent(key, order_id, amount_hkd, method, currency)
    # Local test-mode simulator (no secret configured).
    return {
        "gateway_ref": _sim_ref("pi", f"{order_id}:{method}"),
        "client_secret": _sim_ref("pi", f"secret:{order_id}") + "_secret_test",
        "status": "requires_payment_method",
        "method": method,
        "amount": round(float(amount_hkd), 2),
        "currency": currency,
        "simulated": True,
    }


def refund_payment(gateway_ref: str, amount_hkd: float,
                   currency: str = "HKD") -> dict:
    """Refund a succeeded payment (原路返回）. Returns a Stripe-shaped dict."""
    key = config.STRIPE_SECRET_KEY
    if key:
        if not _is_test_key(key):
            raise GatewayError("只接受 Stripe 测试密钥（sk_test_…），拒绝正式密钥")
        return _stripe_refund(key, gateway_ref, amount_hkd, currency)
    return {
        "gateway_ref": _sim_ref("re", f"{gateway_ref}:{amount_hkd}"),
        "payment_intent": gateway_ref,
        "status": "succeeded",
        "amount": round(float(amount_hkd), 2),
        "currency": currency,
        "simulated": True,
    }


def _stripe_create_intent(key: str, order_id: int, amount_hkd: float,
                          method: str, currency: str) -> dict:
    # Stripe FPS is a payment-method type in Hong Kong; card is universal.
    # Amounts are in the smallest unit (cents for HKD).
    payload = {
        "amount": int(round(float(amount_hkd) * 100)),
        "currency": currency.lower(),
        "payment_method_types[]": "fps" if method == "fps" else "card",
        "metadata[order_id]": str(order_id),
        "metadata[app]": "coupon-go",
    }
    try:
        res = httpx.post(f"{STRIPE_API}/payment_intents", data=payload,
                         auth=(key, ""), timeout=15)
    except httpx.HTTPError as exc:
        raise GatewayError(f"Stripe 测试网关连接失败：{exc}")
    if res.status_code >= 400:
        raise GatewayError(f"Stripe 测试网关拒绝：{res.text[:200]}")
    data = res.json()
    return {
        "gateway_ref": data.get("id", ""),
        "client_secret": data.get("client_secret", ""),
        "status": data.get("status", ""),
        "method": method,
        "amount": round(float(amount_hkd), 2),
        "currency": currency,
        "simulated": False,
    }


def _stripe_refund(key: str, gateway_ref: str, amount_hkd: float,
                   currency: str) -> dict:
    payload = {
        "payment_intent": gateway_ref,
        "amount": int(round(float(amount_hkd) * 100)),
        "reason": "requested_by_customer",
    }
    try:
        res = httpx.post(f"{STRIPE_API}/refunds", data=payload,
                         auth=(key, ""), timeout=15)
    except httpx.HTTPError as exc:
        raise GatewayError(f"Stripe 测试网关连接失败：{exc}")
    if res.status_code >= 400:
        raise GatewayError(f"Stripe 测试网关拒绝：{res.text[:200]}")
    data = res.json()
    return {
        "gateway_ref": data.get("id", ""),
        "payment_intent": gateway_ref,
        "status": data.get("status", ""),
        "amount": round(float(amount_hkd), 2),
        "currency": currency,
        "simulated": False,
    }


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """Test-mode webhook check: with a configured endpoint secret this
    verifies the Stripe signature; without one it returns False and the
    webhook route falls back to accepting only adapter-minted ids."""
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        return False
    try:
        ts, _, sig = signature.partition(",v1=")
        expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload,
                            hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False


def mint_simulated_id(prefix: str = "pi") -> str:
    return _sim_ref(prefix, str(time.time()))
