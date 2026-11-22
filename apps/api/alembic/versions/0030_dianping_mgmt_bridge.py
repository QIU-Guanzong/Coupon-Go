"""Converge merchant-management and current application/Connect histories."""
import sqlalchemy as sa
from alembic import op

revision = "0030_dianping_mgmt_bridge"
down_revision = ("0029_application_connect_bridge", "0023_dianping_merchant_mgmt")
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    redemption_columns = {c["name"]: c for c in inspector.get_columns("redemptions")}
    if "branch_id" not in redemption_columns:
        op.add_column("redemptions", sa.Column("branch_id", sa.Integer(), nullable=True))
    elif not isinstance(redemption_columns["branch_id"]["type"], sa.Integer):
        raise RuntimeError("Incompatible redemption branch_id column")
    columns = {c["name"]: c for c in inspector.get_columns("shops")}
    for name, default in (("business_hours", ""), ("gallery", "[]"), ("announcement", "")):
        if name not in columns:
            op.add_column("shops", sa.Column(name, sa.Text(), nullable=False, server_default=default))
        elif not isinstance(columns[name]["type"], sa.Text):
            raise RuntimeError(f"Incompatible shop column: {name}")
    if not inspector.has_table("coupon_branches"):
        op.create_table("coupon_branches",
                        sa.Column("id", sa.Integer(), primary_key=True),
                        sa.Column("coupon_id", sa.Integer(), sa.ForeignKey("coupons.id"), nullable=False),
                        sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branches.id"), nullable=False),
                        sa.Column("created_at", sa.DateTime(), nullable=False),
                        sa.UniqueConstraint("coupon_id", "branch_id", name="uq_coupon_branch"))
    else:
        found = {c["name"]: c for c in inspector.get_columns("coupon_branches")}
        for name in ("coupon_id", "branch_id"):
            if name not in found or not isinstance(found[name]["type"], sa.Integer) or found[name]["nullable"]:
                raise RuntimeError(f"Incompatible coupon_branches column: {name}")
        unique = {tuple(item["column_names"]) for item in inspector.get_unique_constraints("coupon_branches")}
        unique |= {tuple(item["column_names"]) for item in inspector.get_indexes("coupon_branches") if item.get("unique")}
        if ("coupon_id", "branch_id") not in unique:
            raise RuntimeError("coupon_branches missing uniqueness constraint")


def downgrade():
    raise RuntimeError("Forward-only merchant bridge: coordinated restore required")
