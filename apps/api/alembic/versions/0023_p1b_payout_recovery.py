"""Add recoverable payout request identities without rewriting old rows."""
import sqlalchemy as sa
from alembic import op

revision = "0023_p1b_payout_recovery"
down_revision = "0022_p1b_forward_bridge"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("connect_ledger_entries"):
        raise RuntimeError("Missing Connect ledger; reconcile migration history")
    existing = {c["name"]: c for c in inspector.get_columns("connect_ledger_entries")}
    specs = [sa.Column("payout_request_id", sa.String(64), nullable=True),
             sa.Column("payout_operation_id", sa.String(32), nullable=True),
             sa.Column("payout_account_id", sa.String(60), nullable=True),
             sa.Column("payout_started_at", sa.DateTime(), nullable=True),
             sa.Column("payout_last_polled_at", sa.DateTime(), nullable=True)]
    for column in specs:
        actual = existing.get(column.name)
        if actual is not None:
            kind = actual["type"]
            if (not isinstance(kind, column.type.__class__) or not actual["nullable"] or
                    (isinstance(column.type, sa.String) and kind.length != column.type.length)):
                raise RuntimeError(f"Incompatible Connect ledger column {column.name}")
    for column in specs:
        if column.name not in existing:
            op.add_column("connect_ledger_entries", column)
    inspector = sa.inspect(bind)
    names = {i["name"] for i in inspector.get_indexes("connect_ledger_entries")}
    names.update(i["name"] for i in inspector.get_unique_constraints("connect_ledger_entries"))
    if "uq_connect_payout_request" not in names:
        op.create_index("uq_connect_payout_request", "connect_ledger_entries",
                        ["shop_id", "kind", "payout_request_id"], unique=True)
    if "uq_connect_payout_operation" not in names:
        op.create_index("uq_connect_payout_operation", "connect_ledger_entries",
                        ["payout_operation_id"], unique=True)


def downgrade():
    raise RuntimeError("Payout reconciliation records require a coordinated restore")
