"""FIX-3: database backstops for the row locks.

* refunds: UNIQUE (order_id, reason) — one refund per order per reason,
  so a second concurrent refund cannot insert a second row even where
  SELECT ... FOR UPDATE is ignored (SQLite).
* packages / bargain_campaigns: CHECK (stock_left >= 0) — stock can
  never go below zero even if a decrement races past the check.

The application-level enforcement lives in the row locks
(cashback_service.withdraw, routers/packages, routers/bargain,
routers/redemption, routers/admin_v2); these constraints are the
second line of defence, and they are what the SQLite test runs
actually assert on (see tests/test_fix3_row_locks.py).

down_revision points at 0004_cashback_redemption_unique (chain tail).
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_fix3_row_lock_backstops"
down_revision = "0004_cashback_redemption_unique"
branch_labels = None
depends_on = None

REFUND_UQ = "uq_refunds_order_reason"
PKG_CK = "ck_packages_stock_left_nonneg"
BARGAIN_CK = "ck_bargain_campaigns_stock_left_nonneg"


def _has_unique(bind, table, columns):
    insp = sa.inspect(bind)
    for uc in insp.get_unique_constraints(table):
        if uc.get("column_names") == list(columns):
            return True
    for idx in insp.get_indexes(table):
        if idx.get("unique") and idx.get("column_names") == list(columns):
            return True
    return False


def _has_check(bind, table, name):
    insp = sa.inspect(bind)
    return any(c.get("name") == name for c in insp.get_check_constraints(table))


def upgrade():
    bind = op.get_bind()
    # Pre-existing violations would make the constraint fail: refuse
    # with a clear message instead of a cryptic DB error.
    dupes = bind.execute(sa.text(
        "SELECT COUNT(*) FROM (SELECT order_id, reason FROM refunds "
        "GROUP BY order_id, reason HAVING COUNT(*) > 1)")).scalar()
    if dupes:
        raise RuntimeError(
            f"refunds has {dupes} (order_id, reason) pair(s) refunded "
            "more than once; clean up the duplicate refunds before "
            "applying FIX-3 (keep one refund per order per reason)")
    for table in ("packages", "bargain_campaigns"):
        bad = bind.execute(sa.text(
            f"SELECT COUNT(*) FROM {table} WHERE stock_left < 0")).scalar()
        if bad:
            raise RuntimeError(
                f"{table} has {bad} row(s) with stock_left < 0; fix the "
                "negative stock before applying FIX-3")
    with op.batch_alter_table("refunds") as batch_op:
        if not _has_unique(bind, "refunds", ("order_id", "reason")):
            batch_op.create_unique_constraint(REFUND_UQ,
                                              ["order_id", "reason"])
    with op.batch_alter_table("packages") as batch_op:
        if not _has_check(bind, "packages", PKG_CK):
            batch_op.create_check_constraint(PKG_CK, "stock_left >= 0")
    with op.batch_alter_table("bargain_campaigns") as batch_op:
        if not _has_check(bind, "bargain_campaigns", BARGAIN_CK):
            batch_op.create_check_constraint(BARGAIN_CK, "stock_left >= 0")


def downgrade():
    bind = op.get_bind()
    with op.batch_alter_table("refunds") as batch_op:
        if _has_unique(bind, "refunds", ("order_id", "reason")):
            batch_op.drop_constraint(REFUND_UQ, type_="unique")
    with op.batch_alter_table("packages") as batch_op:
        if _has_check(bind, "packages", PKG_CK):
            batch_op.drop_constraint(PKG_CK, type_="check")
    with op.batch_alter_table("bargain_campaigns") as batch_op:
        if _has_check(bind, "bargain_campaigns", BARGAIN_CK):
            batch_op.drop_constraint(BARGAIN_CK, type_="check")
