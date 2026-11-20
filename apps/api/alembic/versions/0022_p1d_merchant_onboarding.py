"""P1-D: merchant onboarding, Hong Kong fields (0022).

New tables: merchant_files, merchant_contract_versions,
merchant_contract_signatures. New columns on merchant_applications:
br_number, br_file_id, ci_file_id, director_id_file_id,
shop_photo_file_ids, stripe_account_id, stripe_charges_enabled,
stripe_payouts_enabled, stripe_onboarding_updated_at,
contract_version_id, contract_signed_at, contract_signed_ip.

OLD-DATA HANDLING (explicit): the mainland columns business_license,
id_card, industry_license and the legacy shop_photos JSON list are
NOT touched -- not renamed, not dropped, not rewritten. Old rows keep
their values; the API still returns them for audit. New columns are
all nullable / defaulted, so no backfill is needed.

Also inserts the contract v1 placeholder row (same text as seed.py,
same approach as M9-2's legal v1 rows) so an upgraded database can
sign contracts immediately; the text is marked 待法務確認.

MERGE ORDER: down_revision is 0021_account_deletion (M9-7). This
migration only applies after M9-2 (0020) and M9-7 (0021) are on main.

Downgrade refuses when any contract signature exists: signatures are
legal archives and must never be silently discarded -- coordinate a
data restore instead.
"""
import sqlalchemy as sa
from alembic import op

revision = "0022_p1d_merchant_onboarding"
down_revision = "0021_account_deletion"
branch_labels = None
depends_on = None

CONTRACT_V1_BODY = (
    "【待法務確認】商户合作协议（v1 占位版）。\n"
    "本版本正文待法務定稿，僅供開發與流程測試使用；"
    "正式版本發布後，商户須重新簽署當前版本方可通過入駐審核。\n"
    "商户入駐平台即表示同意遵守平台規則、按時結算，"
    "並授權平台按約定比例收取佣金。")


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("merchant_applications")}
    new_columns = [
        ("br_number", sa.String(20), {"server_default": ""}),
        ("br_file_id", sa.Integer(), {}),
        ("ci_file_id", sa.Integer(), {}),
        ("director_id_file_id", sa.Integer(), {}),
        ("shop_photo_file_ids", sa.Text(), {"server_default": "'[]'"}),
        ("stripe_account_id", sa.String(120), {"server_default": ""}),
        ("stripe_charges_enabled", sa.Boolean(),
         {"server_default": sa.text("false")}),
        ("stripe_payouts_enabled", sa.Boolean(),
         {"server_default": sa.text("false")}),
        ("stripe_onboarding_updated_at", sa.DateTime(), {}),
        ("contract_version_id", sa.Integer(), {}),
        ("contract_signed_at", sa.DateTime(), {}),
        ("contract_signed_ip", sa.String(45), {"server_default": ""}),
    ]
    # File-id / contract columns are plain integers here: the models
    # declare the FKs and fresh databases build them from metadata.
    # The migration only needs the columns present for existing DBs.
    for name, coltype, opts in new_columns:
        if name in cols:
            continue
        kwargs = {"nullable": True}
        if "server_default" in opts:
            kwargs["server_default"] = opts["server_default"]
        op.add_column("merchant_applications",
                      sa.Column(name, coltype, **kwargs))
    if not insp.has_table("merchant_files"):
        op.create_table(
            "merchant_files",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("application_id", sa.Integer,
                      sa.ForeignKey("merchant_applications.id"),
                      nullable=True),
            sa.Column("uploader_id", sa.Integer,
                      sa.ForeignKey("users.id"), nullable=False),
            sa.Column("kind", sa.String(20), nullable=False),
            sa.Column("stored_name", sa.String(120), nullable=False),
            sa.Column("original_filename", sa.String(255), nullable=False,
                      server_default=""),
            sa.Column("content_type", sa.String(120), nullable=False,
                      server_default=""),
            sa.Column("size_bytes", sa.Integer, nullable=False,
                      server_default="0"),
            sa.Column("sha256", sa.String(64), nullable=False,
                      server_default=""),
            sa.Column("created_at", sa.DateTime, nullable=True),
        )
    # Index is declared on the model too (index=True), so 0001's
    # metadata.create_all() builds it on FRESH databases; this guard
    # covers EXISTING databases, where the table is created above.
    # (Checked separately from has_table: on a fresh DB the table
    # already exists from 0001 and this still fires.)
    if not sa.inspect(bind).has_index(
            "merchant_files", "ix_merchant_files_application_id"):
        op.create_index("ix_merchant_files_application_id",
                        "merchant_files", ["application_id"])
    if not insp.has_table("merchant_contract_versions"):
        op.create_table(
            "merchant_contract_versions",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("version", sa.String(20), nullable=False),
            sa.Column("title", sa.String(200), nullable=False,
                      server_default=""),
            sa.Column("body_text", sa.Text, nullable=False,
                      server_default=""),
            sa.Column("is_current", sa.Boolean, nullable=False,
                      server_default=sa.text("false")),
            sa.Column("published_at", sa.DateTime, nullable=True),
            sa.Column("created_by", sa.Integer,
                      sa.ForeignKey("users.id"), nullable=True),
            sa.UniqueConstraint("version",
                                name="uq_merchant_contract_version"),
        )
    if not insp.has_table("merchant_contract_signatures"):
        op.create_table(
            "merchant_contract_signatures",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("application_id", sa.Integer,
                      sa.ForeignKey("merchant_applications.id"),
                      nullable=False),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"),
                      nullable=False),
            sa.Column("contract_version_id", sa.Integer,
                      sa.ForeignKey("merchant_contract_versions.id"),
                      nullable=False),
            sa.Column("signed_at", sa.DateTime, nullable=True),
            sa.Column("ip_address", sa.String(45), nullable=False,
                      server_default=""),
            sa.Column("pdf_file_id", sa.Integer,
                      sa.ForeignKey("merchant_files.id"), nullable=True),
            sa.UniqueConstraint("application_id", "contract_version_id",
                                name="uq_contract_signature_app_version"),
        )
    bind.execute(sa.text(
        "INSERT INTO merchant_contract_versions "
        "(version, title, body_text, is_current, published_at) "
        "SELECT '1', '商户合作协议', :body, TRUE, CURRENT_TIMESTAMP "
        "WHERE NOT EXISTS (SELECT 1 FROM merchant_contract_versions "
        "WHERE is_current)"
    ).bindparams(body=CONTRACT_V1_BODY))


def downgrade():
    bind = op.get_bind()
    if sa.inspect(bind).has_table("merchant_contract_signatures"):
        count = bind.execute(
            sa.text("SELECT count(*) FROM merchant_contract_signatures")
        ).scalar()
        if count:
            raise RuntimeError(
                "Signed contracts exist; refusing to drop the P1-D "
                "archive tables -- coordinate a data restore first")
    # Drop the new merchant_applications columns FIRST: on Postgres
    # their FKs (contract_version_id, br/ci/director file ids) block
    # dropping the referenced tables.
    cols = {c["name"] for c in
            sa.inspect(bind).get_columns("merchant_applications")}
    for name in ("contract_signed_ip", "contract_signed_at",
                 "contract_version_id", "stripe_onboarding_updated_at",
                 "stripe_payouts_enabled", "stripe_charges_enabled",
                 "stripe_account_id", "shop_photo_file_ids",
                 "director_id_file_id", "ci_file_id", "br_file_id",
                 "br_number"):
        if name in cols:
            op.drop_column("merchant_applications", name)
    if sa.inspect(bind).has_table("merchant_contract_signatures"):
        op.drop_table("merchant_contract_signatures")
    if sa.inspect(bind).has_table("merchant_contract_versions"):
        op.drop_table("merchant_contract_versions")
    if sa.inspect(bind).has_table("merchant_files"):
        if sa.inspect(bind).has_index(
                "merchant_files", "ix_merchant_files_application_id"):
            op.drop_index("ix_merchant_files_application_id",
                          table_name="merchant_files")
        op.drop_table("merchant_files")
