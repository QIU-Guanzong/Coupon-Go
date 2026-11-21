"""Dianping §4: record how each redemption was made (method + note).

Adds two columns to redemptions:
* method — "scan" (QR scanned), "manual" (6 digits typed in),
  "supplementary" (§4.1 after-the-fact scan, no code presented),
  "batch" (§4.3 multi-item scan). Default "scan" so every pre-existing
  row is a normal QR scan.
* note — free-text operator note (e.g. the supplementary-scan reason).

MERGE NOTE — migration-number collision, needs Miso's ruling before
merge: this file claims 0021 on main (whose chain ends at 0020), but
the unmerged M9-7 branch and the unmerged P1-A branch ALSO claim 0021
in their own migration files, and P1-D's 0022 declares
down_revision = 0021_account_deletion (M9-7). Do NOT merge this
alongside those branches without renumbering one of them first; the
suggested order in GAP_ANALYSIS_20261121.md (M9-7 first, then P1-A,
then the rest) still stands.
"""
import sqlalchemy as sa
from alembic import op

revision = "0021_dianping_redemption"
down_revision = "0020_m92_legal_consents"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    cols = {c["name"] for c in sa.inspect(conn).get_columns("redemptions")}
    if "method" not in cols:
        op.add_column("redemptions",
                      sa.Column("method", sa.String(20), nullable=False,
                                server_default="scan"))
    if "note" not in cols:
        op.add_column("redemptions",
                      sa.Column("note", sa.Text(), nullable=True))


def downgrade():
    conn = op.get_bind()
    cols = {c["name"] for c in sa.inspect(conn).get_columns("redemptions")}
    if "note" in cols:
        op.drop_column("redemptions", "note")
    if "method" in cols:
        op.drop_column("redemptions", "method")
