"""Upgrade independent paid-coupon/points histories in disposable databases."""
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

API = Path(__file__).resolve().parents[1]
HEAD = '0027_p1d_forward_bridge'


@pytest.fixture(params=['sqlite', 'postgres'])
def database_url(request, tmp_path):
    if request.param == 'sqlite':
        return f"sqlite:///{tmp_path / 'p1c-bridge.db'}"
    raw = os.environ.get('P1C_MIGRATION_PG_URL')
    if not raw:
        pytest.skip('requires isolated PostgreSQL cluster')
    base = make_url(raw)
    assert base.get_backend_name() == 'postgresql'
    assert base.host in (None, '', '127.0.0.1', 'localhost', '::1') and not base.password
    assert (base.database or '').startswith('coupon_p1c_migration_test_')
    name = 'coupon_p1c_migration_test_' + uuid4().hex[:10]
    admin = create_engine(base.set(database='postgres'), isolation_level='AUTOCOMMIT')
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}" ENCODING \'UTF8\''))
    admin.dispose()
    return base.set(database=name).render_as_string(hide_password=False)


def alembic(url, *args, ok=True):
    env = dict(os.environ, DATABASE_URL=url, COUPON_GO_ENV='dev', SEED_ON_STARTUP='0')
    result = subprocess.run([sys.executable, '-m', 'alembic', *args], cwd=API,
                            env=env, text=True, capture_output=True)
    assert (result.returncode == 0) == ok, result.stdout + result.stderr
    return result


@pytest.mark.parametrize('prior', ['0021_p1a_paid_coupons', '0025_p1a_forward_bridge',
                                   '0021_p1c_points_expiry', '0022_p1c_expiry_indexes'])
def test_old_heads_upgrade_preserving_ledger_and_adding_missing_columns(database_url, prior):
    alembic(database_url, 'upgrade', prior)
    engine = create_engine(database_url)
    with engine.begin() as conn:
        for table, name in [('wallet_items', 'settlement_allocated_amount'),
                            ('wallet_items', 'refund_hold_previous_status'),
                            ('orders', 'wallet_allocation_fingerprint')]:
            if name in {c['name'] for c in inspect(conn).get_columns(table)}:
                conn.execute(text(f'ALTER TABLE {table} DROP COLUMN {name}'))
        if prior in ('0021_p1a_paid_coupons', '0025_p1a_forward_bridge'):
            for index in inspect(conn).get_indexes('point_transactions'):
                if 'expires_at' in index['column_names']:
                    conn.execute(text(f'DROP INDEX {index["name"]}'))
            if 'expires_at' in {c['name'] for c in inspect(conn).get_columns('point_transactions')}:
                conn.execute(text('ALTER TABLE point_transactions DROP COLUMN expires_at'))
        if prior != '0022_p1c_expiry_indexes':
            if inspect(conn).has_table('point_expiry_finalizations'):
                conn.execute(text('DROP TABLE point_expiry_finalizations'))
            for index in inspect(conn).get_indexes('point_transactions'):
                if index['name'] in ('ix_point_tx_expiry_user', 'ix_point_tx_user_reason_ref'):
                    conn.execute(text(f'DROP INDEX {index["name"]}'))
        conn.execute(text("INSERT INTO users(id,email,password_hash,role) VALUES (1,'legacy@example.com','x','customer')"))
        conn.execute(text("INSERT INTO orders(id,user_id,order_type,quantity,amount,paid_amount,payment_status,refund_status,settlement_status) VALUES (1,1,'package',1,19.95,19.95,'paid','none','pending')"))
        # Text insert is portable for the historical datetime column.
        if prior in ('0021_p1c_points_expiry', '0022_p1c_expiry_indexes'):
            conn.execute(text("INSERT INTO point_transactions(user_id,delta,reason,created_at,expires_at) VALUES (1,10,'claim','2026-01-01 00:00:00','2027-01-01 00:00:00')"))
        else:
            conn.execute(text("INSERT INTO point_transactions(user_id,delta,reason,created_at) VALUES (1,10,'claim','2026-01-01 00:00:00')"))
    alembic(database_url, 'upgrade', 'head')
    with engine.connect() as conn:
        assert set(conn.execute(text('SELECT version_num FROM alembic_version')).scalars()) == {HEAD}
        assert float(conn.execute(text('SELECT paid_amount FROM orders WHERE id=1')).scalar_one()) == 19.95
        assert conn.execute(text('SELECT delta FROM point_transactions WHERE user_id=1')).scalar_one() == 10
        assert conn.execute(text('SELECT expires_at FROM point_transactions WHERE user_id=1')).scalar_one() is not None
        assert conn.execute(text('SELECT wallet_allocation_fingerprint FROM orders WHERE id=1')).scalar_one() is None
        assert {'settlement_allocated_amount', 'refund_hold_previous_status'} <= {c['name'] for c in inspect(conn).get_columns('wallet_items')}
        assert inspect(conn).has_table('point_expiry_finalizations')
    engine.dispose()


def test_existing_allocation_values_are_preserved(database_url):
    alembic(database_url, 'upgrade', '0025_p1a_forward_bridge')
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users(id,email,password_hash,role) VALUES (1,'legacy@example.com','x','customer')"))
        conn.execute(text("INSERT INTO shops(id,vendor_id,name) VALUES (1,1,'shop')"))
        conn.execute(text("INSERT INTO coupons(id,shop_id,vendor_id,coupon_type,coupon_name,valid_from,valid_to,total,remaining,status,kind,refund_policy) VALUES (1,1,1,'Price','coupon','2026-01-01','2027-01-01',5,4,'active','coupon','anytime')"))
        conn.execute(text("INSERT INTO orders(id,user_id,order_type,coupon_id,quantity,amount,paid_amount,payment_status,refund_status,settlement_status,wallet_allocation_fingerprint) VALUES (1,1,'coupon',1,1,19.95,19.95,'paid','none','pending','legacy-fingerprint')"))
        conn.execute(text("INSERT INTO wallet_items(id,user_id,coupon_id,order_id,status,source,settlement_allocated_amount,refund_hold_previous_status) VALUES (1,1,1,1,3,'purchase',19.95,1)"))
    alembic(database_url, 'upgrade', 'head')
    with engine.connect() as conn:
        assert conn.execute(text('SELECT wallet_allocation_fingerprint FROM orders WHERE id=1')).scalar_one() == 'legacy-fingerprint'
        amount, status = conn.execute(text('SELECT settlement_allocated_amount,refund_hold_previous_status FROM wallet_items WHERE id=1')).one()
        assert float(amount) == 19.95 and status == 1
    engine.dispose()


def test_incompatible_existing_allocation_fails_without_overwriting(database_url):
    alembic(database_url, 'upgrade', '0025_p1a_forward_bridge')
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE wallet_items DROP COLUMN settlement_allocated_amount'))
        conn.execute(text('ALTER TABLE wallet_items ADD COLUMN settlement_allocated_amount VARCHAR(10)'))
    result = alembic(database_url, 'upgrade', 'head', ok=False)
    assert 'Incompatible existing column wallet_items.settlement_allocated_amount' in result.stderr
    engine.dispose()
