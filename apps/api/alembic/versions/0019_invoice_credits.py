"""Retain issued invoices and separately record confirmed credit notes."""
import sqlalchemy as sa
from alembic import op

revision = "0019_invoice_credits"
down_revision = "0018_settlement_ledger"
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("invoice_credits"):
        op.create_table(
            "invoice_credits",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("invoice_id", sa.Integer, sa.ForeignKey("invoices.id"), nullable=False),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("reference", sa.String(120), nullable=False),
            sa.Column("actor_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.UniqueConstraint("invoice_id", "reference", name="uq_invoice_credit_reference"),
        )
        op.create_index("ix_invoice_credits_invoice_id", "invoice_credits", ["invoice_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM invoice_credits")).scalar():
        raise RuntimeError("A populated invoice credit ledger requires a coordinated data restore")
    op.drop_index("ix_invoice_credits_invoice_id", table_name="invoice_credits")
    op.drop_table("invoice_credits")
