"""Auth rate limits + lockouts (stage-0 security baseline, queue §I).

Keys are SHA-256 digests of scope + identity: no raw email or IP is
stored in Redis or in the in-process fallback. Redis is tried first
(same pattern as qbot_budget.admit); when Redis is unavailable the
bounded in-process store is used and Redis is not retried for 15 s,
so a down Redis never blocks login.

Lock semantics: the failure counter lives 15 minutes from the first
failure in a streak; once the threshold is reached a separate lock key
is set, so the lock runs a full 15 minutes from the lockout itself.
A successful login / OTP verification clears the streak.
"""
import hashlib
import logging
from functools import lru_cache
from threading import Lock
from time import monotonic

from fastapi import HTTPException, Request
import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from . import config

logger = logging.getLogger(__name__)

# Queue §I values (counts / seconds).
OTP_MAX_FAILURES = 5
OTP_LOCK_SECONDS = 900            # 15 minutes
LOGIN_MAX_FAILURES = 10
LOGIN_LOCK_SECONDS = 900          # 15 minutes
RESEND_MAX_PER_WINDOW = 5
RESEND_WINDOW_SECONDS = 900       # 15 minutes
FORGOT_MAX_PER_WINDOW = 5
FORGOT_WINDOW_SECONDS = 900       # 15 minutes

_INCR_WITH_TTL = """
local c = redis.call('INCR', KEYS[1])
if c == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return c
"""

# Failure streaks use a sliding window: every failure refreshes the
# window, so a slow streak (bcrypt verifies take ~0.3 s each) still
# counts as consecutive. Fixed-window caps use _INCR_WITH_TTL above.
_INCR_SLIDING = """
local c = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ARGV[1])
return c
"""

_MAX_LOCAL_ENTRIES = 10000
_local = {}  # key -> (value, expires_at_monotonic)
_lock = Lock()
_redis_unavailable_until = 0.0


@lru_cache(maxsize=1)
def _redis(url):
    return redis.Redis.from_url(url, socket_connect_timeout=0.25, socket_timeout=0.25,
                                retry=Retry(NoBackoff(), 0))


def _redis_or_none():
    """Redis client, or None when unconfigured / recently failed."""
    global _redis_unavailable_until
    url = config.REDIS_URL
    if not url or monotonic() < _redis_unavailable_until:
        return None
    try:
        client = _redis(url)
        client.ping()
        return client
    except (redis.RedisError, ValueError, OSError):
        with _lock:
            now = monotonic()
            if now >= _redis_unavailable_until:
                logger.warning("rate_limit: Redis unavailable; using bounded local store")
                _redis_unavailable_until = now + 15
        return None


def _key(scope: str, identity: str) -> str:
    digest = hashlib.sha256(f"{scope}:{identity}".encode()).hexdigest()
    return f"sec:{scope}:{digest}"


def _local_purge_locked():
    now = monotonic()
    for k in [k for k, (_, exp) in _local.items() if exp <= now]:
        del _local[k]
    while len(_local) > _MAX_LOCAL_ENTRIES:
        _local.pop(next(iter(_local)))


def _store_get(key: str):
    client = _redis_or_none()
    if client is not None:
        try:
            raw = client.get(key)
            return None if raw is None else int(raw)
        except (redis.RedisError, ValueError):
            pass
    with _lock:
        _local_purge_locked()
        item = _local.get(key)
        return None if item is None else item[0]


def _store_incr(key: str, ttl_seconds: int, sliding: bool = False) -> int:
    client = _redis_or_none()
    if client is not None:
        try:
            script = _INCR_SLIDING if sliding else _INCR_WITH_TTL
            return int(client.eval(script, 1, key, ttl_seconds))
        except (redis.RedisError, ValueError):
            pass
    with _lock:
        _local_purge_locked()
        now = monotonic()
        item = _local.get(key)
        value = (item[0] if item else 0) + 1
        expires = now + ttl_seconds if sliding or item is None else item[1]
        _local[key] = (value, expires)
        return value


def _store_set(key: str, value: int, ttl_seconds: int) -> None:
    client = _redis_or_none()
    if client is not None:
        try:
            client.set(key, value, ex=ttl_seconds)
            return
        except (redis.RedisError, ValueError):
            pass
    with _lock:
        _local_purge_locked()
        _local[key] = (value, monotonic() + ttl_seconds)


def _store_delete(key: str) -> None:
    client = _redis_or_none()
    if client is not None:
        try:
            client.delete(key)
        except (redis.RedisError, ValueError):
            pass
    with _lock:
        _local.pop(key, None)


def reset_local_state() -> None:
    """Test helper: clear the in-process fallback store."""
    global _redis_unavailable_until
    with _lock:
        _local.clear()
        _redis_unavailable_until = 0.0


def client_ip(request: Request) -> str:
    if request is not None and request.client is not None:
        return request.client.host or "unknown"
    return "unknown"


def _reject(detail: str, retry_after: int):
    raise HTTPException(429, detail, headers={"Retry-After": str(retry_after)})


def check_fixed_window(scope: str, identity: str, limit: int, window_seconds: int) -> None:
    """Fixed-window cap: at most `limit` hits per `window_seconds`."""
    count = _store_incr(_key(scope, identity), window_seconds)
    if count > limit:
        _reject("請求過於頻繁，請稍後再試", window_seconds)


# --- login: consecutive failures -> 15-min lock ---

def _login_fail_key(identity: str) -> str:
    return _key("login-fail", identity)


def _login_lock_key(identity: str) -> str:
    return _key("login-lock", identity)


def record_login_failure(identity: str) -> bool:
    """Record one failed login. Returns True when the lock trips."""
    count = _store_incr(_login_fail_key(identity), LOGIN_LOCK_SECONDS, sliding=True)
    if count >= LOGIN_MAX_FAILURES:
        _store_set(_login_lock_key(identity), 1, LOGIN_LOCK_SECONDS)
        return True
    return False


def is_login_locked(identity: str) -> bool:
    return _store_get(_login_lock_key(identity)) is not None


def reset_login_failures(identity: str) -> None:
    _store_delete(_login_fail_key(identity))
    _store_delete(_login_lock_key(identity))


# --- OTP: wrong-code streak -> void + 15-min lock ---

def _otp_fail_key(email: str, purpose: str) -> str:
    return _key("otp-fail", f"{email}|{purpose}")


def _otp_lock_key(email: str, purpose: str) -> str:
    return _key("otp-lock", f"{email}|{purpose}")


def record_otp_failure(email: str, purpose: str) -> bool:
    """Record one wrong OTP. Returns True when the lock trips."""
    count = _store_incr(_otp_fail_key(email, purpose), OTP_LOCK_SECONDS, sliding=True)
    if count >= OTP_MAX_FAILURES:
        _store_set(_otp_lock_key(email, purpose), 1, OTP_LOCK_SECONDS)
        return True
    return False


def is_otp_locked(email: str, purpose: str) -> bool:
    return _store_get(_otp_lock_key(email, purpose)) is not None


def clear_otp_attempts(email: str, purpose: str) -> None:
    _store_delete(_otp_fail_key(email, purpose))
    _store_delete(_otp_lock_key(email, purpose))
