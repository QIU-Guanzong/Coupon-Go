"""F8: shop-member invite flow — status / invited_by / invited_at.

Adds three columns to shop_members:
- status (pending/active): invites start pending; the invitee confirms
  them into active. Pre-existing rows are backfilled to active, since
  they were created by the old add-and-promote-immediately flow.
- invited_by / invited_at: who sent the invite and when.

down_revision points at 0009_gap9_review_helpful (chain tail at the
time of writing; the coordinator re-orders the chain on merge).
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_f8f9_member_invite"
down_revision = "0011_f5_commission_rate_table"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("shop_members")}
    if "status" not in cols:
        with op.batch_alter_table("shop_members") as batch_op:
            batch_op.add_column(sa.Column("status", sa.String(20),
                                          nullable=True))
        # Rows created before the invite flow were live memberships.
        op.execute("UPDATE shop_members SET status = 'active' "
                   "WHERE status IS NULL")
        with op.batch_alter_table("shop_members") as batch_op:
            batch_op.alter_column("status", nullable=False,
                                  existing_type=sa.String(20))
    if "invited_by" not in cols:
        with op.batch_alter_table("shop_members") as batch_op:
            batch_op.add_column(sa.Column(
                "invited_by", sa.Integer,
                sa.ForeignKey("users.id",
                              name="fk_shop_members_invited_by_users"),
                nullable=True))
    if "invited_at" not in cols:
        with op.batch_alter_table("shop_members") as batch_op:
            batch_op.add_column(sa.Column("invited_at", sa.DateTime,
                                          nullable=True))


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("shop_members")}
    for col in ("invited_at", "invited_by", "status"):
        if col in cols:
            with op.batch_alter_table("shop_members") as batch_op:
                batch_op.drop_column(col)
