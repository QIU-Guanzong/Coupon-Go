"""Test only in explicitly designated disposable local Postgres database."""
import importlib.util
import os
from pathlib import Path
from urllib.parse import urlparse
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

URL = os.environ.get('P1B_MIGRATION_POSTGRES_URL', '')
p = urlparse(URL.replace('postgresql+psycopg2://', 'postgresql://', 1))
if p.hostname not in ('localhost', '127.0.0.1') or not p.path.startswith('/coupon_pr16_migration'):
    pytest.skip('requires dedicated coupon_pr16_migration database', allow_module_level=True)


def test_connect_migration_has_recovery_identity_and_unique_request():
    engine = create_engine(URL)
    script = Path(__file__).resolve().parents[1] / 'alembic/versions/0021_p1b_stripe_connect.py'
    spec = importlib.util.spec_from_file_location('pr16_migration', script)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE IF NOT EXISTS shops (id INTEGER PRIMARY KEY)')
        conn.exec_driver_sql('CREATE TABLE IF NOT EXISTS redemptions (id INTEGER PRIMARY KEY)')
        conn.exec_driver_sql('CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY)')
        conn.exec_driver_sql('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY)')
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
        cols = {c['name'] for c in inspect(conn).get_columns('connect_ledger_entries')}
        assert {'operation_id', 'order_id', 'payment_intent_id', 'stripe_charge_id', 'client_request_id', 'destination_account_id', 'first_attempt_at',
                'last_attempt_at', 'last_polled_at', 'attempt_count'} <= cols
        constraints = inspect(conn).get_unique_constraints('connect_ledger_entries')
        assert any(set(c['column_names']) == {'shop_id', 'kind', 'client_request_id'} for c in constraints)
    engine.dispose()
