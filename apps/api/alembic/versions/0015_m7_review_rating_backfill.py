"""M7: backfill reviews.counts_toward_rating to True (rating basis fix).

Pre-M7, a review that took the +15 points was stored with
counts_toward_rating=False and never entered the shop rating, so the
stars only reflected reviewers who forwent the points. M7 decouples
points from stars: every visible review counts toward the rating.
This migration flips every legacy False row to True so the stored
flag matches the new rule (create_review now always writes True and
recompute_rating aggregates all visible reviews).

Downgrade reconstructs the pre-M7 mapping exactly, because the old
rule was deterministic from points_given (points -> False, else
True); no information is lost.

down_revision points at 0014_m6_settlement_basis_backfill (chain tail).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "0015_m7_review_rating_backfill"
down_revision = "0014_m6_settlement_basis_backfill"
branch_labels = None
depends_on = None

# TRUE works on both backends (SQLite treats TRUE as 1, Postgres natively).
# IS NOT TRUE also catches NULLs (the column is nullable in the model).
BACKFILL_SQL = text(
    "UPDATE reviews SET counts_toward_rating = TRUE "
    "WHERE counts_toward_rating IS NOT TRUE"
)

DOWNGRADE_SQL = text(
    "UPDATE reviews SET counts_toward_rating = "
    "CASE WHEN points_given THEN FALSE ELSE TRUE END"
)


def backfill_counts_toward_rating(bind):
    """Set every legacy counts_toward_rating=False row to True.

    Takes a SQLAlchemy Connection (op.get_bind() under alembic, any
    connection in tests) so the same code is exercised by pytest.
    """
    bind.execute(BACKFILL_SQL)


def upgrade():
    backfill_counts_toward_rating(op.get_bind())


def downgrade():
    # Exact reconstruction of the pre-M7 rule (see module docstring).
    op.get_bind().execute(DOWNGRADE_SQL)
