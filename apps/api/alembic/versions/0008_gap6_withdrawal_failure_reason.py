"""Gap 6: cashback_withdrawals.failure_reason — why a payout failed.

Additive nullable column; no existing column or constraint changes,
so upgrade is safe on a live DB. The client shows this reason on a
failed withdrawal row instead of a generic message (see
cashback_service._withdrawal_out / mark_withdrawal_failed).

down_revision points at 0007_gap7_order_quantity (chain tail).
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_gap6_withdrawal_failure_reason"
down_revision = "0007_gap7_order_quantity"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("cashback_withdrawals")}
    if "failure_reason" not in cols:
        with op.batch_alter_table("cashback_withdrawals") as batch_op:
            batch_op.add_column(sa.Column(
                "failure_reason", sa.String(200), nullable=True))


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("cashback_withdrawals")}
    if "failure_reason" in cols:
        with op.batch_alter_table("cashback_withdrawals") as batch_op:
            batch_op.drop_column("failure_reason")
