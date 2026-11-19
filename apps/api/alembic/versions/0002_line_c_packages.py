"""Line C (套餐 2A): packages / orders / payments / refunds tables +
coupons.kind/package_id/person_count/usage_rules/refund_policy and
wallet_items.source/order_id columns (report §11).

Revision id is unique; down_revision points at 0002_bargain_e (merged chain:
0001 -> 0002_line_a -> 0002_line_b -> 0002_cashback ->
0002_bargain_e -> 0002_line_c).

Idempotent by design: 0001 builds tables via Base.metadata.create_all,
so on a fresh database these tables may already exist when 0002 runs
(and on an existing database they do not). Every step checks first.
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_line_c"
down_revision = "0002_bargain_e"
branch_labels = None
depends_on = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _alter(table):
    # SQLite needs table recreation for FK changes. PostgreSQL can alter
    # in place and must retain primary keys referenced by other tables.
    mode = "always" if op.get_bind().dialect.name == "sqlite" else "auto"
    return op.batch_alter_table(table, recreate=mode)


def upgrade():
    tables = _tables()
    if "packages" not in tables:
        op.create_table(
            "packages",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("shop_id", sa.Integer, sa.ForeignKey("shops.id"), nullable=False),
            sa.Column("name", sa.String(160), nullable=False),
            sa.Column("name_en", sa.String(160), server_default=""),
            sa.Column("items", sa.Text, server_default="[]"),
            sa.Column("person_count", sa.Integer, server_default="2", nullable=False),
            sa.Column("original_price", sa.Numeric(10, 2), nullable=False),
            sa.Column("package_price", sa.Numeric(10, 2), nullable=False),
            sa.Column("valid_from", sa.DateTime, nullable=False),
            sa.Column("valid_to", sa.DateTime, nullable=False),
            sa.Column("usage_rules", sa.Text, server_default="{}"),
            sa.Column("stock_total", sa.Integer, server_default="0", nullable=False),
            sa.Column("stock_left", sa.Integer, server_default="0", nullable=False),
            sa.Column("refund_policy", sa.String(20), server_default="anytime", nullable=False),
            sa.Column("status", sa.String(20), server_default="active", nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=True),
        )
    if "orders" not in tables:
        op.create_table(
            "orders",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
            sa.Column("order_type", sa.String(30), server_default="package", nullable=False),
            sa.Column("package_id", sa.Integer, sa.ForeignKey("packages.id"), nullable=True),
            sa.Column("amount", sa.Numeric(10, 2), nullable=False),
            sa.Column("paid_amount", sa.Numeric(10, 2), server_default="0"),
            sa.Column("payment_method", sa.String(20), server_default=""),
            sa.Column("payment_status", sa.String(20), server_default="pending", nullable=False),
            sa.Column("refund_status", sa.String(20), server_default="none", nullable=False),
            sa.Column("settlement_status", sa.String(20), server_default="pending", nullable=False),
            sa.Column("commission_amount", sa.Numeric(10, 2), server_default="0"),
            sa.Column("net_amount", sa.Numeric(10, 2), server_default="0"),
            sa.Column("client_ref", sa.String(64), server_default=""),
            sa.Column("created_at", sa.DateTime, nullable=True),
            sa.Column("paid_at", sa.DateTime, nullable=True),
        )
    if "payments" not in tables:
        op.create_table(
            "payments",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("order_id", sa.Integer, sa.ForeignKey("orders.id"), nullable=False),
            sa.Column("gateway", sa.String(20), server_default="stripe", nullable=False),
            sa.Column("gateway_ref", sa.String(80), server_default=""),
            sa.Column("client_secret", sa.String(255), server_default=""),
            sa.Column("method", sa.String(20), server_default=""),
            sa.Column("amount", sa.Numeric(10, 2), nullable=False),
            sa.Column("currency", sa.String(10), server_default="HKD", nullable=False),
            sa.Column("status", sa.String(20), server_default="requires_payment", nullable=False),
            sa.Column("failure_message", sa.String(255), server_default=""),
            sa.Column("created_at", sa.DateTime, nullable=True),
        )
    if "refunds" not in tables:
        op.create_table(
            "refunds",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("order_id", sa.Integer, sa.ForeignKey("orders.id"), nullable=False),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
            sa.Column("amount", sa.Numeric(10, 2), nullable=False),
            sa.Column("reason", sa.String(255), server_default=""),
            sa.Column("status", sa.String(20), server_default="requested", nullable=False),
            sa.Column("gateway_ref", sa.String(80), server_default=""),
            sa.Column("created_at", sa.DateTime, nullable=True),
        )
    coupon_cols = _columns("coupons")
    # Batch mode: SQLite cannot ALTER-ADD a column carrying a FK.
    with _alter("coupons") as batch:
        if "kind" not in coupon_cols:
            batch.add_column(sa.Column("kind", sa.String(20), server_default="coupon", nullable=False))
        if "package_id" not in coupon_cols:
            batch.add_column(sa.Column("package_id", sa.Integer, sa.ForeignKey("packages.id", name="fk_coupons_package_id"), nullable=True))
        if "person_count" not in coupon_cols:
            batch.add_column(sa.Column("person_count", sa.Integer, nullable=True))
        if "usage_rules" not in coupon_cols:
            batch.add_column(sa.Column("usage_rules", sa.Text, server_default=""))
        if "refund_policy" not in coupon_cols:
            batch.add_column(sa.Column("refund_policy", sa.String(20), server_default="anytime", nullable=False))
    wallet_cols = _columns("wallet_items")
    with _alter("wallet_items") as batch:
        if "source" not in wallet_cols:
            batch.add_column(sa.Column("source", sa.String(20), server_default="claim", nullable=False))
        if "order_id" not in wallet_cols:
            batch.add_column(sa.Column("order_id", sa.Integer, sa.ForeignKey("orders.id", name="fk_wallet_items_order_id"), nullable=True))


def downgrade():
    tables = _tables()
    wallet_cols = _columns("wallet_items") if "wallet_items" in tables else set()
    # Batch mode: plain DROP COLUMN fails on SQLite for FK columns.
    with _alter("wallet_items") as batch:
        if "order_id" in wallet_cols:
            batch.drop_column("order_id")
        if "source" in wallet_cols:
            batch.drop_column("source")
    coupon_cols = _columns("coupons") if "coupons" in tables else set()
    with _alter("coupons") as batch:
        for name in ("refund_policy", "usage_rules", "person_count",
                     "package_id", "kind"):
            if name in coupon_cols:
                batch.drop_column(name)
    for table in ("refunds", "payments", "orders", "packages"):
        if table in tables:
            op.drop_table(table)
