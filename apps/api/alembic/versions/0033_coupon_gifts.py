"""Preserve the sender and recipient wallet history for coupon gifts."""

import sqlalchemy as sa
from alembic import op

revision = "0033_coupon_gifts"
down_revision = "0032_user_token_version"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("coupon_gifts"):
        return  # Revision 0001 creates tables from current metadata.
    op.create_table(
        "coupon_gifts",
        sa.Column("sender_wallet_item_id", sa.Integer(), sa.ForeignKey("wallet_items.id"),
                  primary_key=True),
        sa.Column("nonce", sa.String(32), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("recipient_wallet_item_id", sa.Integer(), sa.ForeignKey("wallet_items.id"),
                  nullable=True, unique=True),
    )


def downgrade():
    if not sa.inspect(op.get_bind()).has_table("coupon_gifts"):
        return
    if op.get_bind().execute(sa.text("SELECT 1 FROM coupon_gifts LIMIT 1")).scalar():
        raise RuntimeError("Coupon gift history requires a coordinated restore.")
    op.drop_table("coupon_gifts")
