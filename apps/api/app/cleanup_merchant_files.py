"""Delete stale unattached merchant files (P1-D item 3).

Run daily, e.g. `python -m app.cleanup_merchant_files` from apps/api/
(or a cron / scheduler entry that runs the same command):

    0 3 * * * cd /path/to/apps/api && python -m app.cleanup_merchant_files

Deletes MerchantFile rows whose application_id IS NULL and whose
created_at is older than MERCHANT_UNATTACHED_MAX_AGE_DAYS (default
7 days), removing both the database row and the file on disk (via
app.merchant_file_storage). Attached files and fresh unattached
files are never touched. Idempotent: re-running deletes nothing
further, and a file already missing from disk is counted separately
instead of failing the run.
"""
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app import merchant_file_storage as storage  # noqa: E402
from app.models_line_b import MerchantFile  # noqa: E402

logger = logging.getLogger(__name__)


def cleanup_unattached_files(db, now=None) -> dict:
    """Delete stale unattached files; returns a summary dict.

    `now` is injectable for tests; defaults to current UTC.
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(days=config.MERCHANT_UNATTACHED_MAX_AGE_DAYS)
    stale = db.query(MerchantFile).filter(
        MerchantFile.application_id.is_(None),
        MerchantFile.created_at < cutoff).all()
    summary = {"deleted_files": 0, "deleted_bytes": 0,
               "missing_on_disk": 0, "cutoff": cutoff.isoformat()}
    for row in stale:
        deleted_on_disk = storage.delete(row.stored_name)
        if not deleted_on_disk:
            summary["missing_on_disk"] += 1
        summary["deleted_files"] += 1
        summary["deleted_bytes"] += row.size_bytes or 0
        db.delete(row)
    db.commit()
    return summary


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(message)s")
    db = SessionLocal()
    try:
        summary = cleanup_unattached_files(db)
    finally:
        db.close()
    print("cleanup_merchant_files: deleted "
          f"{summary['deleted_files']} file(s), "
          f"{summary['deleted_bytes']} bytes, "
          f"{summary['missing_on_disk']} already missing on disk "
          f"(cutoff {summary['cutoff']})")
    logger.info("cleanup summary: %s", summary)


if __name__ == "__main__":
    main()
