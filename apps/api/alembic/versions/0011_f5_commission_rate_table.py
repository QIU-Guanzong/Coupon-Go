"""F5: merchant_commission_rates — source table for merchant
commission rates (one row per shop).

generate_settlement reads the shop's configured rate from this table
when one exists, else the platform default 10%; the generate request
body can no longer set an arbitrary rate.

down_revision points at 0009_gap9_review_helpful (chain tail at the
time of writing; the coordinator re-orders the chain on merge).
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_f5_commission_rate_table"
down_revision = "0010_f4f6f7_settlement_snapshot"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "merchant_commission_rates" not in insp.get_table_names():
        op.create_table(
            "merchant_commission_rates",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("shop_id", sa.Integer,
                      sa.ForeignKey("shops.id"), nullable=False,
                      unique=True),
            sa.Column("rate", sa.Numeric(5, 4), nullable=False),
            sa.Column("set_by", sa.Integer,
                      sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=True),
            sa.Column("updated_at", sa.DateTime, nullable=True),
            sa.UniqueConstraint("shop_id",
                                name="uq_merchant_commission_rate_shop"),
        )


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "merchant_commission_rates" in insp.get_table_names():
        op.drop_table("merchant_commission_rates")
