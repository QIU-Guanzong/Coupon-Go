"""#19 reporting stays consistent while #16 verifies uncertain provider results."""
from datetime import datetime, timedelta, timezone

from tests import test_p1b_connect_fixes as base
from tests.test_connect_transfer_reconcile import _uncertain_transfer, _uncertain_reversal
from app import models
from app.models_connect import ConnectLedgerEntry
from app.models_line_f import Settlement, SettlementTransfer

client = base.client


def _last_month():
    return (datetime.now(timezone.utc).replace(day=1) - timedelta(days=1)).strftime('%Y-%m')


def test_unknown_transfer_then_verified_statement_uses_one_ledger(client, monkeypatch):
    shop_id = 2
    entry_id, redemption_id, provider_id = _uncertain_transfer(client, monkeypatch, 'stats-transfer', shop_id)
    vendor, admin = base._vendor_headers(client), base._admin_headers(client)
    before = client.get(f'/api/v3/merchant/shops/{shop_id}/balance', headers=vendor).json()
    assert before['unknown_transfer_count'] == 1 and before['withdrawable'] == 0
    assert before['unknown_transfer_amount'] > 0
    result = client.post(f'/api/v3/admin/connect/transfers/{entry_id}/reconcile',
                         json={'provider_id': provider_id}, headers=admin)
    assert result.status_code == 200, result.text
    after = client.get(f'/api/v3/merchant/shops/{shop_id}/balance', headers=vendor).json()
    assert after['unknown_transfer_count'] == 0
    assert after['withdrawable'] == before['unknown_transfer_amount']
    period = _last_month()
    with base._db() as db:
        db.get(models.Redemption, redemption_id).scanned_at = datetime.fromisoformat(f'{period}-15T12:00:00')
        db.commit()
    generated = client.post('/api/v3/admin/settlements/generate',
                            json={'shop_id': shop_id, 'period': period}, headers=admin)
    assert generated.status_code == 201, generated.text
    statement = generated.json()['settlement']
    assert statement['payment_mode'] == 'connect'
    assert client.post(f"/api/v3/admin/settlements/{statement['id']}/approve", headers=admin).status_code == 200
    assert client.post(f"/api/v3/admin/settlements/{statement['id']}/pay", headers=admin).status_code == 409
    with base._db() as db:
        assert db.query(SettlementTransfer).filter_by(settlement_id=statement['id']).count() == 0


def test_unknown_reversal_stays_visible_until_provider_confirmation(client, monkeypatch):
    shop_id = 9
    entry_id, provider_id = _uncertain_reversal(client, monkeypatch, 'stats-reversal', shop_id)
    vendor, admin = base._vendor_headers(client), base._admin_headers(client)
    balance = client.get(f'/api/v3/merchant/shops/{shop_id}/balance', headers=vendor).json()
    assert balance['unknown_reversal_count'] == 1 and balance['withdrawable'] == 0
    entries = client.get(f'/api/v3/merchant/shops/{shop_id}/balance/entries', headers=vendor).json()['entries']
    with base._db() as db:
        redemption_id = db.get(ConnectLedgerEntry, entry_id).redemption_id
    assert next(e for e in entries if e['redemption_id'] == redemption_id)['state'] == 'reversal_unknown'
    result = client.post(f'/api/v3/admin/connect/transfers/{entry_id}/reversal/reconcile',
                         json={'provider_id': provider_id}, headers=admin)
    assert result.status_code == 200 and result.json()['status'] == 'reversed', result.text
    assert client.get(f'/api/v3/merchant/shops/{shop_id}/balance', headers=vendor).json()['unknown_reversal_count'] == 0
    entries = client.get(f'/api/v3/merchant/shops/{shop_id}/balance/entries', headers=vendor).json()['entries']
    assert next(e for e in entries if e['redemption_id'] == redemption_id)['state'] == 'reversed'
    with base._db() as db:
        db.get(models.Redemption, redemption_id).scanned_at = datetime(2016, 4, 1)
        db.commit()
        from app.merchant_finance import catch_up_monthly_settlements
        catch_up_monthly_settlements(db)
        assert not db.query(Settlement).filter_by(shop_id=shop_id, period='2016-04').first()
