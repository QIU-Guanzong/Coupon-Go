"""Line F (admin v2): settlements, settlement_items,
reconciliation_reports, reconciliation_discrepancies, invoices,
cms_banners, cms_announcements, shop_members; categories.is_active.

down_revision points at v0.1's 0001 per the line-F task contract; the
coordinator re-orders the chain on merge (line B's 0002 also points at
0001). Same guard pattern as 0002: 0001's upgrade() runs
Base.metadata.create_all(), so on a FRESH database these tables may
already exist. upgrade() guards each create with a table-exists check.
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_line_f"
down_revision = "0002_line_c"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if "settlements" not in existing:
        _create_settlements()
    if "settlement_items" not in existing:
        _create_settlement_items()
    if "reconciliation_reports" not in existing:
        _create_reconciliation_reports()
    if "reconciliation_discrepancies" not in existing:
        _create_reconciliation_discrepancies()
    if "invoices" not in existing:
        _create_invoices()
    if "cms_banners" not in existing:
        _create_cms_banners()
    if "cms_announcements" not in existing:
        _create_cms_announcements()
    if "shop_members" not in existing:
        _create_shop_members()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("categories")}
    if "is_active" not in cols:
        op.add_column("categories",
                      sa.Column("is_active", sa.Boolean(), default=True))


def _create_settlements():
    op.create_table(
        "settlements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("redemption_count", sa.Integer(), default=0, nullable=False),
        sa.Column("gross_amount", sa.Numeric(12, 2), default=0, nullable=False),
        sa.Column("commission_rate", sa.Numeric(5, 4), default=0, nullable=False),
        sa.Column("commission", sa.Numeric(12, 2), default=0, nullable=False),
        sa.Column("net_amount", sa.Numeric(12, 2), default=0, nullable=False),
        sa.Column("status", sa.String(20), default="pending", nullable=False),
        sa.Column("dispute_reason", sa.Text(), default=""),
        sa.Column("generated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.UniqueConstraint("shop_id", "period", name="uq_settlement_shop_period"),
    )


def _create_settlement_items():
    op.create_table(
        "settlement_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("settlement_id", sa.Integer(), sa.ForeignKey("settlements.id"), nullable=False),
        sa.Column("redemption_id", sa.Integer(), sa.ForeignKey("redemptions.id"), nullable=False, unique=True),
        sa.Column("coupon_id", sa.Integer(), sa.ForeignKey("coupons.id"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), default=0, nullable=False),
        sa.Column("commission", sa.Numeric(12, 2), default=0, nullable=False),
        sa.Column("created_at", sa.DateTime()),
    )


def _create_reconciliation_reports():
    op.create_table(
        "reconciliation_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("settlement_id", sa.Integer(), sa.ForeignKey("settlements.id"), nullable=True),
        sa.Column("redemption_count", sa.Integer(), default=0, nullable=False),
        sa.Column("settlement_line_count", sa.Integer(), default=0, nullable=False),
        sa.Column("discrepancy_count", sa.Integer(), default=0, nullable=False),
        sa.Column("status", sa.String(20), default="open", nullable=False),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.UniqueConstraint("shop_id", "period", name="uq_recon_shop_period"),
    )


def _create_reconciliation_discrepancies():
    op.create_table(
        "reconciliation_discrepancies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("reconciliation_reports.id"), nullable=False),
        sa.Column("discrepancy_type", sa.String(40), nullable=False),
        sa.Column("redemption_id", sa.Integer(), sa.ForeignKey("redemptions.id"), nullable=True),
        sa.Column("settlement_item_id", sa.Integer(), sa.ForeignKey("settlement_items.id"), nullable=True),
        sa.Column("expected_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("actual_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("status", sa.String(20), default="open", nullable=False),
        sa.Column("resolution", sa.Text(), default=""),
        sa.Column("resolved_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime()),
    )


def _create_invoices():
    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("settlement_id", sa.Integer(), sa.ForeignKey("settlements.id"), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("tax_id", sa.String(60), default=""),
        sa.Column("invoice_type", sa.String(20), default="receipt", nullable=False),
        sa.Column("status", sa.String(20), default="pending", nullable=False),
        sa.Column("reject_reason", sa.Text(), default=""),
        sa.Column("decided_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("issued_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )


def _create_cms_banners():
    op.create_table(
        "cms_banners",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("image", sa.String(255), default=""),
        sa.Column("link_type", sa.String(20), default="none", nullable=False),
        sa.Column("link_target", sa.String(255), default=""),
        sa.Column("sort_order", sa.Integer(), default=0, nullable=False),
        sa.Column("start_at", sa.DateTime(), nullable=True),
        sa.Column("end_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(20), default="draft", nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )


def _create_cms_announcements():
    op.create_table(
        "cms_announcements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("body", sa.Text(), default=""),
        sa.Column("status", sa.String(20), default="draft", nullable=False),
        sa.Column("sort_order", sa.Integer(), default=0, nullable=False),
        sa.Column("start_at", sa.DateTime(), nullable=True),
        sa.Column("end_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )


def _create_shop_members():
    op.create_table(
        "shop_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(20), default="merchant-admin", nullable=False),
        sa.Column("added_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime()),
        sa.UniqueConstraint("shop_id", "user_id", name="uq_shop_member"),
    )


def downgrade():
    op.drop_table("shop_members")
    op.drop_table("cms_announcements")
    op.drop_table("cms_banners")
    op.drop_table("invoices")
    op.drop_table("reconciliation_discrepancies")
    op.drop_table("reconciliation_reports")
    op.drop_table("settlement_items")
    op.drop_table("settlements")
    op.drop_column("categories", "is_active")
