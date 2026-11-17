"""F4/F6/F7: redemptions.face_value — face-value snapshot at scan time.

Additive nullable column; no existing column or constraint changes, so
upgrade is safe on a live DB. New redemptions carry their face value
(cash_amount, else discount_price, else regular_price, else 0 for
percentage-only / gift coupons) from the coupon row as it stood at
scan time; settlement generation and reconciliation read the snapshot
instead of the live coupon row, so a later price change cannot drift
the settlement basis. Legacy rows keep face_value NULL and fall back
to the live coupon price (see admin_v2._face_value).

down_revision points at 0009_gap9_review_helpful (chain tail).
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_f4f6f7_settlement_snapshot"
down_revision = "0009_gap9_review_helpful"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("redemptions")}
    if "face_value" not in cols:
        with op.batch_alter_table("redemptions") as batch_op:
            batch_op.add_column(sa.Column(
                "face_value", sa.Numeric(12, 2), nullable=True))


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("redemptions")}
    if "face_value" in cols:
        with op.batch_alter_table("redemptions") as batch_op:
            batch_op.drop_column("face_value")
