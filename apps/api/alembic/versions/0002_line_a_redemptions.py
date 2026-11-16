"""Line A: redemption hardening columns on redemptions (report §11.2).

- idempotency_key: unique, nullable (NULL = legacy direct scan without a key)
- prepared_at: when the two-step prepare check happened
- revoked_at / revoked_by: 10-minute revoke window (report §5.4 item 11)

down_revision points at v0.1's 0001; the coordinator re-orders the chain
at merge time.

Idempotency note: 0001 builds tables with Base.metadata.create_all, so on
a FRESH database the columns already exist when this migration runs.
Every step below is therefore guarded by an existence check — it is a
no-op on fresh DBs and adds the columns on DBs migrated from v0.1.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0002_line_a"
down_revision = "0001"
branch_labels = None
depends_on = None

COLUMNS = [
    ("idempotency_key", sa.String(80)),
    ("prepared_at", sa.DateTime()),
    ("revoked_at", sa.DateTime()),
    ("revoked_by", sa.Integer()),
]
CONSTRAINT = "uq_redemptions_idempotency_key"


def _existing_columns(bind):
    return {c["name"] for c in inspect(bind).get_columns("redemptions")}


def _has_unique_index(bind):
    """True if some unique index already covers idempotency_key."""
    for idx in inspect(bind).get_indexes("redemptions"):
        if idx.get("unique") and idx.get("column_names") == ["idempotency_key"]:
            return True
    return False


def upgrade():
    bind = op.get_bind()
    existing = _existing_columns(bind)
    for name, type_ in COLUMNS:
        if name not in existing:
            op.add_column("redemptions", sa.Column(name, type_, nullable=True))
    # Unique index (not a table constraint) so the migration also runs on
    # SQLite, which has no ALTER support for constraints. On PostgreSQL
    # this enforces uniqueness the same way.
    if not _has_unique_index(bind):
        op.create_index(CONSTRAINT, "redemptions", ["idempotency_key"],
                        unique=True)


def downgrade():
    bind = op.get_bind()
    existing = _existing_columns(bind)
    if _has_unique_index(bind):
        op.drop_index(CONSTRAINT, table_name="redemptions")
    for name, _ in reversed(COLUMNS):
        if name in existing:
            op.drop_column("redemptions", name)
