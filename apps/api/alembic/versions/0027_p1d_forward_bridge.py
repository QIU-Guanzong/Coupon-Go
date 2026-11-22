"""Join onboarding history with the current paid-coupon/points main line.

The historical onboarding revision keeps its original parent. Existing rows
and signatures are never rewritten; incompatible existing schemas fail loudly.
"""
import sqlalchemy as sa
from alembic import op

revision = "0027_p1d_forward_bridge"
down_revision = ("0026_p1c_allocation_bridge", "0022_p1d_merchant_onboarding")
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    required = {
        "merchant_applications": {
            "br_number": sa.String, "br_file_id": sa.Integer,
            "ci_file_id": sa.Integer, "director_id_file_id": sa.Integer,
            "shop_photo_file_ids": sa.Text, "stripe_account_id": sa.String,
            "stripe_charges_enabled": sa.Boolean, "stripe_payouts_enabled": sa.Boolean,
            "stripe_onboarding_updated_at": sa.DateTime,
            "contract_version_id": sa.Integer, "contract_signed_at": sa.DateTime,
            "contract_signed_ip": sa.String,
        },
        "merchant_files": {"id": sa.Integer, "application_id": sa.Integer,
                           "uploader_id": sa.Integer, "kind": sa.String,
                           "stored_name": sa.String, "sha256": sa.String},
        "merchant_contract_versions": {"id": sa.Integer, "version": sa.String,
                                       "body_text": sa.Text, "is_current": sa.Boolean},
        "merchant_contract_signatures": {"id": sa.Integer, "application_id": sa.Integer,
                                         "user_id": sa.Integer, "contract_version_id": sa.Integer,
                                         "pdf_file_id": sa.Integer},
    }
    for table, columns in required.items():
        if not inspector.has_table(table):
            raise RuntimeError(f"Missing onboarding table: {table}")
        actual = {c["name"]: c["type"] for c in inspector.get_columns(table)}
        for name, expected in columns.items():
            if name not in actual or not isinstance(actual[name], expected):
                raise RuntimeError(f"Incompatible onboarding column: {table}.{name}")


def downgrade():
    raise RuntimeError("Onboarding history is retained; downgrade requires a coordinated restore")
