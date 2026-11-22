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

Payment confirmation in this build is gateway-driven (FIX-1): with a
Stripe test key configured, POST /orders/{id}/confirm-payment ignores
the client-supplied outcome and settles only after retrieving the
payment_intent status from Stripe; without a key the client-supplied
outcome is accepted only when APP_ENV=dev. POST /payments/webhook
verifies the Stripe-Signature against STRIPE_WEBHOOK_SECRET (with a
timestamp tolerance) whenever a key or secret is configured, and is
refused outright outside dev when neither is configured. The local
simulator's ``pi_test_…`` ids are server-side only and are never
returned to clients (see serializers_packages.payment_out).
"""
import hashlib
import hmac
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
    if config.APP_ENV != "dev":
        raise GatewayError("付款網關未配置")
    # Local test-mode simulator (no secret configured).
    # NOTE (FIX-1): the client_secret is an opaque cs_test_ token that
    # does NOT embed the pi_test_ id, so the forgeable intent id never
    # reaches the client in any field.
    return {
        "gateway_ref": _sim_ref("pi", f"{order_id}:{method}"),
        "client_secret": _sim_ref("cs", f"secret:{order_id}:{method}"),
        "status": "requires_payment_method",
        "method": method,
        "amount": round(float(amount_hkd), 2),
        "currency": currency,
        "simulated": True,
    }


def refund_payment(gateway_ref: str, amount_hkd: float,
                   currency: str = "HKD", *, request_key: str | None = None) -> dict:
    """Refund a succeeded payment (原路返回）. Returns a Stripe-shaped dict."""
    key = config.STRIPE_SECRET_KEY
    if key:
        if not _is_test_key(key):
            raise GatewayError("只接受 Stripe 测试密钥（sk_test_…），拒绝正式密钥")
        return _stripe_refund(key, gateway_ref, amount_hkd, currency, request_key)
    if config.APP_ENV != "dev" or (request_key and not gateway_ref.startswith("pi_test_")):
        raise GatewayError("退款需要相符的支付網關")
    refund_ref = _sim_ref("re", f"{gateway_ref}:{amount_hkd}") if not request_key else (
        "re_test_" + hashlib.sha256(f"{gateway_ref}:{request_key}".encode()).hexdigest()[:24])
    return {
        "gateway_ref": refund_ref,
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
                   currency: str, request_key=None) -> dict:
    payload = {
        "payment_intent": gateway_ref,
        "amount": int(round(float(amount_hkd) * 100)),
        "reason": "requested_by_customer",
    }
    try:
        res = httpx.post(f"{STRIPE_API}/refunds", data=payload,
                         headers={"Idempotency-Key": request_key} if request_key else {},
                         auth=(key, ""), timeout=15)
    except httpx.HTTPError as exc:
        raise GatewayError(f"Stripe 测试网关连接失败：{exc}")
    if res.status_code >= 400:
        raise GatewayError(f"Stripe 测试网关拒绝：{res.text[:200]}")
    data = res.json()
    return _refund_result(data)


def _refund_result(data):
    return {"gateway_ref": data.get("id", ""), "payment_intent": data.get("payment_intent"),
            "status": data.get("status", ""), "amount": data.get("amount", 0) / 100,
            "currency": data.get("currency", ""), "simulated": False}


def retrieve_refund(ref):
    key = config.STRIPE_SECRET_KEY
    if not _is_test_key(key):
        raise GatewayError("退款狀態需要相符的測試網關")
    try:
        response = httpx.get(f"{STRIPE_API}/refunds/{ref}", auth=(key, ""), timeout=15)
        response.raise_for_status()
        return _refund_result(response.json())
    except httpx.HTTPError as exc:
        raise GatewayError("未能確認退款狀態") from exc


def retrieve_payment_intent(gateway_ref: str) -> dict:
    """FIX-1: ask the gateway for a payment_intent's current status.
    Only meaningful with a Stripe test key configured; the simulator
    keeps no server-side intent state, so without a key this raises.
    Returns a Stripe-shaped dict: {gateway_ref, status, ...} where
    status is Stripe's status (succeeded / canceled /
    requires_payment_method / requires_action / processing / ...)."""
    key = config.STRIPE_SECRET_KEY
    if not key:
        raise GatewayError("未配置 Stripe 密鑰，無法向網關查詢支付狀態")
    if not _is_test_key(key):
        raise GatewayError("只接受 Stripe 測試密鑰（sk_test_…），拒絕正式密鑰")
    try:
        res = httpx.get(f"{STRIPE_API}/payment_intents/{gateway_ref}",
                        auth=(key, ""), timeout=15)
    except httpx.HTTPError as exc:
        raise GatewayError(f"Stripe 測試網關連接失敗：{exc}")
    if res.status_code >= 400:
        raise GatewayError(f"Stripe 測試網關拒絕：{res.text[:200]}")
    data = res.json()
    return {
        "gateway_ref": data.get("id", gateway_ref),
        "status": data.get("status", ""),
        "amount": data.get("amount"),
        "currency": (data.get("currency") or "HKD").upper(),
        "simulated": False,
    }


def verify_webhook_signature(payload: bytes, signature: str,
                             tolerance_seconds: int = None) -> bool:
    """FIX-1: verify a Stripe webhook signature.

    Header format: ``t=<unix ts>,v1=<hex hmac>,...`` (possibly several
    v1 values). The signed payload is ``f"{ts}.{raw_body}"`` HMAC-SHA256
    with STRIPE_WEBHOOK_SECRET (read from config, not os.environ, so
    tests can monkeypatch it). The timestamp must be within the
    tolerance (default config.STRIPE_WEBHOOK_TOLERANCE_SECONDS, 300s).
    Returns False when no secret is configured, the header is missing
    or malformed, the timestamp is stale, or no v1 signature matches.
    """
    secret = config.STRIPE_WEBHOOK_SECRET
    if not secret or not signature:
        return False
    if tolerance_seconds is None:
        tolerance_seconds = config.STRIPE_WEBHOOK_TOLERANCE_SECONDS
    try:
        parts = {}
        for item in signature.split(","):
            key, _, value = item.partition("=")
            if key and value:
                parts.setdefault(key.strip(), []).append(value.strip())
        ts_values = parts.get("t") or []
        sig_values = parts.get("v1") or []
        if not ts_values or not sig_values:
            return False
        ts = int(ts_values[0])
        if abs(int(time.time()) - ts) > int(tolerance_seconds):
            return False
        expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload,
                            hashlib.sha256).hexdigest()
        return any(hmac.compare_digest(expected, sig) for sig in sig_values)
    except (ValueError, TypeError):
        return False


def mint_simulated_id(prefix: str = "pi") -> str:
    return _sim_ref(prefix, str(time.time()))
