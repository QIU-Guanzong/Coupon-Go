"""P1-A: paid coupons — coupons.sale_price + orders.coupon_id.

down_revision points at 0020_m92_legal_consents (M9-2, in flight):
merge order matters — M9-2 must land before this migration runs on a
real database, otherwise alembic cannot resolve the chain. If M9-2 is
delayed, rebase this file's down_revision to 0019_invoice_credits
and renumber only if 0020 stays free (do NOT silently create two
heads off 0019).

Idempotent: skips when the columns already exist (fresh databases
built from Base.metadata.create_all already have them).
"""
from alembic import op
import sqlalchemy as sa

revision = "0021_p1a_paid_coupons"
down_revision = "0020_m92_legal_consents"
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
    constraints = {c["name"] for c in
                   sa.inspect(bind).get_check_constraints("coupons")}
    if COUPON_CK in constraints:
        with op.batch_alter_table("coupons") as batch_op:
            batch_op.drop_constraint(COUPON_CK, type_="check")
    order_cols = {c["name"] for c in sa.inspect(bind).get_columns("orders")}
    if "coupon_id" in order_cols:
        with op.batch_alter_table("orders") as batch_op:
            batch_op.drop_column("coupon_id")
    coupon_cols = {c["name"] for c in sa.inspect(bind).get_columns("coupons")}
    if "sale_price" in coupon_cols:
        with op.batch_alter_table("coupons") as batch_op:
            batch_op.drop_column("sale_price")
