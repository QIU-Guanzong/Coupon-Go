"""P1-A: paid coupons — coupons.sale_price + orders.coupon_id.

The historical revision and parent are retained for databases that ran
this branch before merge. A later forward bridge joins it to listing.

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
    # legacy negative rows need manual reconciliation, not silent rewriting.
    if bind.execute(sa.text("SELECT COUNT(*) FROM coupons WHERE remaining < 0")).scalar():
        raise RuntimeError("Negative coupon stock requires reconciliation before migration")
    constraints = {c["name"] for c in
                   sa.inspect(bind).get_check_constraints("coupons")}
    if COUPON_CK not in constraints:
        with op.batch_alter_table("coupons") as batch_op:
            batch_op.create_check_constraint(COUPON_CK, "remaining >= 0")


def downgrade():
    raise RuntimeError("Paid coupon history requires a coordinated restore before downgrade")
