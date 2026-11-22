"""A real ledger journey with the local Stripe simulator and an isolated DB."""
from datetime import datetime, timezone
from decimal import Decimal

from app import config, models
from app.models_cashback import CashbackTransaction
from app.models_connect import ConnectLedgerEntry
from app.models_line_f import Settlement, SettlementItem, SettlementReversal, SettlementTransfer
from app.models_packages import Package
from tests import test_p1b_connect_fixes as base
from tests.test_p1b_connect_fixes import client


def test_paid_out_merchant_redemption_reversed_then_customer_refunded(client, monkeypatch):
    monkeypatch.setattr(config, "CASHBACK_ENABLED", True)
    vendor, admin = base._vendor_headers(client), base._admin_headers(client)
    shop = client.post('/api/v3/merchant/shops', json={'name': '结算专项店'}, headers=vendor)
    assert shop.status_code == 201, shop.text
    shop_id = shop.json()['shop']['id']
    base._onboard_sync(client, shop_id)
    customer = base._register_and_login(client, 'settlement-flow@example.com')
    package = base._create_package(client, '专项套餐', stock=5, price=150, shop_id=shop_id)
    order = base._buy_paid(client, customer, package['id'])
    with base._db() as db:
        assert db.get(Package, package['id']).stock_left == 4
    scan = base._qr_and_scan(client, customer, vendor, order['wallet_item_id'])
    redemption_id, entry_id = scan['redemption_id'], scan['connect']['entry_id']
    assert scan['connect']['transfer'] == 'pending'
    base._sweep()
    assert base._entry(entry_id)['status'] == 'transferred'
    assert base._balance(client, shop_id)['available'] == 135

    period = datetime.now(timezone.utc).strftime('%Y-%m')
    result = client.post('/api/v3/admin/settlements/generate',
                         json={'shop_id': shop_id, 'period': period}, headers=admin)
    assert result.status_code == 201, result.text
    statement = result.json()['settlement']
    assert (Decimal(str(statement['gross_amount'])), Decimal(str(statement['commission'])),
            Decimal(str(statement['net_amount']))) == (Decimal('150'), Decimal('15'), Decimal('135'))
    assert client.post(f"/api/v3/admin/settlements/{statement['id']}/approve", headers=admin).status_code == 200
    assert client.post(f"/api/v3/admin/settlements/{statement['id']}/pay", headers=admin).status_code == 409
    payout = client.post('/api/v3/merchant/connect/payouts',
                         json={'shop_id': shop_id, 'amount': 135, 'request_id': 'settlement-flow-payout-01'}, headers=vendor)
    assert payout.status_code == 200, payout.text
    assert base._balance(client, shop_id)['available'] == 0

    revoke = client.post(f'/api/v3/redemptions/{redemption_id}/revoke', headers=vendor)
    assert revoke.status_code == 200, revoke.text
    base._sweep()
    assert base._entry(entry_id)['status'] == 'reversed'
    assert base._balance(client, shop_id)['available'] == -135
    refund = client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=customer)
    assert refund.status_code == 200, refund.text
    assert refund.json()['refund']['status'] == 'refunded'
    assert base._balance(client, shop_id)['available'] == -135

    with base._db() as db:
        s = db.get(Settlement, statement['id'])
        item = db.query(SettlementItem).filter_by(settlement_id=s.id, redemption_id=redemption_id).one()
        reversal = db.query(SettlementReversal).filter_by(item_id=item.id).one()
        assert (s.gross_amount, s.commission, s.net_amount) == (Decimal('0'), Decimal('0'), Decimal('0'))
        assert (reversal.amount, reversal.commission) == (Decimal('-150'), Decimal('-15'))
        assert db.query(SettlementTransfer).filter_by(settlement_id=s.id).count() == 0
        transfer = db.get(ConnectLedgerEntry, entry_id)
        assert transfer.status == 'reversed' and transfer.pending_reversal is False
        assert db.query(ConnectLedgerEntry).filter_by(shop_id=shop_id, kind='payout', status='paid_out').count() == 1
        cashback = db.query(CashbackTransaction).filter_by(redemption_id=redemption_id).one()
        assert cashback.status == 'void'
        assert db.get(models.WalletItem, order['wallet_item_id']).status == 3
        assert db.get(Package, package['id']).stock_left == 5
