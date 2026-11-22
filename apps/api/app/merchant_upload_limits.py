"""Per-user upload rate limit for POST /merchant-files (P1-D item 3).

Same pattern as app/qbot_budget.py: Redis INCR + EXPIRE inside one
Lua script (atomic, one-minute window), with a bounded in-process
fallback when Redis is unavailable. The limit is
MERCHANT_UPLOAD_RATE_LIMIT_PER_MINUTE per user per minute (default
10); over it -> HTTP 429 with a Retry-After header.

reset_for_tests() clears the in-process counters; tests use it (and
fresh users) so the limiter never leaks state between tests.
"""
import hashlib
import logging
from collections import Counter
from threading import Lock
from time import monotonic, time

from fastapi import HTTPException
import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from . import config

logger = logging.getLogger(__name__)

_SCRIPT = """
local c = redis.call('INCR', KEYS[1])
if c == 1 then redis.call('EXPIRE', KEYS[1], 60) end
return c
"""

_window = -1
_counts = Counter()
_lock = Lock()
_retry_after = 0.0
_redis_client = None


def _redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(
            config.REDIS_URL, socket_connect_timeout=0.25,
            socket_timeout=0.25, retry=Retry(NoBackoff(), 0))
    return _redis_client


def _reject():
    raise HTTPException(
        status_code=429,
        detail="上傳過於頻繁，請稍後再試",
        headers={"Retry-After": "60"})


def _admit_local(digest: str, limit: int):
    global _window
    with _lock:
        minute = int(monotonic() // 60)
        if minute != _window:
            _window = minute
            _counts.clear()
        if _counts[digest] >= limit:
            _reject()
        _counts[digest] += 1


def check_upload_rate_limit(user_id: int):
    """Admit one upload for this user or raise 429."""
    global _retry_after
    limit = config.MERCHANT_UPLOAD_RATE_LIMIT_PER_MINUTE
    digest = hashlib.sha256(f"merchant-upload:{user_id}".encode()).hexdigest()
    if config.REDIS_URL and monotonic() >= _retry_after:
        slot = int(time() // 60)
        key = f"coupon-go:merchant-upload:{slot}:{digest}"
        try:
            count = _redis().eval(_SCRIPT, 1, key)
        except (redis.RedisError, ValueError):
            with _lock:
                now = monotonic()
                if now >= _retry_after:
                    logger.warning(
                        "merchant upload Redis quota unavailable; "
                        "using bounded local budget")
                    _retry_after = now + 15
        else:
            if count > limit:
                _reject()
            return
    _admit_local(digest, limit)


def reset_for_tests():
    """Clear in-process fallback counters (tests only)."""
    global _window, _retry_after
    with _lock:
        _window = -1
        _counts.clear()
        _retry_after = 0.0
