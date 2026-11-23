"""Regressions retained from the earlier multi-voucher integration branch.

Voucher rows reconstructed by the shared helper model historical multi-voucher
orders; Stripe Connect operations use the test gateway, not live Stripe.
"""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.database import Base, get_db
from app.main import app
from app.models_packages import Order
from app.seed import seed
from tests import test_packages as base
from tests.test_paid_points_allocation import make_order, scan


@pytest.fixture(scope="module")
def client():
    # Use a separate SQLite engine: the shared package fixture keeps its
    # in-memory database across test modules, including generated statements.
    # An explicitly supplied disposable PostgreSQL URL is supported as well.
    engine = (create_engine(base.PG_URL, pool_size=5, max_overflow=5) if base.PG_URL else
              create_engine("sqlite://", connect_args={"check_same_thread": False},
                            poolclass=StaticPool))
    sessions = sessionmaker(bind=engine, autoflush=False)
    if base.PG_URL:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with sessions() as db:
        seed(db)
    previous_sessions = base.TestingSession
    previous_override = app.dependency_overrides.get(get_db)
    base.TestingSession = sessions

    def isolated_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = isolated_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        base.TestingSession = previous_sessions
        if previous_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous_override
        engine.dispose()


def test_three_vouchers_connect_statement_revoke_and_refund_conserve(client):
    """Combined #12/#19/#16 path: no extra receivable or refund."""
    from datetime import datetime, timezone
    from app.models_connect import ConnectLedgerEntry
    from app.models_line_f import Settlement, SettlementItem, SettlementReversal

    user, pkg, order, ids = make_order(client, 'combined-finance', count=3)
    vendor = base._vendor_headers(client)
    admin = base._admin_headers(client)
    before_stock = base._stock_left(client, pkg['id'])
    reds = [scan(client, user, vendor, item_id) for item_id in ids]
    with base.TestingSession() as db:
        entries = db.query(ConnectLedgerEntry).filter(ConnectLedgerEntry.redemption_id.in_(reds)).all()
        assert len(entries) == 3
        assert sum(e.gross_amount for e in entries) == Decimal('100.01')
        assert sum(e.commission for e in entries) == Decimal('9.99')
        assert sum(e.net_amount for e in entries) == Decimal('90.02')

    period = datetime.now(timezone.utc).strftime('%Y-%m')
    response = client.post('/api/v3/admin/settlements/generate', json={'shop_id': 1, 'period': period}, headers=admin)
    assert response.status_code == 201, response.text
    statement = response.json()['settlement']
    with base.TestingSession() as db:
        lines = db.query(SettlementItem).filter(SettlementItem.redemption_id.in_(reds)).all()
        assert sum(i.amount for i in lines) == Decimal('100.01')
        assert sum(i.commission for i in lines) == Decimal('9.99')
    assert statement['payment_mode'] == 'connect'
    projection = client.get(f"/api/v3/orders/{order['id']}", headers=user).json()['order']
    assert projection['settlement_amount_complete'] is True
    assert (projection['commission_amount'], projection['net_amount']) == (9.99, 90.02)
    denied = client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=user)
    assert denied.status_code == 400
    for red in reds:
        result = client.post(f'/api/v3/redemptions/{red}/revoke', headers=vendor)
        assert result.status_code == 200, result.text
    result = client.post(f"/api/v3/orders/{order['id']}/refund", json={}, headers=user)
    assert result.status_code == 200, result.text
    assert result.json()['refund']['status'] == 'refunded'
    with base.TestingSession() as db:
        s = db.get(Settlement, statement['id'])
        assert s.gross_amount == Decimal(str(statement['gross_amount'])) - Decimal('100.01')
        assert s.commission == Decimal(str(statement['commission'])) - Decimal('9.99')
        assert s.net_amount == Decimal(str(statement['net_amount'])) - Decimal('90.02')
        lines = db.query(SettlementItem).filter(SettlementItem.redemption_id.in_(reds)).all()
        assert len(lines) == 3
        reversals = db.query(SettlementReversal).filter(SettlementReversal.item_id.in_([i.id for i in lines])).all()
        assert len(reversals) == 3
        assert sum(r.amount for r in reversals) == Decimal('-100.01')
        entries = db.query(ConnectLedgerEntry).filter(ConnectLedgerEntry.redemption_id.in_(reds)).all()
        assert all(e.status == 'reversed' for e in entries)
        assert all(db.get(models.WalletItem, item_id).status == 3 for item_id in ids)
    assert base._stock_left(client, pkg['id']) == before_stock + len(ids)


def test_refunded_legacy_redemption_does_not_create_untracked_balance(client):
    from app.merchant_finance import shop_balance
    from app.models_connect import ConnectLedgerEntry

    user, _, order, ids = make_order(client, 'legacy-refunded', count=1)
    vendor = base._vendor_headers(client)
    red = scan(client, user, vendor, ids[0])
    with base.TestingSession() as db:
        shop = db.get(models.Shop, 1)
        previous = shop_balance(db, shop)['legacy_untracked_redemptions']
        db.query(ConnectLedgerEntry).filter_by(redemption_id=red).delete()
        db.get(Order, order['id']).refund_status = 'refunded'
        db.flush()
        assert shop_balance(db, shop)['legacy_untracked_redemptions'] == previous
