"""Short-lived redemption confirmation bound to the wallet item, clerk and code.

The token carries the item ID, expiry, issue time and HMAC. The signature
also covers the clerk and code, supplied separately when confirming.
"""
import base64
import hashlib
import hmac
import time

from . import config


def _sign(payload: str) -> str:
    return hmac.new(config.SECRET_KEY.encode(), payload.encode(),
                    hashlib.sha256).hexdigest()


def redemption_binding(vendor_id: int, code: str, branch_id: int | None) -> str:
    """Keep legacy unrestricted tokens valid; bind selected branches explicitly."""
    binding = f"{vendor_id}:{code}"
    return binding if branch_id is None else f"{binding}:branch:{branch_id}"


def issue_prepare_token(wallet_item_id: int,
                        *, binding: str, ttl_seconds: int = None) -> tuple[str, int]:
    """Return (token, prepared_at_unix)."""
    ttl = config.PREPARE_TOKEN_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    now = int(time.time())
    exp = now + ttl
    payload = f"{wallet_item_id}:{exp}:{now}"
    signature = _sign(f"{payload}:{binding}")
    raw = f"{payload}:{signature}".encode()
    token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return token, now


def verify_prepare_token(token: str, wallet_item_id: int, *, binding: str) -> int | None:
    """Return prepared_at (unix) if the token is valid for this item, else None."""
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded).decode()
        item_id_s, exp_s, issued_s, sig = raw.split(":")
        if int(item_id_s) != wallet_item_id:
            return None
        if int(exp_s) < int(time.time()):
            return None
        if not hmac.compare_digest(
                _sign(f"{item_id_s}:{exp_s}:{issued_s}:{binding}"), sig):
            return None
        return int(issued_s)
    except Exception:
        return None
