"""Join old P1-A databases and the listing branch without rewriting data."""
import sqlalchemy as sa
from alembic import op

revision = "0025_p1a_forward_bridge"
down_revision = ("0024_listing_template_versions", "0021_p1a_paid_coupons")
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    coupon_cols = {column["name"]: column for column in inspector.get_columns("coupons")}
    order_cols = {column["name"]: column for column in inspector.get_columns("orders")}
    price = coupon_cols.get("sale_price")
    coupon_id = order_cols.get("coupon_id")
    if (price is None or not isinstance(price["type"], sa.Numeric) or
            price["type"].precision != 10 or price["type"].scale != 2 or
            coupon_id is None or not isinstance(coupon_id["type"], sa.Integer)):
        raise RuntimeError("Paid coupon schema needs manual reconciliation before migration")
    constraints = {item["name"] for item in inspector.get_check_constraints("coupons")}
    if "ck_coupons_remaining_nonneg" not in constraints:
        raise RuntimeError("Paid coupon stock constraint needs manual reconciliation")


def downgrade():
    raise RuntimeError("Paid coupon history requires a coordinated restore before downgrade")
