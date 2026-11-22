"""Dianping block 1 — merchant management (GAP §1).

- coupon_branches (§1.2): coupon <-> branch applicability. No rows for
  a coupon = usable at every branch of its shop.
- shops.business_hours / gallery / announcement (§1.4): merchant
  self-maintenance fields.

Numbering: remote main's chain ends at 0020_m92_legal_consents. 0021 is
claimed by the in-flight M9-7 / P1-A / P1-B / P1-C work and 0022 by
P1-D (stage1/p1-d-merchant-onboarding, down_revision 0021) — none of
them is on main yet. This migration therefore takes the next free
number, 0023, with down_revision 0020. If M9-7 (0021) lands first, this
revision must be renumbered / rebased onto the new chain tip before
merge; the merge order is Miso's call (see GAP §5.8 / merge-order note).
"""
import sqlalchemy as sa
from alembic import op

revision = "0023_dianping_merchant_mgmt"
down_revision = "0020_m92_legal_consents"
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("coupon_branches"):
        op.create_table(
            "coupon_branches",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("coupon_id", sa.Integer,
                      sa.ForeignKey("coupons.id"), nullable=False),
            sa.Column("branch_id", sa.Integer,
                      sa.ForeignKey("branches.id"), nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.UniqueConstraint("coupon_id", "branch_id",
                                name="uq_coupon_branch"),
        )
    # No separate index on coupon_id: the unique constraint above already
    # builds a unique index on (coupon_id, branch_id), whose leftmost
    # column serves coupon lookups. (A standalone ix_ index here would
    # be redundant — and on fresh databases 0001's create_all builds
    # this table before this migration runs, so the index would never be
    # created there while downgrade would still try to drop it.)
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("shops")}
    if "business_hours" not in cols:
        op.add_column("shops", sa.Column(
            "business_hours", sa.Text(), nullable=False,
            server_default=""))
    if "gallery" not in cols:
        op.add_column("shops", sa.Column(
            "gallery", sa.Text(), nullable=False, server_default="[]"))
    if "announcement" not in cols:
        op.add_column("shops", sa.Column(
            "announcement", sa.Text(), nullable=False, server_default=""))


def downgrade():
    if op.get_bind().execute(
            sa.text("SELECT count(*) FROM coupon_branches")).scalar():
        raise RuntimeError(
            "Populated coupon-branch links require a coordinated data restore")
    op.drop_table("coupon_branches")
    for col in ("announcement", "gallery", "business_hours"):
        op.drop_column("shops", col)
