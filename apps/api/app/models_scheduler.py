"""M1: scheduler lock rows — the single-runner guard for background
jobs on non-Postgres databases.

On Postgres the scheduler uses pg_try_advisory_lock (no table needed,
released automatically when the connection drops). On SQLite / other
dialects it claims a row in this table instead (INSERT ... ON
CONFLICT DO NOTHING + stale-lease reclaim); the row is deleted when
the job finishes, and a crashed holder's row becomes reclaimable
after SCHEDULER_LOCK_LEASE_SECONDS.
"""
from sqlalchemy import Column, DateTime, String

from .database import Base


class SchedulerLock(Base):
    __tablename__ = "scheduler_locks"
    # Job name: expired-packages | bargain-expire | payment-timeouts |
    # cashback-sweep (one row per job, at most).
    name = Column(String(64), primary_key=True)
    # Which instance holds it: "hostname:pid".
    owner = Column(String(128), default="", nullable=False)
    acquired_at = Column(DateTime, nullable=True)
