"""Cross-feature conservation on a synthetic multi-voucher order."""
from datetime import datetime, timezone
from decimal import Decimal

from app import config, models
from app.merchant_finance import sweep_monthly_settlements
from app.models_cashback import CashbackTransaction
from app.models_connect import ConnectLedgerEntry
from app.models_line_f import Settlement, SettlementItem, SettlementReversal
from tests import test_packages as base
from tests.test_packages import client
from tests.test_paid_points_allocation import make_order, scan


def test_multi_voucher_settlement_reversal_refund_and_monthly_retry(client, monkeypatch):
    monkeypatch.setattr(config, 'CASHBACK_ENABLED', True)
    customer, package, order, voucher_ids = make_order(client, 'final-queue-lifecycle', count=3)
    merchant, admin = base._vendor_headers(client), base._admin_headers(client)
    stock_after_purchase = base._stock_left(client, package['id'])
    redemptions = [scan(client, customer, merchant, item_id) for item_id in voucher_ids]
    period = datetime.now(timezone.utc).strftime('%Y-%m')
    created = client.post('/api/v3/admin/settlements/generate',
                          json={'shop_id': 1, 'period': period}, headers=admin)
    assert created.status_code == 201, created.text
    statement_id = created.json()['settlement']['id']
    with base.TestingSession() as db:
        statement = db.get(Settlement, statement_id)
        lines = db.query(SettlementItem).filter(SettlementItem.settlement_id == statement_id).all()
        assert sorted(line.amount for line in lines) == [Decimal('33.33'), Decimal('33.34'), Decimal('33.34')]
        assert (statement.gross_amount, statement.commission, statement.net_amount) == (Decimal('100.01'), Decimal('9.99'), Decimal('90.02'))
        entries = db.query(ConnectLedgerEntry).filter(ConnectLedgerEntry.redemption_id.in_(redemptions)).all()
        assert sum(entry.gross_amount for entry in entries) == Decimal('100.01')
        assert sum(entry.commission for entry in entries) == Decimal('9.99')
        assert sum(entry.net_amount for entry in entries) == Decimal('90.02')
    for redemption_id in redemptions:
        response = client.post(f'/api/v3/redemptions/{redemption_id}/revoke', headers=merchant)
        assert response.status_code == 200, response.text
    refund = client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=customer)
    assert refund.status_code == 200, refund.text
    assert refund.json()['refund']['status'] == 'refunded'
    assert base._stock_left(client, package['id']) == stock_after_purchase + 3
    assert client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=customer).status_code == 400
    assert base._stock_left(client, package['id']) == stock_after_purchase + 3
    with base.TestingSession() as db:
        statement = db.get(Settlement, statement_id)
        lines = db.query(SettlementItem).filter(SettlementItem.settlement_id == statement_id).all()
        reversals = db.query(SettlementReversal).filter(SettlementReversal.item_id.in_([line.id for line in lines])).all()
        assert len(lines) == len(reversals) == 3
        assert sum(row.amount for row in lines) + sum(row.amount for row in reversals) == Decimal('0')
        assert sum(row.commission for row in lines) + sum(row.commission for row in reversals) == Decimal('0')
        assert (statement.gross_amount, statement.commission, statement.net_amount) == (Decimal('0'), Decimal('0'), Decimal('0'))
        assert all(db.get(models.WalletItem, item_id).status == 3 for item_id in voucher_ids)
        cashback = db.query(CashbackTransaction).filter(CashbackTransaction.redemption_id.in_(redemptions)).all()
        assert len(cashback) == 3 and all(row.status == 'void' for row in cashback)
        awards = db.query(models.PointTransaction).filter(models.PointTransaction.reason == 'redeem', models.PointTransaction.ref_id.in_(voucher_ids)).all()
        reversals = db.query(models.PointTransaction).filter(models.PointTransaction.reason == 'revoke', models.PointTransaction.ref_id.in_(redemptions)).all()
        assert sum(row.delta for row in awards) + sum(row.delta for row in reversals) == 0
        assert len(db.query(ConnectLedgerEntry).filter(ConnectLedgerEntry.redemption_id.in_(redemptions)).all()) == 3
        first = sweep_monthly_settlements(db, period=period)
        second = sweep_monthly_settlements(db, period=period)
        assert first['generated'] == second['generated'] == 0
        assert db.query(Settlement).filter_by(shop_id=1, period=period).count() == 1
