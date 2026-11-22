"""Dianping listing (§3): coupons.sale_price + coupons.per_user_limit.

- sale_price (NULL / 0 = free, > 0 = paid) uses the P1-A calibre
  exactly: P1-A's own migration (0021_p1a_paid_coupons, local-only,
  down_revision 0020) adds the same column plus orders.coupon_id.
  This migration is written to be merge-order safe: upgrade() skips
  a column that already exists, and the P1-A file does the same, so
  whichever of the two lands first, the second is a no-op for this
  column. The two files share the number 0021 only while both are
  unmerged — the later one must be renumbered at merge time (see
  docs/DIANPING_LISTING_API.md §衔接）; the revision ids differ, so
  alembic never confuses the two heads.
- per_user_limit (default 1) backs §3.4; existing rows backfill to 1
  (= previous behaviour: one claim per user).
- ck_coupons_remaining_nonneg mirrors the packages stock backstop
  (FIX-3): remaining can never go below zero even if a row lock
  fails to serialise. Same constraint name as P1-A's file, guarded
  by an existence check on both sides.

Idempotent: skips when the columns / constraint already exist (fresh
databases built from Base.metadata.create_all already have them).
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_dianping_listing"
down_revision = "0021_account_deletion"
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
    if "per_user_limit" not in coupon_cols:
        with op.batch_alter_table("coupons") as batch_op:
            batch_op.add_column(
                sa.Column("per_user_limit", sa.Integer(), nullable=True))
    bind.execute(sa.text(
        "UPDATE coupons SET per_user_limit = 1 WHERE per_user_limit IS NULL"))
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
    coupon_cols = {c["name"] for c in sa.inspect(bind).get_columns("coupons")}
    if "per_user_limit" in coupon_cols:
        with op.batch_alter_table("coupons") as batch_op:
            batch_op.drop_column("per_user_limit")
    if "sale_price" in coupon_cols:
        with op.batch_alter_table("coupons") as batch_op:
            batch_op.drop_column("sale_price")
