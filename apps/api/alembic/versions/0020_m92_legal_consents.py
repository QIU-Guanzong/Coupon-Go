"""Record explicitly accepted legal document pairs."""
import sqlalchemy as sa
from alembic import op

revision = "0020_m92_legal_consents"
down_revision = "0019_invoice_credits"
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("legal_acceptances"):
        op.create_table(
            "legal_acceptances",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
            sa.Column("terms_version", sa.String(64), nullable=False),
            sa.Column("privacy_version", sa.String(64), nullable=False),
            sa.Column("terms_url", sa.String(1024), nullable=False),
            sa.Column("privacy_url", sa.String(1024), nullable=False),
            sa.Column("accepted_at", sa.DateTime, nullable=False),
            sa.UniqueConstraint("user_id", "terms_version", "privacy_version",
                                name="uq_legal_acceptance_versions"),
        )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM legal_acceptances")).scalar():
        raise RuntimeError("A populated legal acceptance ledger requires coordinated retention")
    op.drop_table("legal_acceptances")
