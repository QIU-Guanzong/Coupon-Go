"""Unify historical redemption schemas without rewriting existing sales."""
import pytest
from sqlalchemy import create_engine, inspect, text
from tests.test_p1c_allocation_bridge import database_url, alembic


@pytest.mark.parametrize('prior', ['0021_dianping_redemption', '0029_application_connect_bridge'])
def test_redemption_legacy_and_main_upgrade_preserving_records(database_url, prior):
    alembic(database_url, 'upgrade', prior)
    engine = create_engine(database_url)
    with engine.begin() as conn:
        if prior == '0029_application_connect_bridge':
            if inspect(conn).has_table('redemption_batches'):
                conn.execute(text('DROP TABLE redemption_batches'))
            for name in ('method', 'note'):
                if name in {c['name'] for c in inspect(conn).get_columns('redemptions')}:
                    conn.execute(text(f'ALTER TABLE redemptions DROP COLUMN {name}'))
        conn.execute(text("INSERT INTO users(id,email,password_hash,role) VALUES (1,'legacy-redemption@example.invalid','x','vendor')"))
        conn.execute(text("INSERT INTO shops(id,vendor_id,name) VALUES (1,1,'old shop')"))
        conn.execute(text("INSERT INTO coupons(id,shop_id,vendor_id,coupon_type,coupon_name,valid_from,valid_to,total,remaining,status,kind,refund_policy) VALUES (1,1,1,'Price','old coupon','2026-01-01','2027-01-01',5,4,'active','coupon','anytime')"))
        conn.execute(text("INSERT INTO wallet_items(id,user_id,coupon_id,status,source) VALUES (1,1,1,2,'claim')"))
        conn.execute(text("INSERT INTO redemptions(id,wallet_item_id,coupon_id,shop_id,scanned_by_vendor_id,face_value,commission_rate_snapshot) VALUES (1,1,1,1,1,19.95,0.125)"))
        if prior == '0021_dianping_redemption':
            conn.execute(text("UPDATE redemptions SET method='manual',note='legacy note' WHERE id=1"))
    alembic(database_url, 'upgrade', 'head')
    with engine.connect() as conn:
        assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0030_redemption_batch_bridge'
        amount, rate, method, note = conn.execute(text('SELECT face_value,commission_rate_snapshot,method,note FROM redemptions WHERE id=1')).one()
        assert float(amount) == 19.95 and float(rate) == 0.125
        assert (method, note) == (('manual', 'legacy note') if prior == '0021_dianping_redemption' else ('scan', None))
        assert inspect(conn).has_table('redemption_batches')
    engine.dispose()
