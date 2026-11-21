"""P1-B: Stripe Connect (Accounts v2) accounts + merchant balance ledger.

NOTE on numbering: this revision is 0021. M9-2 (0020, down_revision
0019) has landed first, so this file still points at 0019 and creates
a second head; renumber down_revision to 0020 at merge time — the
chain must stay linear, and the tables here do not depend on M9-2.

No bank-account data is stored: Stripe collects merchant identity and
bank accounts during onboarding; we keep only the Stripe account id
and the readiness flags Stripe reports.
"""
import sqlalchemy as sa
from alembic import op

revision = "0021_p1b_stripe_connect"
down_revision = "0019_invoice_credits"
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("connect_accounts"):
        op.create_table(
            "connect_accounts",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("shop_id", sa.Integer, sa.ForeignKey("shops.id"), nullable=False),
            sa.Column("stripe_account_id", sa.String(60), nullable=False),
            sa.Column("charges_enabled", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("payouts_enabled", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("details_submitted", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("onboarding_status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("manual_payouts", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime, nullable=True),
            sa.Column("updated_at", sa.DateTime, nullable=True),
            sa.UniqueConstraint("shop_id", name="uq_connect_account_shop"),
        )
    if not sa.inspect(op.get_bind()).has_table("connect_ledger_entries"):
        op.create_table(
            "connect_ledger_entries",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("shop_id", sa.Integer, sa.ForeignKey("shops.id"), nullable=False),
            sa.Column("redemption_id", sa.Integer, sa.ForeignKey("redemptions.id"), nullable=True),
            sa.Column("kind", sa.String(20), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("gross_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("commission", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("net_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("stripe_transfer_id", sa.String(60), nullable=False, server_default=""),
            sa.Column("stripe_reversal_id", sa.String(60), nullable=False, server_default=""),
            sa.Column("stripe_payout_id", sa.String(60), nullable=False, server_default=""),
            sa.Column("dispatch_started", sa.Boolean, nullable=True),
            sa.Column("pending_reversal", sa.Boolean, nullable=False, server_default="0"),
            # Queue §F items 2–3: failed transfer / reversal attempts
            # and the once-only admin notification for needs_manual.
            sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("manual_notified", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("note", sa.String(255), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime, nullable=True),
            sa.Column("updated_at", sa.DateTime, nullable=True),
            sa.UniqueConstraint("redemption_id", name="uq_connect_ledger_redemption"),
        )
        op.create_index("ix_connect_ledger_entries_shop_id",
                        "connect_ledger_entries", ["shop_id"])
    # Existing unversioned/test tables must preserve NULL as unknown history.
    columns = {col["name"] for col in sa.inspect(op.get_bind()).get_columns("connect_ledger_entries")}
    if "dispatch_started" not in columns:
        op.add_column("connect_ledger_entries", sa.Column("dispatch_started", sa.Boolean, nullable=True))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM connect_ledger_entries")).scalar():
        raise RuntimeError("A populated Connect ledger requires a coordinated data restore")
    if op.get_bind().execute(sa.text("SELECT count(*) FROM connect_accounts")).scalar():
        raise RuntimeError("Existing Connect accounts require a coordinated data restore")
    op.drop_index("ix_connect_ledger_entries_shop_id", table_name="connect_ledger_entries")
    op.drop_table("connect_ledger_entries")
    op.drop_table("connect_accounts")
