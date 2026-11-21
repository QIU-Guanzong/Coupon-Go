"""M1: background scheduler — expired refunds / bargain settlement /
payment-timeout close / cashback sweep / points expiry must run on
their own, not only when an admin (or a user opening a page) happens
to trigger them.

Design
------
* In-process APScheduler (BackgroundScheduler) started from
  app.main's startup hook; one job per sweep, every
  SCHEDULER_INTERVAL_SECONDS (default 300s). Each job is independent:
  its own lock, its own session, its own failure log.
* Single-runner across instances: on Postgres the job takes
  pg_try_advisory_lock (fixed key per job, released when the job
  ends); on any other dialect it claims a row in scheduler_locks
  (INSERT ... ON CONFLICT DO NOTHING + stale-lease reclaim). A job
  that cannot take its lock logs "skipped (locked by another
  instance)" and does nothing — two instances never refund twice.
* Idempotency lives in the sweeps themselves (refund_status /
  session status / payment_status / cashback status transitions plus
  the (order_id, reason) UNIQUE): re-running a job is always safe.
* One bad item never blocks the rest: each sweep catches per item,
  logs, rolls back, counts it as failed, and retries it next run.
* Manual endpoints stay as the admin backfill path and call the
  SAME sweep functions (routers/packages.py, routers/bargain.py,
  cashback_service.py) — no duplicated logic.
* Run-once CLI for ops / cron / tests:
      python -m app.scheduler run-once [--job NAME] [--batch-size N]
      python -m app.scheduler run        # foreground scheduler
      python -m app.scheduler status     # resolved scheduler config
  run-once ignores SCHEDULER_ENABLED (it is an explicit manual run);
  the in-process scheduler honours it — see config.scheduler_status().
  Non-dev without SCHEDULER_ENABLED set does NOT silently skip: the
  startup path logs an explicit warning (validate_scheduler_config)
  and /health reports the scheduler as disabled with the reason.
"""
import argparse
import json
import logging
import os
import socket
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text

from . import config
from .database import SessionLocal
from .models_scheduler import SchedulerLock  # noqa: F401  (register table)

logger = logging.getLogger(__name__)

JOB_NAMES = ("expired-packages", "bargain-expire", "payment-timeouts",
             "cashback-sweep", "points-expire")

# Fixed Postgres advisory-lock keys, one per job (arbitrary but stable;
# changing them orphans nothing — advisory locks die with the session).
ADVISORY_LOCK_KEYS = {
    "expired-packages": 2026111701,
    "bargain-expire": 2026111702,
    "payment-timeouts": 2026111703,
    "cashback-sweep": 2026111704,
    "points-expire": 2026111705,
}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _owner():
    return f"{socket.gethostname()[:70]}:{os.getpid()}:{uuid4().hex}"


def _try_postgres_advisory_lock(db, key):
    """Use a dedicated physical connection until the job has finished.

    A Session commit returns its connection to the pool, while a Postgres
    session advisory lock survives that commit. Holding an independent
    connection avoids both early reuse and unlocking a different backend.
    """
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return None, None
    engine = bind.engine if hasattr(bind, "engine") else bind
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        locked = bool(conn.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar())
        return locked, conn
    except Exception:
        conn.invalidate()
        conn.close()
        raise


def _try_row_claim(db, job_name, owner, lease_seconds):
    """Claim the scheduler_locks row for this job. True when this
    instance now owns it; False when another (live) instance does.
    A row whose acquired_at is older than the lease is treated as a
    crashed holder's and is reclaimed."""
    now = _now()
    cutoff = now - timedelta(seconds=lease_seconds)
    claimed = db.execute(
        text("INSERT INTO scheduler_locks (name, owner, acquired_at) "
             "VALUES (:name, :owner, :now) "
             "ON CONFLICT (name) DO NOTHING"),
        {"name": job_name, "owner": owner, "now": now})
    if claimed.rowcount and claimed.rowcount > 0:
        db.commit()
        return True
    reclaimed = db.execute(
        text("UPDATE scheduler_locks SET owner = :owner, "
             "acquired_at = :now WHERE name = :name "
             "AND acquired_at < :cutoff"),
        {"owner": owner, "now": now, "name": job_name, "cutoff": cutoff})
    if reclaimed.rowcount and reclaimed.rowcount > 0:
        db.commit()
        return True
    db.rollback()
    return False


def _release_row_claim(db, job_name, owner):
    db.execute(
        text("DELETE FROM scheduler_locks "
             "WHERE name = :name AND owner = :owner"),
        {"name": job_name, "owner": owner})
    db.commit()


@contextmanager
def acquire_job_lock(db, job_name, owner=None, lease_seconds=None):
    """Yield True when this instance owns the job's lock, False when
    another instance does (job must then skip). Releases on exit."""
    owner = owner or _owner()
    lease = (lease_seconds if lease_seconds is not None
             else config.SCHEDULER_LOCK_LEASE_SECONDS)
    acquired = False
    mode = None
    lock_conn = None
    try:
        pg, lock_conn = _try_postgres_advisory_lock(db, ADVISORY_LOCK_KEYS[job_name])
        if pg is not None:
            mode = "advisory"
            acquired = pg
        else:
            mode = "row"
            acquired = _try_row_claim(db, job_name, owner, lease)
        yield acquired
    finally:
        if acquired:
            try:
                if mode == "advisory":
                    released = lock_conn.execute(
                        text("SELECT pg_advisory_unlock(:key)"),
                        {"key": ADVISORY_LOCK_KEYS[job_name]}).scalar()
                    if not released:
                        raise RuntimeError("PostgreSQL scheduler lock was not held")
                else:
                    _release_row_claim(db, job_name, owner)
            except Exception:
                logger.exception("scheduler lock release failed job=%s",
                                 job_name)
                if lock_conn is not None:
                    # Never put a connection with a possibly-held session
                    # lock back into the pool.
                    lock_conn.invalidate()
                else:
                    db.rollback()
        if lock_conn is not None:
            lock_conn.close()


def _run_job(db, job_name, sweep_fn, **kwargs):
    """Lock, sweep, log. A job that loses the lock is skipped, not
    failed; a job that raises is logged and reported failed so the
    next cycle retries it."""
    try:
        with acquire_job_lock(db, job_name) as acquired:
            if not acquired:
                logger.info("scheduler job skipped (locked by another "
                            "instance) job=%s", job_name)
                return {"skipped": True,
                        "reason": "locked_by_another_instance"}
            result = sweep_fn(db, **kwargs)
            logger.info("scheduler job done job=%s result=%s",
                        job_name, result)
            return result
    except Exception:
        logger.exception("scheduler job failed job=%s", job_name)
        try:
            db.rollback()
        except Exception:
            pass
        return {"failed": True, "error": "job failed; see server log"}


def run_expired_packages(db):
    """Expired paid package vouchers -> auto-refund / void."""
    from .routers import packages as pkg
    return _run_job(db, "expired-packages", pkg.sweep_expired_packages)


def run_bargain_expire(db):
    """Expired bargain sessions -> settle (refund difference / full)."""
    from .routers import bargain as bgn
    return _run_job(db, "bargain-expire", bgn.sweep_expired_bargains)


def run_payment_timeouts(db):
    """Pending orders past PAYMENT_TIMEOUT_MINUTES -> timeout + stock."""
    from .routers import packages as pkg
    return _run_job(db, "payment-timeouts", pkg.sweep_payment_timeouts)


def run_cashback_sweep(db, batch_size=None):
    """Cashback time transitions for all users, in batches."""
    from . import cashback_service as svc
    size = (batch_size if batch_size is not None
            else config.SCHEDULER_CASHBACK_BATCH_SIZE)
    return _run_job(db, "cashback-sweep", svc.sweep_all_users,
                    batch_size=size)


def run_points_expire(db):
    """P1-C: points grants past POINTS_EXPIRE_MONTHS -> expire the
    unspent remainder of each lot, one reason=expire ledger row."""
    from . import points_service as svc
    return _run_job(db, "points-expire", svc.sweep_expired_points)


JOB_RUNNERS = {
    "expired-packages": run_expired_packages,
    "bargain-expire": run_bargain_expire,
    "payment-timeouts": run_payment_timeouts,
    "cashback-sweep": run_cashback_sweep,
    "points-expire": run_points_expire,
}


def run_all_jobs(session_factory=None, batch_size=None):
    """Run all five sweeps once, each in its own session. Returns
    {job_name: sweep result}."""
    factory = session_factory or SessionLocal
    results = {}
    for name in ("expired-packages", "bargain-expire", "payment-timeouts",
                 "points-expire"):
        db = factory()
        try:
            results[name] = JOB_RUNNERS[name](db)
        finally:
            db.close()
    db = factory()
    try:
        results["cashback-sweep"] = run_cashback_sweep(
            db, batch_size=batch_size)
    finally:
        db.close()
    return results


def create_scheduler(session_factory=None, interval_seconds=None):
    """Build (but do not start) the in-process scheduler. One job per
    sweep; max_instances=1 + coalesce so a slow run never overlaps
    itself, misfire_grace_time=60s so a briefly-blocked trigger still
    fires once instead of piling up."""
    interval = (interval_seconds if interval_seconds is not None
                else config.SCHEDULER_INTERVAL_SECONDS)
    factory = session_factory or SessionLocal

    def _one(runner, **kwargs):
        db = factory()
        try:
            runner(db, **kwargs)
        finally:
            db.close()

    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(lambda: _one(run_expired_packages),
                  trigger=IntervalTrigger(seconds=interval),
                  id="expired-packages", max_instances=1, coalesce=True,
                  misfire_grace_time=60)
    sched.add_job(lambda: _one(run_bargain_expire),
                  trigger=IntervalTrigger(seconds=interval),
                  id="bargain-expire", max_instances=1, coalesce=True,
                  misfire_grace_time=60)
    sched.add_job(lambda: _one(run_payment_timeouts),
                  trigger=IntervalTrigger(seconds=interval),
                  id="payment-timeouts", max_instances=1, coalesce=True,
                  misfire_grace_time=60)
    sched.add_job(lambda: _one(run_cashback_sweep),
                  trigger=IntervalTrigger(seconds=interval),
                  id="cashback-sweep", max_instances=1, coalesce=True,
                  misfire_grace_time=60)
    sched.add_job(lambda: _one(run_points_expire),
                  trigger=IntervalTrigger(seconds=interval),
                  id="points-expire", max_instances=1, coalesce=True,
                  misfire_grace_time=60)
    return sched


def validate_scheduler_config():
    """Startup check: report the resolved scheduler switch, and warn
    LOUDLY (never silently) when a non-dev process has no scheduler
    configured, so nobody deploys believing refunds run automatically
    when they do not."""
    status = config.scheduler_status()
    if not status["enabled"]:
        logger.warning(
            "Scheduler disabled: %s. Expired-package refunds, bargain "
            "settlement, payment-timeout close, cashback sweep and "
            "points expiry will NOT run automatically; only the admin "
            "manual endpoints can backfill them.", status["reason"])
    else:
        logger.info("Scheduler config: %s", status["reason"])
    return status


def start_scheduler(session_factory=None):
    """Start the in-process scheduler when enabled; return the
    scheduler, or None when disabled (caller keeps None on
    app.state.scheduler)."""
    status = validate_scheduler_config()
    if not status["enabled"]:
        return None
    sched = create_scheduler(session_factory=session_factory)
    sched.start()
    logger.info("Scheduler started: %s, every %s seconds",
                status["reason"], status["interval_seconds"])
    return sched


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="app.scheduler",
        description="M1 background jobs: expired refunds, bargain "
                    "settlement, payment-timeout close, cashback sweep, "
                    "points expiry (same logic as the admin manual "
                    "endpoints).")
    sub = parser.add_subparsers(dest="command", required=True)
    p_once = sub.add_parser(
        "run-once",
        help="Run once now (all jobs or one), for ops / cron / tests")
    p_once.add_argument("--job", choices=list(JOB_NAMES) + ["all"],
                        default="all")
    p_once.add_argument("--batch-size", type=int, default=None,
                        help="Cashback sweep users per batch (default "
                             "SCHEDULER_CASHBACK_BATCH_SIZE)")
    sub.add_parser("run", help="Start the foreground scheduler (Ctrl-C quits)")
    sub.add_parser("status", help="Print the resolved scheduler config")
    args = parser.parse_args(argv)

    if args.command == "status":
        print(json.dumps(config.scheduler_status(), ensure_ascii=False,
                         indent=2))
        return 0
    if args.command == "run-once":
        # Explicit manual run: ignores SCHEDULER_ENABLED.
        if args.job == "all":
            results = run_all_jobs(batch_size=args.batch_size)
        else:
            db = SessionLocal()
            try:
                if args.job == "cashback-sweep":
                    results = {args.job: run_cashback_sweep(
                        db, batch_size=args.batch_size)}
                else:
                    results = {args.job: JOB_RUNNERS[args.job](db)}
            finally:
                db.close()
        print(json.dumps(results, ensure_ascii=False))
        # Per-item failures are reported as a count and job-level failures
        # as True. Either must be visible to cron or another supervisor.
        return int(any(result.get("failed", 0) for result in results.values()))
    # run: foreground scheduler. A disabled config is a loud failure,
    # not a silent no-op.
    sched = start_scheduler()
    if sched is None:
        print("Scheduler disabled: " + config.scheduler_status()["reason"],
              file=sys.stderr)
        return 1
    print("Scheduler running (Ctrl-C to quit) ...")
    try:
        import time
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        sched.shutdown(wait=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
