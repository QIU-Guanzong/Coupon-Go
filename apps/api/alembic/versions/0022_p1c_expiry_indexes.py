"""Add forward indexes for selecting due, unprocessed points grants.

This separate migration also upgrades databases that already ran the
original P1-C migration. Reconcile revision identifiers when integrating
with other migration branches; never rewrite an applied migration.
"""
import sqlalchemy as sa
from alembic import op

revision = "0022_p1c_expiry_indexes"
down_revision = "0021_p1c_points_expiry"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "point_expiry_finalizations" not in inspector.get_table_names():
        op.create_table("point_expiry_finalizations",
                        sa.Column("grant_id", sa.Integer(), sa.ForeignKey("point_transactions.id"), primary_key=True),
                        sa.Column("checked_at", sa.DateTime(), nullable=False))
    indexes = {i["name"] for i in inspector.get_indexes("point_transactions")}
    if "ix_point_tx_expiry_user" not in indexes:
        op.create_index("ix_point_tx_expiry_user", "point_transactions", ["expires_at", "user_id"])
    if "ix_point_tx_user_reason_ref" not in indexes:
        op.create_index("ix_point_tx_user_reason_ref", "point_transactions", ["user_id", "reason", "ref_id"])


def downgrade():
    op.drop_index("ix_point_tx_user_reason_ref", table_name="point_transactions")
    op.drop_index("ix_point_tx_expiry_user", table_name="point_transactions")
    op.drop_table("point_expiry_finalizations")
