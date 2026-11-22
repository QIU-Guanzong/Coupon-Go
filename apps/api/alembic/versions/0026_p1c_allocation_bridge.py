"""Converge paid-coupon and previously deployed points migration histories.

Add allocation fields only when absent; existing values are never rewritten.
"""
import sqlalchemy as sa
from alembic import op

revision = "0026_p1c_allocation_bridge"
down_revision = ("0025_p1a_forward_bridge", "0022_p1c_expiry_indexes")
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {
        "wallet_items": (sa.Column("settlement_allocated_amount", sa.Numeric(12, 2), nullable=True),
                         sa.Column("refund_hold_previous_status", sa.Integer(), nullable=True)),
        "orders": (sa.Column("wallet_allocation_fingerprint", sa.String(64), nullable=True),),
    }
    existing = {}
    for table, expected in columns.items():
        existing[table] = {c["name"]: c for c in inspector.get_columns(table)}
        for column in expected:
            found = existing[table].get(column.name)
            if found is None:
                continue
            actual = found["type"]
            compatible = isinstance(actual, type(column.type))
            if isinstance(column.type, sa.Numeric):
                compatible = isinstance(actual, sa.Numeric) and actual.precision == 12 and actual.scale == 2
            elif isinstance(column.type, sa.String):
                compatible = isinstance(actual, sa.String) and actual.length == 64
            if not compatible or not found["nullable"]:
                raise RuntimeError(f"Incompatible existing column {table}.{column.name}; reconcile before upgrade")
    for table, expected in columns.items():
        for column in expected:
            if column.name not in existing[table]:
                op.add_column(table, column)


def downgrade():
    raise RuntimeError("Paid coupon and points history requires a coordinated restore before downgrade")
