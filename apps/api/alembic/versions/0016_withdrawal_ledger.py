"""Retain withdrawal allocations and review history."""
from alembic import op
import sqlalchemy as sa

revision = "0016_withdrawal_ledger"
down_revision = "0015_m7_review_rating_backfill"
branch_labels = None
depends_on = None


def upgrade():
    # Dev previews may have already bootstrapped these tables with create_all.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "cashback_withdrawal_allocations" not in existing:
        op.create_table("cashback_withdrawal_allocations",
            sa.Column("withdrawal_id", sa.Integer, sa.ForeignKey("cashback_withdrawals.id"), primary_key=True),
            sa.Column("transaction_id", sa.Integer, sa.ForeignKey("cashback_transactions.id"), primary_key=True),
            sa.Column("amount", sa.Numeric(10, 2), nullable=False))
    if "cashback_withdrawal_events" not in existing:
        op.create_table("cashback_withdrawal_events",
            sa.Column("withdrawal_id", sa.Integer, sa.ForeignKey("cashback_withdrawals.id"), primary_key=True),
            sa.Column("status", sa.String(20), primary_key=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("actor_id", sa.Integer, sa.ForeignKey("users.id")),
            sa.Column("request_key", sa.String(80)),
            sa.Column("detail", sa.String(200), nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.UniqueConstraint("user_id", "request_key"))


def downgrade():
    op.drop_table("cashback_withdrawal_events")
    op.drop_table("cashback_withdrawal_allocations")
