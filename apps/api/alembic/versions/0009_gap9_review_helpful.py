"""Gap 9: review_helpful_votes — one helpful vote per (review, user).

Additive new table; no existing column or constraint changes, so
upgrade is safe on a live DB. The count is derived from this table
(no denormalised counter to drift); the unique constraint makes a
duplicate vote impossible even under concurrency.

down_revision points at 0008_gap6_withdrawal_failure_reason (chain tail).
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_gap9_review_helpful"
down_revision = "0008_gap6_withdrawal_failure_reason"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "review_helpful_votes" not in insp.get_table_names():
        op.create_table(
            "review_helpful_votes",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("review_id", sa.Integer,
                      sa.ForeignKey("reviews.id"), nullable=False),
            sa.Column("user_id", sa.Integer,
                      sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=True),
            sa.UniqueConstraint("review_id", "user_id",
                                name="uq_review_helpful_vote"),
        )


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "review_helpful_votes" in insp.get_table_names():
        op.drop_table("review_helpful_votes")
