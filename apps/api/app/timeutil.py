"""One clock for the whole backend.

Every module used to define its own identical ``_now()`` (18 copies:
routers, services, models). They all mean the same thing — naive UTC
now, the timezone the database columns store — so there is exactly
one implementation here. Modules keep the local name with::

    from .timeutil import utcnow as _now       # app/ package
    from ..timeutil import utcnow as _now      # app/routers/

so call sites do not change and column ``default=_now`` keeps working.
"""
from datetime import datetime, timezone


def utcnow():
    """Naive UTC now, matching the DateTime columns (no tzinfo stored)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
