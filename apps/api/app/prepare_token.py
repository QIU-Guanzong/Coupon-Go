"""Stateless prepare token for the two-step redemption flow.

prepare (read-only check) returns a short-lived signed token binding a
wallet item to an expiry time. consume() verifies the token and records
its issued time as the redemption's prepared_at. No DB row is written
at prepare time — prepare really is read-only (report §5.4 / §7.1).

Token format: base64url("<wallet_item_id>:<exp_unix>:<hmac_hex>"), HMAC-SHA256
keyed by SECRET_KEY. Single-use is enforced indirectly: consume clears the
wallet item's code, so a replayed token finds no usable code.
"""
import base64
import hashlib
import hmac
import time

from . import config


def _sign(payload: str) -> str:
    return hmac.new(config.SECRET_KEY.encode(), payload.encode(),
                    hashlib.sha256).hexdigest()


def issue_prepare_token(wallet_item_id: int,
                        ttl_seconds: int = None) -> tuple[str, int]:
    """Return (token, prepared_at_unix)."""
    ttl = config.PREPARE_TOKEN_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    now = int(time.time())
    exp = now + ttl
    payload = f"{wallet_item_id}:{exp}:{now}"
    raw = f"{payload}:{_sign(payload)}".encode()
    token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return token, now


def verify_prepare_token(token: str, wallet_item_id: int) -> int | None:
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
                _sign(f"{item_id_s}:{exp_s}:{issued_s}"), sig):
            return None
        return int(issued_s)
    except Exception:
        return None
