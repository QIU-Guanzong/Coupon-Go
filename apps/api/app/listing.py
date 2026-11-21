"""Dianping listing (§3): one shared listing lifecycle for coupons and
packages, plus the scheduled-listing sweep.

Status machine (both kinds; package template coupons mirror the package):
    draft -> pending_review -> active | scheduled | rejected
    active <-> paused
    active / paused / scheduled / pending_review -> draft (unlist /
        withdraw; merchant edits, then submits again)
    rejected -> pending_review (resubmit) | draft (edit)
    active -> expired (valid_to passed) | sold_out (stock exhausted)
    scheduled -> active (valid_from reached, via the sweep)

`scheduled` means "approved, waiting for valid_from": it is never
claimable / purchasable (public lists filter status == "active", and
claim / purchase both require "active"). The sweep is the only writer
of scheduled -> active, expired and sold_out.

Refund policy, sale price and stock share one calibre across kinds:
- sale_price NULL / 0 = free (claim as before); > 0 = paid (P1-A's
  purchase endpoint buys it; free claim is refused). This module does
  NOT build the purchase flow.
- refund_policy is anytime | expiry_only | non_refundable on both.
- remaining / stock_left hitting 0 means sold_out; valid_to passing
  means expired. Neither is ever silently re-listed.
"""
import logging

from sqlalchemy.orm import Session

from . import models
from .models_line_b import AdminAuditLog
from .models_packages import Package, REFUND_POLICIES
from .timeutil import utcnow as _now

logger = logging.getLogger(__name__)

# Unify: the refund-policy triple lives once, in models_packages
# (routers/packages.py used to define its own copy of the same tuple);
# this module re-exports it so listing callers keep one import.

# States a merchant may edit from (see vendor / package edit endpoints).
EDITABLE_STATES = ("draft", "rejected", "pending_review", "active",
                   "paused", "scheduled")
# States an edit sends back to review (approved but changed content).
RE_REVIEW_STATES = ("active", "paused", "scheduled")


def audit(db: Session, admin_id: int, action: str, target_type: str,
          target_id: int, detail: str = ""):
    """Append one AdminAuditLog row. Call AFTER the listing change's
    own commit: the audit is a separate commit, so a logging failure
    can never roll back (or block) the action it records."""
    try:
        db.add(AdminAuditLog(admin_id=admin_id, action=action,
                             target_type=target_type, target_id=target_id,
                             detail=detail or ""))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("listing audit log failed action=%s target=%s:%s",
                         action, target_type, target_id)


def live_status(valid_from, valid_to, stock_left: int) -> str:
    """The status an approved item should hold right now."""
    now = _now()
    if valid_to is not None and valid_to <= now:
        return "expired"
    if stock_left is not None and stock_left <= 0:
        return "sold_out"
    if valid_from is not None and valid_from > now:
        return "scheduled"
    return "active"


def approve_status(valid_from, valid_to, stock_left: int) -> str:
    """Status an approval lands on (400-worthy cases are checked by
    the caller: valid_to already passed is rejected there)."""
    return live_status(valid_from, valid_to, stock_left)


def sync_package_coupon_status(db: Session, package: Package, status: str):
    """The package template coupon (kind='package') mirrors the
    package status, so scan / claim guards that read the coupon see
    the same state as the listing."""
    coupon = (db.query(models.Coupon)
              .filter(models.Coupon.package_id == package.id,
                      models.Coupon.kind == "package")
              .first())
    if coupon is not None:
        coupon.status = status
    return coupon


def sweep_listing_schedule(db: Session) -> dict:
    """One pass of the scheduled-listing job. Idempotent: every
    transition moves a row out of the selected set, so a re-run is a
    no-op. Per-row failures are logged and counted, never blocking
    the rest (same pattern as the M1 sweeps)."""
    stats = {"coupons_activated": 0, "coupons_expired": 0,
             "coupons_sold_out": 0, "coupons_rescheduled": 0,
             "packages_activated": 0, "packages_expired": 0,
             "packages_sold_out": 0, "packages_rescheduled": 0,
             "failed": 0}
    now = _now()

    def _run(rows, fn):
        for row in rows:
            try:
                fn(row)
                db.commit()
            except Exception:
                db.rollback()
                stats["failed"] += 1
                logger.exception("listing sweep failed id=%s", row.id)

    # --- coupons ---
    _run(db.query(models.Coupon)
         .filter(models.Coupon.kind == "coupon",
                 models.Coupon.status == "scheduled",
                 models.Coupon.valid_from <= now,
                 models.Coupon.valid_to > now).all(),
         lambda c: _set(c, live_status(c.valid_from, c.valid_to, c.remaining),
                        stats))
    # Legacy / edited rows that are active before their valid_from
    # (claim already refuses them) go back to scheduled, not live.
    _run(db.query(models.Coupon)
         .filter(models.Coupon.kind == "coupon",
                 models.Coupon.status == "active",
                 models.Coupon.valid_from > now).all(),
         lambda c: _set(c, "scheduled", stats))
    _run(db.query(models.Coupon)
         .filter(models.Coupon.kind == "coupon",
                 models.Coupon.status.in_(("active", "scheduled", "paused")),
                 models.Coupon.valid_to <= now).all(),
         lambda c: _set(c, "expired", stats))
    _run(db.query(models.Coupon)
         .filter(models.Coupon.kind == "coupon",
                 models.Coupon.status == "active",
                 models.Coupon.remaining <= 0).all(),
         lambda c: _set(c, "sold_out", stats))

    # --- packages (+ their template coupons) ---
    _run(db.query(Package)
         .filter(Package.status == "scheduled",
                 Package.valid_from <= now,
                 Package.valid_to > now).all(),
         lambda p: _set_pkg(db, p, live_status(p.valid_from, p.valid_to,
                                               p.stock_left), stats))
    _run(db.query(Package)
         .filter(Package.status == "active",
                 Package.valid_from > now).all(),
         lambda p: _set_pkg(db, p, "scheduled", stats))
    _run(db.query(Package)
         .filter(Package.status.in_(("active", "scheduled", "paused")),
                 Package.valid_to <= now).all(),
         lambda p: _set_pkg(db, p, "expired", stats))
    _run(db.query(Package)
         .filter(Package.status == "active",
                 Package.stock_left <= 0).all(),
         lambda p: _set_pkg(db, p, "sold_out", stats))
    return stats


def _set(row, status: str, stats: dict):
    """Set a coupon status and count the outcome (live_status can
    land on expired / sold_out instead of active when the row's
    dates / stock changed between select and update)."""
    row.status = status
    if status == "active":
        stats["coupons_activated"] += 1
    elif status == "expired":
        stats["coupons_expired"] += 1
    elif status == "sold_out":
        stats["coupons_sold_out"] += 1
    elif status == "scheduled":
        stats["coupons_rescheduled"] += 1


def _set_pkg(db: Session, package: Package, status: str, stats: dict):
    package.status = status
    sync_package_coupon_status(db, package, status)
    if status == "active":
        stats["packages_activated"] += 1
    elif status == "expired":
        stats["packages_expired"] += 1
    elif status == "sold_out":
        stats["packages_sold_out"] += 1
    elif status == "scheduled":
        stats["packages_rescheduled"] += 1
