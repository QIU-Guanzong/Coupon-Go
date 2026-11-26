"""Add users.token_version for DB-backed JWT revocation (S-H1)."""
import sqlalchemy as sa
from alembic import op

revision = "0032_user_token_version"
down_revision = "0031_dianping_mgmt_bridge"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("users")}
    if "token_version" not in columns:
        op.add_column(
            "users",
            sa.Column(
                "token_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("users")}
    if "token_version" in columns:
        op.drop_column("users", "token_version")
