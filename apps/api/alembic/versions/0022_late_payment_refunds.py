"""Track independent refunds for payment attempts that settle too late."""
import sqlalchemy as sa
from alembic import op

revision = "0022_late_payment_refunds"
down_revision = "0021_account_deletion"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("late_payment_refunds"):
        op.create_table(
            "late_payment_refunds",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id"), nullable=False, unique=True),
            sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("amount", sa.Numeric(10, 2), nullable=False),
            sa.Column("currency", sa.String(10), nullable=False),
            sa.Column("request_key", sa.String(80), nullable=False, unique=True),
            sa.Column("gateway_ref", sa.String(80), nullable=False, server_default=""),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("last_error", sa.String(100), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    inspector = sa.inspect(bind)
    if not inspector.has_index("late_payment_refunds", "ix_late_payment_refunds_order_id"):
        op.create_index("ix_late_payment_refunds_order_id", "late_payment_refunds", ["order_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM late_payment_refunds")).scalar():
        raise RuntimeError("Existing payment compensation records require a coordinated restore")
    op.drop_index("ix_late_payment_refunds_order_id", table_name="late_payment_refunds")
    op.drop_table("late_payment_refunds")
