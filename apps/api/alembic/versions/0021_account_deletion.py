"""M9-7: account-deletion audit columns on users.

Adds users.deleted_at + users.delete_reason. The user row and financial
ledgers remain for audit; the request handler anonymizes identity fields
and does not retain the optional free-form reason because it may contain
personal information. See docs/M97_ACCOUNT_DELETION_SCOPE.md for limits.
"""
import sqlalchemy as sa
from alembic import op

revision = "0021_account_deletion"
down_revision = "0020_m92_legal_consents"
branch_labels = None
depends_on = None


def upgrade():
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    if "deleted_at" not in cols:
        op.add_column("users", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    if "delete_reason" not in cols:
        op.add_column("users", sa.Column("delete_reason", sa.String(500), nullable=True))


def downgrade():
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    if "delete_reason" in cols:
        op.drop_column("users", "delete_reason")
    if "deleted_at" in cols:
        op.drop_column("users", "deleted_at")
