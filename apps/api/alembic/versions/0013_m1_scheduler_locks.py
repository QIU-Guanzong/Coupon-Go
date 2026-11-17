"""M1: scheduler_locks — single-runner claim rows for background jobs
on non-Postgres databases.

On Postgres the scheduler uses pg_try_advisory_lock (no table
needed); on SQLite / other dialects it claims a row in this table
(INSERT ... ON CONFLICT DO NOTHING + stale-lease reclaim). The row
is deleted when the job finishes; a crashed holder's row becomes
reclaimable after SCHEDULER_LOCK_LEASE_SECONDS.
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_m1_scheduler_locks"
down_revision = "0012_f8f9_member_invite"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "scheduler_locks" not in insp.get_table_names():
        op.create_table(
            "scheduler_locks",
            sa.Column("name", sa.String(64), primary_key=True),
            sa.Column("owner", sa.String(128), nullable=False,
                      server_default=""),
            sa.Column("acquired_at", sa.DateTime, nullable=True))


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "scheduler_locks" in insp.get_table_names():
        op.drop_table("scheduler_locks")
