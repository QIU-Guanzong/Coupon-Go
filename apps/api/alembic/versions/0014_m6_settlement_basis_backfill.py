"""M6: backfill redemptions.face_value to the actual-paid basis.

Pre-M6 snapshots were written under the old face-value order
(cash_amount, else discount_price, else regular_price): a free
coupon could carry regular_price, and a package redemption carried
discount_price (= package price) instead of the order's paid amount
(which differs when quantity > 1). This migration rewrites every
resolvable snapshot under the new basis (see app.settlement_basis):
package redemption -> Order.paid_amount (0 when the order was
refunded), price coupon -> discount_price, cash coupon ->
cash_amount, everything else -> 0. Legacy NULL snapshots are filled
the same way.

Only the Redemption rows change. Settlements and settlement items
already generated are NEVER touched here: a period settled under
the old basis keeps its old amounts, and reconciliation flags the
difference as amount_mismatch for manual handling. Downgrade is a
deliberate no-op: the pre-M6 snapshots cannot be reconstructed.

down_revision points at 0013_m1_scheduler_locks (chain tail).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "0014_m6_settlement_basis_backfill"
down_revision = "0013_m1_scheduler_locks"
branch_labels = None
depends_on = None

# Same CASE as settlement_basis, in SQL so the backfill runs on any
# backend (SQLite in tests, Postgres in production) without loading
# the ORM. Rows whose wallet item or coupon row is missing are left
# untouched (their snapshot stays NULL and the runtime fallback
# computes the basis live).
BACKFILL_SQL = text("""
UPDATE redemptions SET face_value = (
  SELECT CASE
    WHEN o.id IS NOT NULL AND o.refund_status = 'refunded' THEN 0
    WHEN o.id IS NOT NULL THEN COALESCE(o.paid_amount, 0)
    WHEN c.discount_price IS NOT NULL THEN c.discount_price
    WHEN c.cash_amount IS NOT NULL THEN c.cash_amount
    ELSE 0
  END
  FROM wallet_items wi
  LEFT JOIN orders o ON o.id = wi.order_id
  JOIN coupons c ON c.id = redemptions.coupon_id
  WHERE wi.id = redemptions.wallet_item_id
)
WHERE EXISTS (
  SELECT 1 FROM wallet_items wi2
  WHERE wi2.id = redemptions.wallet_item_id
)
AND EXISTS (
  SELECT 1 FROM coupons c2 WHERE c2.id = redemptions.coupon_id
)
""")


def backfill_face_values(bind):
    """Rewrite resolvable face_value snapshots to the M6 basis.

    Takes a SQLAlchemy Connection (op.get_bind() under alembic, any
    connection in tests) so the same code is exercised by pytest.
    """
    bind.execute(BACKFILL_SQL)


def upgrade():
    backfill_face_values(op.get_bind())


def downgrade():
    # No-op by design: pre-M6 snapshots are not recoverable, and
    # silently re-deriving them from live rows would be wrong.
    pass
