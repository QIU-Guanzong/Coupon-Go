"""P1-C: points grants expire (PointTransaction.expires_at).

Every positive points grant expires POINTS_EXPIRE_MONTHS after it is
earned; the `points-expire` scheduler job clears expired lots and
writes a reason=expire ledger row. Legacy positive rows are backfilled
to created_at + 12 months (the P1-C default); negative / reversal
rows never expire and keep expires_at NULL.

NOTE on numbering: this is 0021 because M9-2's migration (legal
consent) is 0020 in flight but not on main yet. down_revision points
at the current main head (0019). If 0020 lands on main first, re-point
down_revision to 0020 at rebase time — do NOT renumber this file.
"""
import sqlalchemy as sa
from alembic import op

revision = "0021_p1c_points_expiry"
down_revision = "0019_invoice_credits"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name
    if "expires_at" not in [c["name"] for c in
                            sa.inspect(bind).get_columns("point_transactions")]:
        op.add_column("point_transactions",
                      sa.Column("expires_at", sa.DateTime, nullable=True))
    if dialect == "postgresql":
        op.execute(
            "UPDATE point_transactions SET expires_at = "
            "created_at + INTERVAL '12 months' "
            "WHERE delta > 0 AND expires_at IS NULL")
    else:
        # SQLite: created_at is stored as 'YYYY-MM-DD HH:MM:SS'.
        op.execute(
            "UPDATE point_transactions SET expires_at = "
            "datetime(created_at, '+12 months') "
            "WHERE delta > 0 AND expires_at IS NULL")
    op.create_index("ix_point_transactions_user_expires",
                    "point_transactions", ["user_id", "expires_at"])


def downgrade():
    # Expiry rows (reason=expire) are ledger history: dropping the
    # column leaves them in place, only the expiry marker goes away.
    op.drop_index("ix_point_transactions_user_expires",
                  table_name="point_transactions")
    op.drop_column("point_transactions", "expires_at")
