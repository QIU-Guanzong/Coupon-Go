"""Converge the historical 0020/0021 heads and add missing P1-B columns.

The original 0021 revision was published on the 0019 branch, independently
of 0020.  Do not change either revision's ancestry: an existing database can
have executed either one or both.  Alembic applies whichever parent is absent
before entering this merge revision.

This bridge deliberately leaves historical amounts, commission rates and
dispatch states NULL.  In particular, a missing dispatch marker is not proof
that a historical Stripe request was never made.
"""

import sqlalchemy as sa
from alembic import op

revision = "0022_p1b_forward_bridge"
down_revision = ("0020_m92_legal_consents", "0021_p1b_stripe_connect")
branch_labels = None
depends_on = None


def _column_specifications():
    # New Column objects are required each time Alembic attaches a column.
    return {
        "redemptions": (
            sa.Column("commission_rate_snapshot", sa.Numeric(5, 4), nullable=True),
        ),
        "connect_ledger_entries": (
            sa.Column("dispatch_started", sa.Boolean(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("manual_notified", sa.Boolean(), nullable=False, server_default="0"),
        ),
        "wallet_items": (
            sa.Column("settlement_allocated_amount", sa.Numeric(12, 2), nullable=True),
            sa.Column("refund_hold_previous_status", sa.Integer(), nullable=True),
        ),
        "orders": (
            sa.Column("wallet_allocation_fingerprint", sa.String(64), nullable=True),
        ),
    }


def _validate_existing(table, actual, expected):
    """Fail before DDL if a pre-existing same-named field is incompatible."""
    found_type = actual["type"]
    expected_type = expected.type
    compatible = isinstance(found_type, expected_type.__class__)
    if isinstance(expected_type, sa.Numeric):
        compatible = (isinstance(found_type, sa.Numeric)
                      and found_type.precision == expected_type.precision
                      and found_type.scale == expected_type.scale)
    elif isinstance(expected_type, sa.String):
        compatible = (isinstance(found_type, sa.String)
                      and found_type.length == expected_type.length)
    if not compatible or bool(actual["nullable"]) != expected.nullable:
        raise RuntimeError(f"Incompatible existing column {table}.{expected.name}; restore or reconcile schema before upgrade")


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    specs = _column_specifications()
    known_tables = set(inspector.get_table_names())
    # Validate every table/column first so even databases without transactional
    # DDL do not receive a partial upgrade for a detectable incompatibility.
    existing = {}
    for table, columns in specs.items():
        if table not in known_tables:
            raise RuntimeError(f"Missing required table {table}; check migration history before upgrade")
        existing[table] = {col["name"]: col for col in inspector.get_columns(table)}
        for column in columns:
            if column.name in existing[table]:
                _validate_existing(table, existing[table][column.name], column)
    for table, columns in specs.items():
        for column in columns:
            if column.name not in existing[table]:
                op.add_column(table, column)


def downgrade():
    # Some installations already had one or more of these columns when this
    # revision was applied.  There is no reliable provenance proving which
    # column was added here; removing it could destroy pre-existing schema or
    # payment/settlement data even when every row currently happens to be NULL.
    raise RuntimeError("Forward-only bridge: coordinated database restore required for downgrade")
