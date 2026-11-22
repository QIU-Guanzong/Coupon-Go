from decimal import Decimal

from tests import test_packages as base
from tests.test_packages import client
from tests.test_paid_points_allocation import make_order, scan
from app.models_connect import ConnectLedgerEntry


def test_multi_voucher_connect_and_statement_use_each_paid_share(client):
    user, _, order, ids = make_order(client, "connect-basis")
    vendor = base._vendor_headers(client)
    reds = [scan(client, user, vendor, item_id) for item_id in ids]
    with base.TestingSession() as db:
        entries = (db.query(ConnectLedgerEntry)
                   .filter(ConnectLedgerEntry.redemption_id.in_(reds))
                   .order_by(ConnectLedgerEntry.redemption_id).all())
        assert [x.gross_amount for x in entries] == [Decimal("33.34"), Decimal("33.34"), Decimal("33.33")]
        assert sum(x.gross_amount for x in entries) == Decimal("100.01")
        assert sum(x.commission for x in entries) == Decimal("9.99")
        assert sum(x.net_amount for x in entries) == Decimal("90.02")
    assert client.post(f"/api/v3/redemptions/{reds[0]}/revoke", headers=vendor).status_code == 200
    repeated = scan(client, user, vendor, ids[0])
    with base.TestingSession() as db:
        previous = db.query(ConnectLedgerEntry).filter_by(redemption_id=reds[0]).one()
        latest = db.query(ConnectLedgerEntry).filter_by(redemption_id=repeated).one()
        assert previous.status == "reversed"
        assert latest.gross_amount == Decimal("33.34")
