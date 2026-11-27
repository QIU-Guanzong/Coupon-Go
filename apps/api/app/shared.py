"""Cross-cutting convergence helpers (dianping unify, GAP §5).

One home for the helpers that purchase / redemption / refund / stats
used to re-implement per module, so a fix lands once:

- :func:`paginate` — the one page envelope every list endpoint
  returns (``admin._page`` and ``admin_v2._page`` were byte-identical
  copies; wallet and review lists hand-rolled the same shape).
  Standard query params: ``page`` (``Query(1, ge=1)``) and
  ``page_size`` (``Query(20, ge=1, le=100)``).
- :func:`vendor_shop_ids` — the shops one vendor owns (was an inline
  list comprehension in coupons / vendor / home / admin_v2 and a
  private ``_vendor_shop_ids`` in redemption).
- :func:`money` — the one JSON money style: HKD amounts are floats
  rounded to 2dp (HALF_UP). Serializers already emitted floats;
  this makes the rounding explicit and shared (bargain's Decimal
  quantize and admin_v2's float cast were two spellings of it).
- :func:`issue_wallet_item` — the one voucher-issuance entry: free
  claim, package purchase (via ``packages._issue_voucher``),
  bargain purchase and points exchange all mint the WalletItem
  here, with the provenance ``source`` the model documents
  (``claim`` | ``purchase`` | ``bargain`` | ``exchange``).
  Note: points exchange used to rely on the column default and
  stored ``claim``; it now stores ``exchange``.
- :func:`audit_log` — one AdminAuditLog row in the caller's own
  transaction (flush, no commit). Distinct from
  ``listing.audit`` (separate commit, for post-commit audit of
  listing changes) — same table, different durability contract.
- :func:`csv_response` — the one CSV export writer (csv module with
  quoting; the redemption export hand-rolled ``",".join`` and broke
  on commas in names). Header row is exactly the columns given.
"""
import csv
import io
from decimal import Decimal, ROUND_HALF_UP

from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from . import models

CENT = Decimal("0.01")


def paginate(items, page: int, page_size: int = 20) -> dict:
    """Slice an already-materialised list into the standard envelope.

    ``{"current_page", "total_pages", "total", "items"}`` — the shape
    every list endpoint already returned; callers rename ``items``
    to their resource key (``shops``, ``coupons``, ``logs``, ...).
    """
    total = len(items)
    return {
        "current_page": page,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "total": total,
        "items": items[(page - 1) * page_size: page * page_size],
    }


def vendor_shop_ids(db: Session, user: models.User) -> list:
    """Ids of the shops ``user`` owns directly (vendor_id == user.id)."""
    return [s.id for s in db.query(models.Shop).filter(
        models.Shop.vendor_id == user.id).all()]


def money(value) -> float:
    """One JSON money style: float, rounded to 2dp (HALF_UP).

    ``None`` -> ``0.0`` (same as admin_v2's ``_money``).
    """
    if value is None:
        return 0.0
    return float(Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP))


def issue_wallet_item(db: Session, user_id: int, coupon_id: int,
                      source: str, order_id=None) -> models.WalletItem:
    """Mint one unused wallet item. Flushes; the caller commits.

    ``source`` is one of ``claim`` | ``purchase`` | ``bargain`` |
    ``exchange`` | ``gift`` (see ``WalletItem.source``).
    """
    item = models.WalletItem(user_id=user_id, coupon_id=coupon_id,
                             status=1, source=source, order_id=order_id)
    db.add(item)
    db.flush()
    return item


def audit_log(db: Session, admin_id: int, action: str, target_type: str,
              target_id, detail: str = ""):
    """Append one AdminAuditLog row in the caller's transaction.

    Flush only — a failed caller transaction never leaves an orphan
    audit row behind (contrast ``listing.audit``, which commits
    separately after a listing change).
    """
    from .models_line_b import AdminAuditLog
    db.add(AdminAuditLog(admin_id=admin_id, action=action,
                         target_type=target_type, target_id=target_id,
                         detail=detail))
    db.flush()


_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_csv_cell(value):
    """Neutralize spreadsheet formula injection on string cells."""
    if isinstance(value, str) and value and value[0] in _CSV_FORMULA_PREFIXES:
        return "'" + value
    return value


def csv_response(filename: str, headers: list, rows: list) -> PlainTextResponse:
    """One CSV writer for every export (GAP §5.9).

    ``headers`` are the column names (also the first row);
    ``rows`` are lists of values in the same order. Quoting follows
    the csv module, so commas/quotes in names survive. No BOM —
    byte-compatible with the existing redemption export.
    """
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows([sanitize_csv_cell(cell) for cell in row] for row in rows)
    return PlainTextResponse(buf.getvalue(),
                             media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition":
                                      f'attachment; filename="{filename}"'})
