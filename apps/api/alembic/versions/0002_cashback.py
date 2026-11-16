"""Line D — cashback tables (返现 2B).

Creates cashback_transactions, cashback_withdrawals, cashback_appeals
(report §11.1). down_revision points at 0002_line_b (merged chain:
0001 -> 0002_line_a -> 0002_line_b -> 0002_cashback).

Guard: 0001's Base.metadata.create_all() already creates these tables on
a FRESH database (models_cashback is registered on Base.metadata), so each
create_table is skipped when the table already exists. This keeps
`alembic upgrade head` working both on a fresh DB and on an existing v0.1
DB that only has the 0001 tables.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0002_cashback"
down_revision = "0002_line_b"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return inspect(bind).has_table(name)


def upgrade():
    bind = op.get_bind()
    # Withdrawals first: cashback_transactions.withdrawal_id references it.
    if not _has_table(bind, "cashback_withdrawals"):
        op.create_table(
            "cashback_withdrawals",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("amount", sa.Numeric(10, 2), nullable=False),
            sa.Column("method", sa.String(20), nullable=False),
            sa.Column("account_name", sa.String(120), nullable=False),
            sa.Column("account_ref", sa.String(120), nullable=False, server_default=""),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("processed_at", sa.DateTime(), nullable=True),
        )
    if not _has_table(bind, "cashback_transactions"):
        op.create_table(
            "cashback_transactions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("merchant_id", sa.Integer(), sa.ForeignKey("shops.id"), nullable=False, index=True),
            sa.Column("redemption_id", sa.Integer(), sa.ForeignKey("redemptions.id"), nullable=True),
            sa.Column("channel", sa.String(20), nullable=False, server_default="dinein"),
            sa.Column("amount", sa.Numeric(10, 2), nullable=False),
            sa.Column("rate", sa.Numeric(5, 4), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("funded_by", sa.String(30), nullable=False, server_default="merchant_commission"),
            sa.Column("expected_confirm_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("confirmed_at", sa.DateTime(), nullable=True),
            sa.Column("withdrawn_amount", sa.Numeric(10, 2), nullable=False, server_default="0"),
            sa.Column("withdrawal_id", sa.Integer(), sa.ForeignKey("cashback_withdrawals.id"), nullable=True),
            sa.Column("void_reason", sa.String(30), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
    if not _has_table(bind, "cashback_appeals"):
        op.create_table(
            "cashback_appeals",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("merchant_id", sa.Integer(), sa.ForeignKey("shops.id"), nullable=False),
            sa.Column("order_no", sa.String(80), nullable=False),
            sa.Column("spent_at", sa.DateTime(), nullable=False),
            sa.Column("amount", sa.Numeric(10, 2), nullable=False),
            sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(20), nullable=False, server_default="submitted"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
        )


def downgrade():
    bind = op.get_bind()
    for name in ("cashback_appeals", "cashback_transactions", "cashback_withdrawals"):
        if _has_table(bind, name):
            op.drop_table(name)
