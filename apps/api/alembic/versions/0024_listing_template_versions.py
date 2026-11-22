"""Mark the current package template while preserving legacy voucher rows.

Revision ID: 0024_listing_template_versions
Revises: 0023_dianping_listing
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_listing_template_versions"
down_revision = "0023_dianping_listing"
branch_labels = None
depends_on = None


def upgrade():
    names = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("coupons")}
    if "is_package_template" not in names:
        with op.batch_alter_table("coupons") as batch:
            batch.add_column(sa.Column("is_package_template", sa.Boolean(), nullable=True))
    names = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("orders")}
    if "package_coupon_id" not in names:
        with op.batch_alter_table("orders") as batch:
            batch.add_column(sa.Column("package_coupon_id", sa.Integer(),
                sa.ForeignKey("coupons.id", name="fk_orders_package_coupon_id"), nullable=True))


def downgrade():
    # Downgrading loses the identity of issued versions, so never silently
    # collapse them back into one shared template.
    raise RuntimeError("Cannot safely downgrade package voucher template versions")
