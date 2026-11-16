"""FIX-2: cashback may only come from a redemption record.

cashback_transactions.redemption_id becomes REQUIRED and UNIQUE:
one redemption credits at most once, and no earning can exist without
a redemption behind it. The application-level enforcement lives in
cashback_service.record_spend() (the only writer,
called from the successful vendor scan); this migration is the
database-level backstop.

down_revision points at 0003_line_f (current chain tail).
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_cashback_redemption_unique"
down_revision = "0003_line_f"
branch_labels = None
depends_on = None

CONSTRAINT_NAME = "uq_cashback_txn_redemption_id"


def _has_unique_on_redemption(bind):
    insp = sa.inspect(bind)
    for uc in insp.get_unique_constraints("cashback_transactions"):
        if uc.get("column_names") == ["redemption_id"]:
            return True
    for idx in insp.get_indexes("cashback_transactions"):
        if idx.get("unique") and idx.get("column_names") == ["redemption_id"]:
            return True
    return False


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"]: c for c in insp.get_columns("cashback_transactions")}
    if "redemption_id" not in cols:
        raise RuntimeError(
            "cashback_transactions.redemption_id column is missing; "
            "cannot apply FIX-2 unique constraint")
    # Pre-existing duplicates would make the UNIQUE constraint fail:
    # refuse with a clear message instead of a cryptic DB error.
    dupes = bind.execute(sa.text(
        "SELECT COUNT(*) FROM (SELECT redemption_id "
        "FROM cashback_transactions WHERE redemption_id IS NOT NULL "
        "GROUP BY redemption_id HAVING COUNT(*) > 1)")).scalar()
    if dupes:
        raise RuntimeError(
            f"cashback_transactions has {dupes} redemption_id(s) credited "
            "more than once; clean up the duplicate earnings before "
            "applying FIX-2 (keep one earning per redemption)")
    nulls = bind.execute(sa.text(
        "SELECT COUNT(*) FROM cashback_transactions "
        "WHERE redemption_id IS NULL")).scalar()
    with op.batch_alter_table("cashback_transactions") as batch_op:
        if not _has_unique_on_redemption(bind):
            batch_op.create_unique_constraint(
                CONSTRAINT_NAME, ["redemption_id"])
        if nulls == 0 and cols["redemption_id"].get("nullable", True):
            batch_op.alter_column("redemption_id", nullable=False)
        elif nulls > 0:
            # Legacy rows credited before FIX-2 without a redemption
            # behind them are preserved, not deleted: the column stays
            # nullable at DB level for them, but no new row can ever be
            # created without a redemption_id (the only writer requires
            # one, and the model declares nullable=False). Re-run the
            # NOT NULL step once those rows are resolved.
            print(f"FIX-2 migration: {nulls} legacy cashback row(s) have "
                  "NULL redemption_id; leaving the column nullable for "
                  "them. Resolve those rows, then alter the column to "
                  "NOT NULL.")


def downgrade():
    bind = op.get_bind()
    with op.batch_alter_table("cashback_transactions") as batch_op:
        if _has_unique_on_redemption(bind):
            batch_op.drop_constraint(CONSTRAINT_NAME, type_="unique")
        batch_op.alter_column("redemption_id", nullable=True)
