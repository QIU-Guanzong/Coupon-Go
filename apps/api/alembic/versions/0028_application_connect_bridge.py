"""Converge onboarding and Connect histories; bind applications to shops."""
import sqlalchemy as sa
from alembic import op

revision = "0028_application_connect_bridge"
down_revision = ("0027_p1d_forward_bridge", "0023_p1b_payout_recovery")
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"]: c for c in inspector.get_columns("merchant_applications")}
    if "approved_shop_id" in columns:
        c = columns["approved_shop_id"]
        if not isinstance(c["type"], sa.Integer) or not c["nullable"]:
            raise RuntimeError("Incompatible merchant_applications.approved_shop_id")
    else:
        # SQLite cannot add a foreign-key constraint to an existing table;
        # ownership and existence are checked in the approval transaction.
        op.add_column("merchant_applications", sa.Column("approved_shop_id", sa.Integer(), nullable=True))
    if not inspector.has_table("application_connect_accounts"):
        op.create_table(
            "application_connect_accounts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("application_id", sa.Integer(), sa.ForeignKey("merchant_applications.id"), nullable=False, unique=True),
            sa.Column("operation_key", sa.String(80), nullable=False, unique=True),
            sa.Column("contact_email", sa.String(255), nullable=False),
            sa.Column("state", sa.String(20), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("stripe_account_id", sa.String(60), nullable=True, unique=True),
            sa.Column("simulated", sa.Boolean(), nullable=False),
            sa.Column("verified_at", sa.DateTime(), nullable=True),
            sa.Column("charges_enabled", sa.Boolean(), nullable=False),
            sa.Column("payouts_enabled", sa.Boolean(), nullable=False),
            sa.Column("transfers_enabled", sa.Boolean(), nullable=False),
            sa.Column("details_submitted", sa.Boolean(), nullable=False),
            sa.Column("manual_payouts", sa.Boolean(), nullable=False),
        )
    account_columns = {c["name"]: c for c in sa.inspect(bind).get_columns("connect_accounts")}
    if "transfers_enabled" in account_columns:
        c = account_columns["transfers_enabled"]
        if not isinstance(c["type"], sa.Boolean) or not c["nullable"]:
            raise RuntimeError("Incompatible connect_accounts.transfers_enabled")
    else:
        op.add_column("connect_accounts", sa.Column("transfers_enabled", sa.Boolean(), nullable=True))
    if not inspector.has_table("shop_connect_operations"):
        op.create_table(
            "shop_connect_operations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id"), nullable=False, unique=True),
            sa.Column("operation_key", sa.String(80), nullable=False, unique=True),
            sa.Column("contact_email", sa.String(255), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=False),
        )


def downgrade():
    raise RuntimeError("Forward-only bridge: coordinated restore required")
