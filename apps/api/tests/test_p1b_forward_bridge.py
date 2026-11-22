"""Real Alembic upgrades on disposable SQLite and opt-in local PostgreSQL.

The optional PostgreSQL URL must point at a task-owned local cluster. Each
case creates a uniquely named database there; never use a customer database.
"""
import os
from datetime import datetime
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.engine import make_url


API = Path(__file__).resolve().parents[1]
BRIDGE = '0022_p1b_forward_bridge'
HEAD = '0030_redemption_batch_bridge'
OLD = '0021_p1b_stripe_connect'
LEGAL = '0020_m92_legal_consents'


@pytest.fixture(params=['sqlite', 'postgres'])
def database_url(request, tmp_path):
    if request.param == 'sqlite':
        return f"sqlite:///{tmp_path / 'bridge.db'}"
    raw = os.environ.get('MIGRATION_BRIDGE_PG_URL')
    if not raw:
        pytest.skip('Set MIGRATION_BRIDGE_PG_URL to an isolated local test cluster')
    base = make_url(raw)
    assert base.get_backend_name() == 'postgresql'
    assert base.host in (None, '', '127.0.0.1', 'localhost', '::1')
    assert not base.password, 'Use an isolated local cluster without stored credentials'
    assert (base.database or '').startswith('coupon_migration_test_')
    name = 'coupon_migration_test_' + uuid4().hex[:12]
    admin = create_engine(base.set(database='postgres'), isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}" ENCODING \'UTF8\''))
    admin.dispose()
    # The caller owns and destroys the entire disposable cluster after tests.
    return base.set(database=name).render_as_string(hide_password=False)


def _alembic(url, *arguments, ok=True):
    env = dict(os.environ, DATABASE_URL=url, APP_ENV='dev',
               SEED_ON_STARTUP='0', SCHEDULER_ENABLED='0')
    result = subprocess.run([sys.executable, '-m', 'alembic', *arguments],
                            cwd=API, env=env, text=True, capture_output=True)
    if ok:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0, result.stdout + result.stderr
    return result


def _versions(connection):
    return set(connection.execute(text('SELECT version_num FROM alembic_version')).scalars())


def _columns(connection, table):
    return {column['name']: column for column in inspect(connection).get_columns(table)}


def _insert_sample(connection):
    metadata = MetaData()
    def insert(table, **values):
        reflected = Table(table, metadata, autoload_with=connection)
        if table == 'connect_ledger_entries':
            for column, value in [('retry_count', 0), ('manual_notified', False)]:
                if column in reflected.c:
                    values[column] = value
        connection.execute(reflected.insert().values(**values))
    insert('users', id=1, email='migration-fixture@example.invalid', password_hash='fixture', role='vendor')
    insert('shops', id=1, vendor_id=1, name='fixture')
    insert('coupons', id=1, shop_id=1, vendor_id=1, coupon_type='Price',
           coupon_name='fixture', valid_from=datetime(2026, 1, 1),
           valid_to=datetime(2027, 1, 1), status='active', kind='coupon',
           refund_policy='anytime')
    insert('orders', id=1, user_id=1, order_type='package', quantity=1,
           amount=100, paid_amount=100, payment_status='paid',
           refund_status='none', settlement_status='pending')
    insert('wallet_items', id=1, user_id=1, coupon_id=1, status=1,
           source='purchase', order_id=1)
    insert('redemptions', id=1, wallet_item_id=1, coupon_id=1, shop_id=1,
           scanned_by_vendor_id=1, face_value=100)
    insert('connect_ledger_entries', id=1, shop_id=1, redemption_id=1,
           kind='transfer', status='pending', gross_amount=100,
           commission=10, net_amount=90, pending_reversal=False)


def _remove_model_added_columns(connection):
    # Revision 0001 builds from current model metadata. Reconstruct the actual
    # original 0021 schema instead of accidentally testing current columns.
    for table, names in {
        'redemptions': ['commission_rate_snapshot'],
        'connect_ledger_entries': ['dispatch_started', 'retry_count', 'manual_notified'],
        'wallet_items': ['settlement_allocated_amount', 'refund_hold_previous_status'],
        'orders': ['wallet_allocation_fingerprint'],
    }.items():
        for name in names:
            if name in _columns(connection, table):
                connection.execute(text(f'ALTER TABLE {table} DROP COLUMN {name}'))


def test_fresh_upgrade_has_one_head_and_nullable_fields(database_url):
    _alembic(database_url, 'upgrade', 'head')
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert _versions(connection) == {HEAD}
        expected = {
            'redemptions': ['commission_rate_snapshot'],
            'connect_ledger_entries': ['dispatch_started', 'retry_count', 'manual_notified'],
            'wallet_items': ['settlement_allocated_amount', 'refund_hold_previous_status'],
            'orders': ['wallet_allocation_fingerprint'],
        }
        for table, names in expected.items():
            actual = _columns(connection, table)
            assert all(name in actual for name in names)
        assert _columns(connection, 'connect_ledger_entries')['dispatch_started']['nullable']
        assert _columns(connection, 'wallet_items')['refund_hold_previous_status']['nullable']
    engine.dispose()


@pytest.mark.parametrize('prior', ['old_0021_only', 'both_old_heads', 'legal_0020_only'])
def test_upgrade_known_existing_heads_preserves_data_and_runs_missing_parent(database_url, prior):
    if prior == 'legal_0020_only':
        _alembic(database_url, 'upgrade', LEGAL)
    else:
        _alembic(database_url, 'upgrade', OLD)
        if prior == 'both_old_heads':
            _alembic(database_url, 'upgrade', LEGAL)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        before = _versions(connection)
        assert before == ({OLD} if prior == 'old_0021_only' else
                          {OLD, LEGAL} if prior == 'both_old_heads' else {LEGAL})
        if prior != 'legal_0020_only':
            # 0001 imports current ORM metadata, so model-added columns can
            # appear even in an ostensibly historical migration. Remove the
            # new column to accurately represent a genuinely old 0021 schema.
            _remove_model_added_columns(connection)
            if prior == 'old_0021_only' and inspect(connection).has_table('legal_acceptances'):
                connection.execute(text('DROP TABLE legal_acceptances'))
            _insert_sample(connection)
    result = _alembic(database_url, 'upgrade', 'head')
    if prior == 'old_0021_only':
        assert 'Running upgrade 0019_invoice_credits -> 0020_m92_legal_consents' in result.stderr
    with engine.connect() as connection:
        assert _versions(connection) == {HEAD}
        assert inspect(connection).has_table('legal_acceptances')
        if prior != 'legal_0020_only':
            assert connection.execute(text('SELECT face_value FROM redemptions WHERE id=1')).scalar_one() == 100
            assert connection.execute(text('SELECT commission_rate_snapshot FROM redemptions WHERE id=1')).scalar_one() is None
            row = connection.execute(text('SELECT gross_amount, commission, net_amount, dispatch_started, retry_count, manual_notified FROM connect_ledger_entries WHERE id=1')).one()
            assert row[:4] == (100, 10, 90, None)
            assert row[4] == 0 and not row[5]
            assert connection.execute(text('SELECT paid_amount FROM orders WHERE id=1')).scalar_one() == 100
            assert connection.execute(text('SELECT wallet_allocation_fingerprint FROM orders WHERE id=1')).scalar_one() is None
            assert connection.execute(text('SELECT settlement_allocated_amount FROM wallet_items WHERE id=1')).scalar_one() is None
            assert connection.execute(text('SELECT refund_hold_previous_status FROM wallet_items WHERE id=1')).scalar_one() is None
    engine.dispose()


def test_existing_incompatible_column_fails_before_any_bridge_ddl(database_url):
    _alembic(database_url, 'upgrade', OLD)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        _remove_model_added_columns(connection)
        connection.execute(text('ALTER TABLE wallet_items ADD COLUMN settlement_allocated_amount VARCHAR(10)'))
    result = _alembic(database_url, 'upgrade', 'head', ok=False)
    assert 'Incompatible existing column wallet_items.settlement_allocated_amount' in result.stderr
    with engine.connect() as connection:
        assert BRIDGE not in _versions(connection)
        assert 'commission_rate_snapshot' not in _columns(connection, 'redemptions')
        assert 'dispatch_started' not in _columns(connection, 'connect_ledger_entries')
    engine.dispose()


def test_existing_compatible_columns_and_values_are_preserved(database_url):
    _alembic(database_url, 'upgrade', OLD)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        _remove_model_added_columns(connection)
        _insert_sample(connection)
        for ddl in [
            'ALTER TABLE redemptions ADD COLUMN commission_rate_snapshot NUMERIC(5,4)',
            'ALTER TABLE connect_ledger_entries ADD COLUMN dispatch_started BOOLEAN',
            'ALTER TABLE connect_ledger_entries ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE connect_ledger_entries ADD COLUMN manual_notified BOOLEAN NOT NULL DEFAULT FALSE',
            'ALTER TABLE wallet_items ADD COLUMN settlement_allocated_amount NUMERIC(12,2)',
            'ALTER TABLE wallet_items ADD COLUMN refund_hold_previous_status INTEGER',
            'ALTER TABLE orders ADD COLUMN wallet_allocation_fingerprint VARCHAR(64)',
            "UPDATE redemptions SET commission_rate_snapshot=0.25 WHERE id=1",
            "UPDATE connect_ledger_entries SET dispatch_started=true, retry_count=7, manual_notified=true WHERE id=1",
            "UPDATE wallet_items SET settlement_allocated_amount=99.5, refund_hold_previous_status=3 WHERE id=1",
            "UPDATE orders SET wallet_allocation_fingerprint='old-fingerprint' WHERE id=1",
        ]:
            connection.execute(text(ddl))
    _alembic(database_url, 'upgrade', 'head')
    with engine.connect() as connection:
        assert float(connection.execute(text('SELECT commission_rate_snapshot FROM redemptions WHERE id=1')).scalar_one()) == 0.25
        row = connection.execute(text('SELECT dispatch_started, retry_count, manual_notified FROM connect_ledger_entries WHERE id=1')).one()
        assert row == (True, 7, True)
        row = connection.execute(text('SELECT settlement_allocated_amount, refund_hold_previous_status FROM wallet_items WHERE id=1')).one()
        assert float(row[0]) == 99.5 and row[1] == 3
        assert connection.execute(text('SELECT wallet_allocation_fingerprint FROM orders WHERE id=1')).scalar_one() == 'old-fingerprint'
    engine.dispose()


def test_rewriting_applied_ancestry_silently_skips_legal_migration(database_url, tmp_path):
    """Evidence why old 0021 cannot be changed to descend from 0020."""
    _alembic(database_url, 'upgrade', OLD)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        if inspect(connection).has_table('legal_acceptances'):
            connection.execute(text('DROP TABLE legal_acceptances'))
        assert _versions(connection) == {OLD}
    scripts = tmp_path / 'rewritten-history'
    shutil.copytree(API / 'alembic', scripts, ignore=shutil.ignore_patterns('__pycache__'))
    old_file = scripts / 'versions' / '0021_p1b_stripe_connect.py'
    old_text = old_file.read_text()
    old_file.write_text(old_text.replace('down_revision = "0019_invoice_credits"',
                                         'down_revision = "0020_m92_legal_consents"'))
    bridge_file = scripts / 'versions' / '0022_p1b_forward_bridge.py'
    bridge_text = bridge_file.read_text()
    bridge_file.write_text(bridge_text.replace(
        'down_revision = ("0020_m92_legal_consents", "0021_p1b_stripe_connect")',
        'down_revision = "0021_p1b_stripe_connect"'))
    config = tmp_path / 'alembic.ini'
    config.write_text((API / 'alembic.ini').read_text().replace(
        'script_location = alembic', f'script_location = {scripts}'))
    env = dict(os.environ, DATABASE_URL=database_url, APP_ENV='dev',
               SEED_ON_STARTUP='0', SCHEDULER_ENABLED='0')
    result = subprocess.run([sys.executable, '-m', 'alembic', '-c', str(config),
                             'upgrade', 'head'], cwd=API, env=env,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Running upgrade 0019_invoice_credits -> 0020_m92_legal_consents' not in result.stderr
    with engine.connect() as connection:
        assert _versions(connection) == {HEAD}
        assert not inspect(connection).has_table('legal_acceptances')
    engine.dispose()


def test_downgrade_refuses_to_drop_existing_data(database_url):
    _alembic(database_url, 'upgrade', 'head')
    engine = create_engine(database_url)
    with engine.begin() as connection:
        _insert_sample(connection)
        connection.execute(text('UPDATE redemptions SET commission_rate_snapshot = 0.125 WHERE id=1'))
        connection.execute(text('UPDATE wallet_items SET settlement_allocated_amount = 100, refund_hold_previous_status = 3 WHERE id=1'))
    result = _alembic(database_url, 'downgrade', LEGAL, ok=False)
    assert 'coordinated restore' in result.stderr
    with engine.connect() as connection:
        assert _versions(connection) == {HEAD}
        assert float(connection.execute(text('SELECT commission_rate_snapshot FROM redemptions WHERE id=1')).scalar_one()) == 0.125
        assert connection.execute(text('SELECT settlement_allocated_amount FROM wallet_items WHERE id=1')).scalar_one() == 100
        assert connection.execute(text('SELECT refund_hold_previous_status FROM wallet_items WHERE id=1')).scalar_one() == 3
    engine.dispose()


def test_original_0021_retry_fields_survive_forward_upgrade(database_url):
    """The published 0021 revision already included both retry fields."""
    _alembic(database_url, 'upgrade', OLD)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        columns = _columns(connection, 'connect_ledger_entries')
        assert {'retry_count', 'manual_notified'} <= set(columns)
        _insert_sample(connection)
        connection.execute(text(
            'UPDATE connect_ledger_entries SET retry_count=7, manual_notified=true WHERE id=1'))
    _alembic(database_url, 'upgrade', 'head')
    with engine.connect() as connection:
        row = connection.execute(text(
            'SELECT retry_count, manual_notified FROM connect_ledger_entries WHERE id=1')).one()
        assert row == (7, True)
    engine.dispose()
