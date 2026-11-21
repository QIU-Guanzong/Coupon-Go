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
            sa.Column("operation_id", sa.String(32), nullable=False),
            sa.Column("shop_id", sa.Integer, sa.ForeignKey("shops.id"), nullable=False),
            sa.Column("redemption_id", sa.Integer, sa.ForeignKey("redemptions.id"), nullable=True),
            sa.Column("order_id", sa.Integer, sa.ForeignKey("orders.id"), nullable=True),
            sa.Column("payment_intent_id", sa.String(80), nullable=False, server_default=""),
            sa.Column("stripe_charge_id", sa.String(80), nullable=False, server_default=""),
            sa.Column("kind", sa.String(20), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("gross_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("commission", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("net_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("stripe_transfer_id", sa.String(60), nullable=False, server_default=""),
            sa.Column("stripe_reversal_id", sa.String(60), nullable=False, server_default=""),
            sa.Column("stripe_payout_id", sa.String(60), nullable=False, server_default=""),
            sa.Column("client_request_id", sa.String(64), nullable=True),
            sa.Column("destination_account_id", sa.String(60), nullable=False, server_default=""),
            sa.Column("first_attempt_at", sa.DateTime, nullable=True),
            sa.Column("last_attempt_at", sa.DateTime, nullable=True),
            sa.Column("last_polled_at", sa.DateTime, nullable=True),
            sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("pending_reversal", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("note", sa.String(255), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime, nullable=True),
            sa.Column("updated_at", sa.DateTime, nullable=True),
            sa.UniqueConstraint("redemption_id", name="uq_connect_ledger_redemption"),
            sa.UniqueConstraint("order_id", name="uq_connect_ledger_order"),
            sa.UniqueConstraint("operation_id", name="uq_connect_ledger_operation_id"),
            sa.UniqueConstraint("shop_id", "kind", "client_request_id", name="uq_connect_ledger_client_request"),
        )
        op.create_index("ix_connect_ledger_entries_shop_id",
                        "connect_ledger_entries", ["shop_id"])
        op.create_index("ix_connect_ledger_entries_last_polled_at",
                        "connect_ledger_entries", ["last_polled_at"])
    else:
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("connect_ledger_entries")}
        required = {"operation_id", "order_id", "payment_intent_id", "stripe_charge_id", "client_request_id", "destination_account_id", "first_attempt_at", "last_attempt_at", "last_polled_at", "attempt_count"}
        if not required <= columns:
            raise RuntimeError("Existing Connect ledger lacks required safety columns; reconcile migration before upgrade")
    if not sa.inspect(op.get_bind()).has_table("connect_reconciliation_events"):
        op.create_table("connect_reconciliation_events",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("entry_id", sa.Integer, sa.ForeignKey("connect_ledger_entries.id"), nullable=False),
            sa.Column("actor_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
            sa.Column("source", sa.String(20), nullable=False),
            sa.Column("prior_status", sa.String(32), nullable=False),
            sa.Column("new_status", sa.String(32), nullable=False),
            sa.Column("provider_id", sa.String(80), nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False))
        op.create_index("ix_connect_reconciliation_events_entry_id", "connect_reconciliation_events", ["entry_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM connect_reconciliation_events")).scalar():
        raise RuntimeError("Reconciliation audit history requires a coordinated data restore")
    if op.get_bind().execute(sa.text("SELECT count(*) FROM connect_ledger_entries")).scalar():
        raise RuntimeError("A populated Connect ledger requires a coordinated data restore")
    if op.get_bind().execute(sa.text("SELECT count(*) FROM connect_accounts")).scalar():
        raise RuntimeError("Existing Connect accounts require a coordinated data restore")
    op.drop_index("ix_connect_reconciliation_events_entry_id", table_name="connect_reconciliation_events")
    op.drop_table("connect_reconciliation_events")
    op.drop_index("ix_connect_ledger_entries_last_polled_at", table_name="connect_ledger_entries")
    op.drop_index("ix_connect_ledger_entries_shop_id", table_name="connect_ledger_entries")
    op.drop_table("connect_ledger_entries")
    op.drop_table("connect_accounts")
