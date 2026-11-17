"""Gap 7: orders.quantity — purchase quantity (default 1).

down_revision points at 0006_p1_cashback_revoke_bargain_refund
(chain tail). Idempotent: skips when the column already exists
(fresh databases built from Base.metadata.create_all already have it).
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_gap7_order_quantity"
down_revision = "0006_p1_cashback_revoke_bargain_refund"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("orders")}
    if "quantity" in cols:
        return
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(
            sa.Column("quantity", sa.Integer, nullable=False,
                      server_default="1"))


def downgrade():
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("orders")}
    if "quantity" not in cols:
        return
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("quantity")
