"""Line E (bargain 2C): bargain_campaigns / bargain_sessions /
bargain_helps / bargain_refunds + coupons.kind, coupons.package_id,
wallet_items.source, wallet_items.order_id (report §11.1/§11.2).

Idempotent: every create/add is guarded by an inspector check, so this
runs cleanly both on an existing v0.1 database and on a fresh database
where 0001's create_all already built the tables from the models.
(The coupons/wallet_items columns are also touched by line A; the guard
keeps a re-run / double-apply from failing. The coordinator re-orders
the revision chain on merge into main.)
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_bargain_e"
down_revision = "0002_cashback"
branch_labels = None
depends_on = None


def _has_table(bind, name):
    return sa.inspect(bind).has_table(name)


def _columns(bind, table):
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade():
    bind = op.get_bind()
    if not _has_table(bind, "bargain_campaigns"):
        op.create_table(
            "bargain_campaigns",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("package_id", sa.Integer(), nullable=True),
            sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id"),
                      nullable=False),
            sa.Column("name", sa.String(160), nullable=False),
            sa.Column("items", sa.Text(), nullable=True),
            sa.Column("person_count", sa.Integer(), nullable=True),
            sa.Column("original_price", sa.Numeric(10, 2), nullable=False),
            sa.Column("floor_price", sa.Numeric(10, 2), nullable=False),
            sa.Column("stock_total", sa.Integer(), nullable=False),
            sa.Column("stock_left", sa.Integer(), nullable=False),
            sa.Column("helper_limit", sa.Integer(), nullable=True),
            sa.Column("duration_minutes", sa.Integer(), nullable=True),
            sa.Column("lock_stock", sa.Boolean(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
    if not _has_table(bind, "bargain_sessions"):
        op.create_table(
            "bargain_sessions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("campaign_id", sa.Integer(),
                      sa.ForeignKey("bargain_campaigns.id"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"),
                      nullable=False),
            sa.Column("current_price", sa.Numeric(10, 2), nullable=False),
            sa.Column("target_price", sa.Numeric(10, 2), nullable=False),
            sa.Column("paid_amount", sa.Numeric(10, 2), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("helper_count", sa.Integer(), nullable=True),
            sa.Column("stock_locked", sa.Boolean(), nullable=True),
            sa.Column("wallet_item_id", sa.Integer(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
    if not _has_table(bind, "bargain_helps"):
        op.create_table(
            "bargain_helps",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("session_id", sa.Integer(),
                      sa.ForeignKey("bargain_sessions.id"), nullable=False),
            sa.Column("helper_user_id", sa.Integer(),
                      sa.ForeignKey("users.id"), nullable=False),
            sa.Column("cut_amount", sa.Numeric(10, 2), nullable=False),
            sa.Column("device_id", sa.String(120), nullable=True),
            sa.Column("is_new_user", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("session_id", "helper_user_id",
                                name="uq_help_once"),
        )
    if not _has_table(bind, "bargain_refunds"):
        op.create_table(
            "bargain_refunds",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("session_id", sa.Integer(),
                      sa.ForeignKey("bargain_sessions.id"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"),
                      nullable=False),
            sa.Column("amount", sa.Numeric(10, 2), nullable=False),
            sa.Column("reason", sa.String(30), nullable=False),
            sa.Column("status", sa.String(20), nullable=True),
            sa.Column("note", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
    cols = _columns(bind, "coupons")
    if "kind" not in cols:
        op.add_column("coupons", sa.Column(
            "kind", sa.String(20), nullable=False, server_default="coupon"))
    if "package_id" not in cols:
        op.add_column("coupons", sa.Column("package_id", sa.Integer(),
                                           nullable=True))
    cols = _columns(bind, "wallet_items")
    if "source" not in cols:
        op.add_column("wallet_items", sa.Column(
            "source", sa.String(20), nullable=False, server_default="claim"))
    if "order_id" not in cols:
        op.add_column("wallet_items", sa.Column("order_id", sa.Integer(),
                                                nullable=True))


def downgrade():
    bind = op.get_bind()
    for table in ("bargain_refunds", "bargain_helps", "bargain_sessions",
                  "bargain_campaigns"):
        if _has_table(bind, table):
            op.drop_table(table)
    cols = _columns(bind, "coupons")
    if "kind" in cols:
        op.drop_column("coupons", "kind")
    if "package_id" in cols:
        op.drop_column("coupons", "package_id")
    cols = _columns(bind, "wallet_items")
    if "source" in cols:
        op.drop_column("wallet_items", "source")
    if "order_id" in cols:
        op.drop_column("wallet_items", "order_id")
