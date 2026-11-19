"""Reconcile package use counters previously incremented at voucher issue."""
from alembic import op
import sqlalchemy as sa

revision = "0017_package_usage"
down_revision = "0016_withdrawal_ledger"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""UPDATE coupons SET used_count = (
        SELECT COUNT(*) FROM redemptions WHERE redemptions.coupon_id = coupons.id
        AND redemptions.revoked_at IS NULL) WHERE kind = 'package'""")


    # Earlier multi-unit retries reserved only one unit. Rebuild just the
    # affected packages from their orders, preserving manually paused sales.
    conn = op.get_bind()
    affected = conn.execute(sa.text("""
        SELECT DISTINCT o.package_id FROM orders o
        WHERE o.quantity > 1 AND o.package_id IS NOT NULL
        AND (SELECT COUNT(*) FROM payments p WHERE p.order_id = o.id) > 1
    """)).scalars().all()
    for package_id in affected:
        params = {"id": package_id}
        remaining = conn.execute(sa.text("""
            SELECT p.stock_total - COALESCE(SUM(o.quantity), 0)
            FROM packages p LEFT JOIN orders o ON o.package_id = p.id
                AND o.payment_status IN ('pending', 'paid')
                AND o.refund_status <> 'refunded' AND o.settlement_status <> 'void'
            WHERE p.id = :id GROUP BY p.id, p.stock_total
        """), params).scalar_one()
        params["left"] = max(0, remaining)
        conn.execute(sa.text("""
            UPDATE packages SET stock_left = :left, status = CASE
                WHEN status IN ('active', 'sold_out') AND :left = 0 THEN 'sold_out'
                WHEN status = 'sold_out' AND :left > 0 THEN 'active'
                ELSE status END WHERE id = :id
        """), params)
        conn.execute(sa.text("""
            UPDATE coupons SET remaining = :left WHERE package_id = :id AND kind = 'package'
        """), params)


def downgrade():
    # Retain accurate counters when rolling back the application.
    pass
