"""P1: cashback revoke rollback + bargain gateway refunds.

* cashback_transactions.unlocked_by_redemption_id — which redemption
  unlocked this earning (set in record_spend; read by
  void_for_revocation on revoke).
* cashback_adjustments — negative adjustments for revoked earnings
  already withdrawn (see cashback_service.void_for_revocation).
* bargain_refunds.gateway_ref — the gateway refund id recorded by
  the P1-3 gateway refund attempt (re_test_… from the simulator).

All three are additive (new nullable column / new table); no existing
column or constraint changes, so upgrade is safe on a live DB.

down_revision points at 0005_fix3_row_lock_backstops (chain tail).
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_p1_cashback_revoke_bargain_refund"
down_revision = "0005_fix3_row_lock_backstops"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    txn_cols = {c["name"] for c in insp.get_columns("cashback_transactions")}
    if "unlocked_by_redemption_id" not in txn_cols:
        with op.batch_alter_table("cashback_transactions") as batch_op:
            batch_op.add_column(sa.Column(
                "unlocked_by_redemption_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_cashback_txn_unlocked_by_redemption",
                "redemptions", ["unlocked_by_redemption_id"], ["id"])
    if "cashback_adjustments" not in insp.get_table_names():
        op.create_table(
            "cashback_adjustments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(),
                      sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("redemption_id", sa.Integer(),
                      sa.ForeignKey("redemptions.id"), nullable=True),
            sa.Column("amount", sa.Numeric(10, 2), nullable=False),
            sa.Column("reason", sa.String(30), nullable=False,
                      server_default="revoked_withdrawn"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
    refund_cols = {c["name"] for c in insp.get_columns("bargain_refunds")}
    if "gateway_ref" not in refund_cols:
        with op.batch_alter_table("bargain_refunds") as batch_op:
            batch_op.add_column(sa.Column(
                "gateway_ref", sa.String(120), nullable=True,
                server_default=""))


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    refund_cols = {c["name"] for c in insp.get_columns("bargain_refunds")}
    if "gateway_ref" in refund_cols:
        with op.batch_alter_table("bargain_refunds") as batch_op:
            batch_op.drop_column("gateway_ref")
    if "cashback_adjustments" in insp.get_table_names():
        op.drop_table("cashback_adjustments")
    txn_cols = {c["name"] for c in insp.get_columns("cashback_transactions")}
    if "unlocked_by_redemption_id" in txn_cols:
        with op.batch_alter_table("cashback_transactions") as batch_op:
            batch_op.drop_constraint(
                "fk_cashback_txn_unlocked_by_redemption", type_="foreignkey")
            batch_op.drop_column("unlocked_by_redemption_id")
