"""Commission snapshot business regression; isolated SQLite or explicitly named PG DB."""
import os
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app import models, connect_service
from app.database import Base
from app.models_line_f import Invoice, MerchantCommissionRate, Settlement, SettlementItem, SettlementReversal
from app.routers.admin_v2 import generate_settlement, get_settlement, record_invoice_credit, run_reconciliation
from app.routers.coupons import scan_coupon
from app.schemas import ScanIn
from app.schemas_line_f import InvoiceCreditIn, ReconciliationRunIn, SettlementGenerateIn
from app.settlement_ledger import add_scanned_sale, reverse_sale


@pytest.fixture
def db():
    pg = os.environ.get('COMMISSION_TEST_PG_URL')
    if pg:
        assert (make_url(pg).database or '').startswith('coupon_commission_test_'), 'must use dedicated commission test DB'
    with TemporaryDirectory(prefix='coupon-commission-test-') as directory:
        url = pg or f"sqlite:///{Path(directory) / 'test.db'}"
        engine = create_engine(url)
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        try:
            yield session
        finally:
            session.close()
            Base.metadata.drop_all(engine)
            engine.dispose()


def setup_shop(db):
    vendor = models.User(email='commission@example.invalid', password_hash='test', role='vendor')
    db.add(vendor); db.flush()
    shop = models.Shop(vendor_id=vendor.id, name='Test shop', is_approved=True)
    db.add(shop); db.flush()
    coupon = models.Coupon(shop_id=shop.id, vendor_id=vendor.id, coupon_name='Package',
                           coupon_type='Price', discount_price=Decimal('100'),
                           valid_from=datetime.utcnow()-timedelta(days=1),
                           valid_to=datetime.utcnow()+timedelta(days=1))
    db.add(coupon); db.flush()
    return vendor, shop, coupon


def set_rate(db, shop, vendor, rate):
    row = db.query(MerchantCommissionRate).filter_by(shop_id=shop.id).first()
    if row is None:
        row = MerchantCommissionRate(shop_id=shop.id, rate=rate, set_by=vendor.id)
        db.add(row)
    else:
        row.rate = rate
    db.flush()


def redemption(db, vendor, shop, coupon, rate, amount='100'):
    # Synthetic paid order only; no Stripe calls or real customer data.
    order = models.Order(user_id=vendor.id, amount=Decimal(amount), paid_amount=Decimal(amount),
                         payment_status='paid', refund_status='none')
    db.add(order); db.flush()
    db.add(models.Payment(order_id=order.id, gateway='stripe', gateway_ref=f'pi_fixture_{order.id}',
                          amount=Decimal(amount), currency='HKD', status='succeeded'))
    item = models.WalletItem(user_id=vendor.id, coupon_id=coupon.id, source='purchase', order_id=order.id)
    db.add(item); db.flush()
    red = models.Redemption(wallet_item_id=item.id, coupon_id=coupon.id, shop_id=shop.id,
                            scanned_by_vendor_id=vendor.id, face_value=Decimal(amount),
                            commission_rate_snapshot=rate)
    db.add(red); db.flush()
    return red


def generate(db, vendor, shop):
    body = SettlementGenerateIn(shop_id=shop.id, period=datetime.utcnow().strftime('%Y-%m'))
    return generate_settlement(body, user=vendor, db=db)['settlement']


@pytest.mark.parametrize('reversal_reason', ['revoked', 'refunded'])
def test_mixed_rates_connect_statement_reconciliation_and_existing_invoice(db, reversal_reason):
    vendor, shop, coupon = setup_shop(db)
    set_rate(db, shop, vendor, Decimal('0.10'))
    first = redemption(db, vendor, shop, coupon, Decimal('0.10'))
    first_transfer = connect_service.record_redemption(db, first)
    set_rate(db, shop, vendor, Decimal('0.20'))
    second = redemption(db, vendor, shop, coupon, Decimal('0.20'))
    second_transfer = connect_service.record_redemption(db, second)
    db.commit()
    assert first_transfer['commission'] == 10
    assert second_transfer['commission'] == 20
    result = generate(db, vendor, shop)
    assert (result['gross_amount'], result['commission'], result['net_amount']) == (200, 30, 170)
    assert result['commission_rate_mixed'] is True
    assert result['applied_commission_rate'] is None
    statement = db.get(Settlement, result['id'])
    rows = db.query(SettlementItem).filter_by(settlement_id=statement.id).order_by(SettlementItem.id).all()
    assert [r.commission for r in rows] == [Decimal('10'), Decimal('20')]
    result = get_settlement(statement.id, user=vendor, db=db)['settlement']
    assert [item['commission_rate'] for item in result['items']] == [0.1, 0.2]
    report = run_reconciliation(ReconciliationRunIn(shop_id=shop.id, period=statement.period), user=vendor, db=db)['report']
    assert report['discrepancy_count'] == 0, report
    invoice = Invoice(shop_id=shop.id, settlement_id=statement.id, user_id=vendor.id,
                      title='Issued', amount=Decimal('170'), status='issued')
    db.add(invoice); db.commit()
    if reversal_reason == 'revoked':
        second.revoked_at = datetime.utcnow()
    else:
        order_id = db.get(models.WalletItem, second.wallet_item_id).order_id
        db.get(models.Order, order_id).refund_status = 'refunded'
    reverse_sale(db, second, reversal_reason); db.commit()
    assert statement.commission == Decimal('10')
    assert statement.net_amount == Decimal('90')
    reversal = db.query(SettlementReversal).filter_by(item_id=rows[1].id).one()
    assert reversal.commission == Decimal('-20')
    # Issued document amount remains intact until a real credit is recorded.
    assert db.get(Invoice, invoice.id).amount == Decimal('170')
    credit = record_invoice_credit(invoice.id, InvoiceCreditIn(amount=Decimal('80'), reference='credit-1'), user=vendor, db=db)
    assert credit['invoice']['net_issued_amount'] == 90
    again = run_reconciliation(ReconciliationRunIn(shop_id=shop.id, period=statement.period), user=vendor, db=db)['report']
    assert again['discrepancy_count'] == 0, again


def test_legacy_null_rate_keeps_statement_rate_and_new_line_uses_snapshot(db):
    vendor, shop, coupon = setup_shop(db)
    set_rate(db, shop, vendor, Decimal('0.10'))
    old = redemption(db, vendor, shop, coupon, None)
    db.commit()
    result = generate(db, vendor, shop)
    statement = db.get(Settlement, result['id'])
    assert result['commission'] == 10
    invoice = Invoice(shop_id=shop.id, settlement_id=statement.id, user_id=vendor.id,
                      title='Historical invoice', amount=Decimal('90'), status='issued')
    db.add(invoice); db.commit()
    set_rate(db, shop, vendor, Decimal('0.20'))
    new = redemption(db, vendor, shop, coupon, Decimal('0.20'))
    add_scanned_sale(db, new); db.commit()
    assert statement.commission == Decimal('30')
    rows = db.query(SettlementItem).filter_by(settlement_id=statement.id).order_by(SettlementItem.id).all()
    assert [r.commission for r in rows] == [Decimal('10'), Decimal('20')]
    assert db.get(models.Redemption, old.id).commission_rate_snapshot is None
    assert db.get(Invoice, invoice.id).amount == Decimal('90')
    report = run_reconciliation(ReconciliationRunIn(shop_id=shop.id, period=statement.period), user=vendor, db=db)['report']
    assert report['discrepancy_count'] == 0, report
    old.revoked_at = datetime.utcnow()
    reverse_sale(db, old, 'revoked'); db.commit()
    assert statement.commission == Decimal('20')
    assert statement.net_amount == Decimal('80')
    assert db.get(Invoice, invoice.id).amount == Decimal('90')
    record_invoice_credit(invoice.id, InvoiceCreditIn(amount=Decimal('10'), reference='legacy-credit'), user=vendor, db=db)
    report = run_reconciliation(ReconciliationRunIn(shop_id=shop.id, period=statement.period), user=vendor, db=db)['report']
    assert report['discrepancy_count'] == 0, report


def test_line_rounding_uses_each_redemption_rate(db):
    vendor, shop, coupon = setup_shop(db)
    set_rate(db, shop, vendor, Decimal('0.20'))
    for _ in range(2):
        redemption(db, vendor, shop, coupon, Decimal('0.10'), '0.05')
    db.commit()
    result = generate(db, vendor, shop)
    # Decimal's per-line rounding is retained rather than rounding aggregate.
    assert result['commission'] == 0
    assert result['net_amount'] == 0.10


def test_actual_scan_freezes_rate_before_later_rate_change(db):
    vendor, shop, coupon = setup_shop(db)
    coupon.status = 'active'
    set_rate(db, shop, vendor, Decimal('0.10'))
    order = models.Order(user_id=vendor.id, amount=Decimal('100'), paid_amount=Decimal('100'),
                         payment_status='paid', refund_status='none')
    db.add(order); db.flush()
    item = models.WalletItem(user_id=vendor.id, coupon_id=coupon.id, source='purchase',
                             order_id=order.id, code='123456', status=1,
                             qr_expires_at=datetime.utcnow() + timedelta(minutes=5))
    db.add(item); db.commit()
    result = scan_coupon(ScanIn(code='123456'), vendor=vendor, db=db)
    red = db.get(models.Redemption, result['redemption_id'])
    assert red.commission_rate_snapshot == Decimal('0.10')
    set_rate(db, shop, vendor, Decimal('0.20')); db.commit()
    statement = generate(db, vendor, shop)
    assert statement['commission'] == 10
    assert statement['items'][0]['commission_rate'] == 0.1
    # Header retains the pre-existing reference contract; the actual rate is separate.
    assert statement['commission_rate'] == 0.2
    assert statement['applied_commission_rate'] == 0.1


def test_postgres_scan_waits_for_rate_update_and_uses_committed_rate(db):
    if db.get_bind().dialect.name != 'postgresql':
        pytest.skip('requires real PostgreSQL row locks')
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event
    from app.settlement_ledger import lock_shop

    vendor, shop, coupon = setup_shop(db)
    coupon.status = 'active'
    set_rate(db, shop, vendor, Decimal('0.10'))
    order = models.Order(user_id=vendor.id, amount=100, paid_amount=100,
                         payment_status='paid', refund_status='none')
    db.add(order); db.flush()
    item = models.WalletItem(user_id=vendor.id, coupon_id=coupon.id, source='purchase',
                             order_id=order.id, code='654321', status=1,
                             qr_expires_at=datetime.utcnow() + timedelta(minutes=5))
    db.add(item); db.commit()
    shop_id, vendor_id = shop.id, vendor.id
    lock_shop(db, shop_id)
    set_rate(db, shop, vendor, Decimal('0.20'))
    entered = Event()
    factory = sessionmaker(bind=db.get_bind())

    def concurrent_scan():
        with factory() as other:
            entered.set()
            result = scan_coupon(ScanIn(code='654321'), vendor=other.get(models.User, vendor_id), db=other)
            return other.get(models.Redemption, result['redemption_id']).commission_rate_snapshot

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(concurrent_scan)
        try:
            assert entered.wait(5)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.3)
        finally:
            db.commit()  # release lock even if an assertion fails
        assert future.result(timeout=10) == Decimal('0.20')
