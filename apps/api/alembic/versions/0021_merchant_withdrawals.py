"""Merchant withdrawal requests (Dianping §2.2, P0-4)."""
import sqlalchemy as sa
from alembic import op

revision = "0021_merchant_withdrawals"
down_revision = "0020_m92_legal_consents"
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("merchant_withdrawals"):
        op.create_table(
            "merchant_withdrawals",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("shop_id", sa.Integer, sa.ForeignKey("shops.id"),
                      nullable=False),
            sa.Column("settlement_id", sa.Integer,
                      sa.ForeignKey("settlements.id"), nullable=False),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"),
                      nullable=False),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("status", sa.String(20), nullable=False,
                      server_default="pending"),
            sa.Column("payout_reference", sa.String(120), nullable=False,
                      server_default=""),
            sa.Column("payout_provider", sa.String(40), nullable=False,
                      server_default=""),
            sa.Column("reject_reason", sa.Text, nullable=False,
                      server_default=""),
            sa.Column("decided_by", sa.Integer, sa.ForeignKey("users.id"),
                      nullable=True),
            sa.Column("decided_at", sa.DateTime, nullable=True),
            sa.Column("paid_at", sa.DateTime, nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("updated_at", sa.DateTime, nullable=False),
        )
        op.create_index("ix_merchant_withdrawals_shop",
                        "merchant_withdrawals", ["shop_id"])
        op.create_index("ix_merchant_withdrawals_settlement",
                        "merchant_withdrawals", ["settlement_id"])
        op.create_index("ix_merchant_withdrawals_status",
                        "merchant_withdrawals", ["status"])


def downgrade():
    if op.get_bind().execute(
            sa.text("SELECT count(*) FROM merchant_withdrawals")).scalar():
        raise RuntimeError(
            "A populated merchant withdrawal ledger requires coordinated retention")
    op.drop_index("ix_merchant_withdrawals_status",
                  table_name="merchant_withdrawals")
    op.drop_index("ix_merchant_withdrawals_settlement",
                  table_name="merchant_withdrawals")
    op.drop_index("ix_merchant_withdrawals_shop",
                  table_name="merchant_withdrawals")
    op.drop_table("merchant_withdrawals")
