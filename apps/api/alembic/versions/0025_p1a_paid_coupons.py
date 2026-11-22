"""P1-A: paid coupons — coupons.sale_price + orders.coupon_id.

The listing migration already owns sale_price and its stock constraint;
this revision only adds order.coupon_id if it is absent.

Idempotent: skips when the columns already exist (fresh databases
built from Base.metadata.create_all already have them).
"""
from alembic import op
import sqlalchemy as sa

revision = "0025_p1a_paid_coupons"
down_revision = "0024_listing_template_versions"
branch_labels = None
depends_on = None

COUPON_CK = "ck_coupons_remaining_nonneg"


def upgrade():
    bind = op.get_bind()
    coupon_cols = {c["name"] for c in sa.inspect(bind).get_columns("coupons")}
    if "sale_price" not in coupon_cols:
        with op.batch_alter_table("coupons") as batch_op:
            batch_op.add_column(
                sa.Column("sale_price", sa.Numeric(10, 2), nullable=True))
    order_cols = {c["name"] for c in sa.inspect(bind).get_columns("orders")}
    if "coupon_id" not in order_cols:
        with op.batch_alter_table("orders") as batch_op:
            batch_op.add_column(
                sa.Column("coupon_id", sa.Integer,
                          sa.ForeignKey("coupons.id"), nullable=True))
    # FIX-3 backstop (mirrors packages.stock_left): remaining can never
    # go below zero even if a row lock fails to serialise. Clamp any
    # legacy negative rows first so the constraint cannot fail on
    # existing data.
    bind.execute(sa.text("UPDATE coupons SET remaining = 0 WHERE remaining < 0"))
    constraints = {c["name"] for c in
                   sa.inspect(bind).get_check_constraints("coupons")}
    if COUPON_CK not in constraints:
        with op.batch_alter_table("coupons") as batch_op:
            batch_op.create_check_constraint(COUPON_CK, "remaining >= 0")


def downgrade():
    bind = op.get_bind()
    order_cols = {c["name"] for c in sa.inspect(bind).get_columns("orders")}
    if "coupon_id" in order_cols:
        if bind.execute(sa.text("SELECT COUNT(*) FROM orders WHERE coupon_id IS NOT NULL")).scalar():
            raise RuntimeError("Paid coupon orders require a coordinated restore before downgrade")
        with op.batch_alter_table("orders") as batch_op:
            batch_op.drop_column("coupon_id")
