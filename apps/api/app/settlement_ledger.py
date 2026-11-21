"""Sales, credit notes and cash movements on a merchant statement."""
from decimal import Decimal

from sqlalchemy import func

from . import models
from .models_line_f import Settlement, SettlementItem, SettlementReversal, SettlementTransfer

CENT = Decimal("0.01")


def redemption_commission_rate(redemption, fallback):
    """Use the scan-time rate; NULL history keeps its statement's rate."""
    snapshot = getattr(redemption, "commission_rate_snapshot", None)
    return Decimal(str(snapshot if snapshot is not None else fallback))


def lock_shop(db, shop_id):
    # A first statement and an in-flight scan must agree which side posts the sale.
    if db.get_bind().dialect.name == "sqlite":
        db.query(models.Shop).filter_by(id=shop_id).update({"name": models.Shop.name}, synchronize_session=False)
    return db.query(models.Shop).filter_by(id=shop_id).with_for_update(key_share=True).first()


def lock_statement(db, statement_id):
    if db.get_bind().dialect.name == "sqlite":
        db.query(Settlement).filter_by(id=statement_id).update(
            {"status": Settlement.status, "updated_at": Settlement.updated_at}, synchronize_session=False)
    return db.query(Settlement).filter_by(id=statement_id).with_for_update().populate_existing().first()


def post_amounts(s, gross, commission, count):
    s.gross_amount += gross
    s.commission += commission
    s.net_amount += gross - commission
    s.redemption_count += count
    if s.status == "approved":
        s.status = "pending"
        s.decided_at = s.decided_by = None


def add_scanned_sale(db, red):
    lock_shop(db, red.shop_id)
    sid = db.query(Settlement.id).filter_by(shop_id=red.shop_id, period=red.scanned_at.strftime("%Y-%m")).scalar()
    if sid is None:
        return
    s = lock_statement(db, sid)
    fee = (red.face_value * redemption_commission_rate(red, s.commission_rate)).quantize(CENT)
    db.add(SettlementItem(settlement_id=s.id, redemption_id=red.id, coupon_id=red.coupon_id,
                          amount=red.face_value, commission=fee))
    post_amounts(s, red.face_value, fee, 1)


def reverse_sale(db, red, reason):
    lock_shop(db, red.shop_id)
    item = db.query(SettlementItem).filter_by(redemption_id=red.id).first()
    if item is None:
        return
    s = lock_statement(db, item.settlement_id)
    if db.get(SettlementReversal, item.id) is not None:
        return
    db.add(SettlementReversal(item_id=item.id, amount=-item.amount, commission=-item.commission,
                              reason=reason))
    post_amounts(s, -item.amount, -item.commission, -1)
    db.flush()


def cash_totals(db, s):
    transferred = db.query(func.coalesce(func.sum(SettlementTransfer.amount), 0)).filter_by(
        settlement_id=s.id).scalar()
    return transferred, s.net_amount - transferred
