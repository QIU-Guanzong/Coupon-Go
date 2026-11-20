"""Atomic minute counters; no IP or chat content stored in Redis."""
from collections import Counter
from functools import lru_cache
import hashlib
import logging
from threading import Lock
from time import monotonic, time

from fastapi import HTTPException
import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from . import config

logger = logging.getLogger(__name__)
_SCRIPT = """
local per = tonumber(redis.call('GET', KEYS[1]) or '0')
local total = tonumber(redis.call('GET', KEYS[2]) or '0')
if per >= 8 or total >= 60 then return 0 end
if redis.call('INCR', KEYS[1]) == 1 then redis.call('EXPIRE', KEYS[1], 60) end
if redis.call('INCR', KEYS[2]) == 1 then redis.call('EXPIRE', KEYS[2], 60) end
return 1
"""
_window = -1
_counts = Counter()
_lock = Lock()
_retry_after = 0.0


@lru_cache(maxsize=1)
def _redis(url):
    return redis.Redis.from_url(url, socket_connect_timeout=0.25, socket_timeout=0.25,
                                retry=Retry(NoBackoff(), 0))


def _reject():
    raise HTTPException(429, "問題較多，請稍後再試", headers={"Retry-After": "60"})


def _admit_local(identity):
    global _window
    with _lock:
        minute = int(monotonic() // 60)
        if minute != _window:
            _window = minute
            _counts.clear()
        # Do not add denied caller keys. Counter cardinality stays bounded.
        if _counts[identity] >= 8 or _counts.total() >= 60:
            _reject()
        _counts[identity] += 1


def admit(identity):
    global _retry_after
    digest = hashlib.sha256(identity.encode()).hexdigest()
    if config.REDIS_URL and monotonic() >= _retry_after:
        slot = int(time() // 60)
        # Same hash slot for both keys permits an atomic Redis Lua transaction.
        prefix = f"coupon-go:{{qbot}}:{slot}"
        try:
            allowed = _redis(config.REDIS_URL).eval(
                _SCRIPT, 2, f"{prefix}:client:{digest}", f"{prefix}:total")
        except (redis.RedisError, ValueError):
            with _lock:
                now = monotonic()
                if now >= _retry_after:
                    logger.warning("Qbot Redis quota unavailable; using bounded local budget")
                    _retry_after = now + 15
        else:
            if not allowed:
                _reject()
            return
    _admit_local(digest)
