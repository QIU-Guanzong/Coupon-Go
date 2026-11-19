"""Keep merchant cash movements and credit notes separate from original sales."""
from decimal import Decimal
import sqlalchemy as sa
from alembic import op

revision = "0018_settlement_ledger"
down_revision = "0017_package_usage"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "financial_snapshot" not in {c["name"] for c in inspector.get_columns("reconciliation_reports")}:
        op.add_column("reconciliation_reports", sa.Column("financial_snapshot", sa.Text()))
    if "rounding_adjustment" not in {c["name"] for c in inspector.get_columns("settlement_items")}:
        op.add_column("settlement_items", sa.Column("rounding_adjustment", sa.Numeric(12, 2), nullable=False, server_default="0"))
    if not inspector.has_table("settlement_reversals"):
        op.create_table("settlement_reversals",
            sa.Column("item_id", sa.Integer, sa.ForeignKey("settlement_items.id"), primary_key=True),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("commission", sa.Numeric(12, 2), nullable=False),
            sa.Column("reason", sa.String(20), nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False))
    if not inspector.has_table("settlement_transfers"):
        op.create_table("settlement_transfers",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("settlement_id", sa.Integer, sa.ForeignKey("settlements.id"), nullable=False, index=True),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False), sa.Column("kind", sa.String(20), nullable=False),
            sa.Column("reference", sa.String(120), nullable=False),
            sa.Column("actor_id", sa.Integer, sa.ForeignKey("users.id")), sa.Column("created_at", sa.DateTime, nullable=False),
            sa.UniqueConstraint("settlement_id", "reference", name="uq_settlement_transfer_reference"))
    op.execute("""INSERT INTO settlement_transfers (settlement_id, amount, kind, reference, actor_id, created_at)
        SELECT s.id, s.net_amount, 'payment', 'legacy-' || CAST(s.id AS VARCHAR), s.decided_by,
               COALESCE(s.paid_at,s.decided_at,s.created_at,CURRENT_TIMESTAMP) FROM settlements s
        WHERE s.status='paid' AND NOT EXISTS (SELECT 1 FROM settlement_transfers t WHERE t.settlement_id=s.id)""")
    # Distribute only the known legacy difference between rounding a total and
    # rounding each sale. Preserve the header, the original payout and other discrepancies.
    for s in conn.execute(sa.text("""SELECT id,gross_amount,commission,commission_rate FROM settlements
           WHERE NOT EXISTS (SELECT 1 FROM settlement_items i JOIN settlement_reversals r ON r.item_id=i.id
                             WHERE i.settlement_id=settlements.id)""")).mappings().all():
        lines = conn.execute(sa.text("SELECT id,amount,commission FROM settlement_items WHERE settlement_id=:id ORDER BY id"), {"id":s["id"]}).mappings().all()
        rate = Decimal(str(s["commission_rate"]))
        gross = Decimal(0)
        allocated = Decimal(0)
        cent = Decimal(".01")
        if (sum(Decimal(str(i["amount"])) for i in lines) != Decimal(str(s["gross_amount"]))
                or (Decimal(str(s["gross_amount"]))*rate).quantize(cent) != Decimal(str(s["commission"]))
                or sum(Decimal(str(i["commission"])) for i in lines) == Decimal(str(s["commission"]))
                or any((Decimal(str(i["amount"]))*rate).quantize(cent) != Decimal(str(i["commission"])) for i in lines)):
            continue
        for i in lines:
            gross += Decimal(str(i["amount"]))
            cumulative = (gross * rate).quantize(cent)
            conn.execute(sa.text("UPDATE settlement_items SET commission=:fee, rounding_adjustment=:delta WHERE id=:id"),
                         {"fee":str(cumulative-allocated), "delta":str(cumulative-allocated-Decimal(str(i["commission"]))), "id":i["id"]})
            allocated = cumulative
    rows = conn.execute(sa.text("""SELECT i.id,i.settlement_id,i.amount,i.commission,r.revoked_at,o.refund_status
        FROM settlement_items i JOIN redemptions r ON r.id=i.redemption_id
        JOIN wallet_items w ON w.id=r.wallet_item_id LEFT JOIN orders o ON o.id=w.order_id
        WHERE (r.revoked_at IS NOT NULL OR o.refund_status='refunded')
        AND NOT EXISTS (SELECT 1 FROM settlement_reversals v WHERE v.item_id=i.id)""")).mappings().all()
    for row in rows:
        params = dict(row, reason="revoked" if row["revoked_at"] else "refunded")
        conn.execute(sa.text("""INSERT INTO settlement_reversals (item_id,amount,commission,reason,created_at)
            VALUES (:id,0-:amount,0-:commission,:reason,COALESCE(:revoked_at,CURRENT_TIMESTAMP))"""),params)
        conn.execute(sa.text("""UPDATE settlements SET gross_amount=ROUND(gross_amount-:amount,2),
            commission=ROUND(commission-:commission,2),net_amount=ROUND(net_amount-(:amount-:commission),2),
            redemption_count=redemption_count-1, decided_at=CASE WHEN status='approved' THEN NULL ELSE decided_at END,
            decided_by=CASE WHEN status='approved' THEN NULL ELSE decided_by END,
            status=CASE WHEN status='approved' THEN 'pending' ELSE status END WHERE id=:settlement_id"""),params)


def downgrade():
    # Without the ledger an older binary could pay a corrected statement twice.
    if op.get_bind().execute(sa.text("SELECT count(*) FROM settlement_transfers")).scalar() or op.get_bind().execute(
            sa.text("SELECT count(*) FROM settlement_reversals")).scalar():
        raise RuntimeError("A populated settlement ledger requires a coordinated data restore")
    op.drop_table("settlement_transfers")
    op.drop_table("settlement_reversals")
    op.drop_column("settlement_items", "rounding_adjustment")
    op.drop_column("reconciliation_reports", "financial_snapshot")
