"""Line B (reviews v1 + admin v1): reviews, shop_ratings,
merchant_applications, admin_audit_logs.

down_revision points at 0002_line_a (merged chain:
0001 -> 0002_line_a -> 0002_line_b).

Note: 0001's upgrade() runs Base.metadata.create_all(), so on a FRESH
database these tables may already exist (created by 0001). On a
database that was already at 0001 before this line landed, they do not
exist yet. upgrade() therefore guards each create with a table-exists
check, so it works in both cases; the migration still owns the tables
for downgrade.
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_line_b"
down_revision = "0002_line_a"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if "reviews" not in existing:
        _create_reviews()
    if "shop_ratings" not in existing:
        _create_shop_ratings()
    if "merchant_applications" not in existing:
        _create_merchant_applications()
    if "admin_audit_logs" not in existing:
        _create_admin_audit_logs()


def _create_reviews():
    op.create_table(
        "reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("redemption_id", sa.Integer(), sa.ForeignKey("redemptions.id"),
                  nullable=False, unique=True),
        sa.Column("wallet_item_id", sa.Integer(), sa.ForeignKey("wallet_items.id"),
                  nullable=False),
        sa.Column("taste", sa.Integer(), nullable=False),
        sa.Column("ambience", sa.Integer(), nullable=False),
        sa.Column("service", sa.Integer(), nullable=False),
        sa.Column("overall", sa.Float(), nullable=False),
        sa.Column("content", sa.Text(), default=""),
        sa.Column("images", sa.Text(), default="[]"),
        sa.Column("tags", sa.Text(), default="[]"),
        sa.Column("points_given", sa.Boolean(), default=False),
        sa.Column("counts_toward_rating", sa.Boolean(), default=False),
        sa.Column("status", sa.String(20), default="visible", nullable=False),
        sa.Column("reply_text", sa.Text(), default=""),
        sa.Column("replied_at", sa.DateTime(), nullable=True),
        sa.Column("replied_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("dispute_reason", sa.Text(), default=""),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )


def _create_shop_ratings():
    op.create_table(
        "shop_ratings",
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id"),
                  primary_key=True),
        sa.Column("overall", sa.Float(), nullable=True),
        sa.Column("taste", sa.Float(), nullable=True),
        sa.Column("ambience", sa.Float(), nullable=True),
        sa.Column("service", sa.Float(), nullable=True),
        sa.Column("review_count", sa.Integer(), default=0, nullable=False),
        sa.Column("updated_at", sa.DateTime()),
    )


def _create_merchant_applications():
    op.create_table(
        "merchant_applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("shop_name", sa.String(120), nullable=False),
        sa.Column("contact_name", sa.String(120), default=""),
        sa.Column("phone", sa.String(40), default=""),
        sa.Column("address", sa.String(255), default=""),
        sa.Column("business_license", sa.Text(), default=""),
        sa.Column("id_card", sa.Text(), default=""),
        sa.Column("shop_photos", sa.Text(), default="[]"),
        sa.Column("industry_license", sa.Text(), default=""),
        sa.Column("status", sa.String(20), default="pending", nullable=False),
        sa.Column("reject_reason", sa.Text(), default=""),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )


def _create_admin_audit_logs():
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("target_type", sa.String(40), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("detail", sa.Text(), default=""),
        sa.Column("created_at", sa.DateTime()),
    )


def downgrade():
    op.drop_table("admin_audit_logs")
    op.drop_table("merchant_applications")
    op.drop_table("shop_ratings")
    op.drop_table("reviews")
