"""Compatibility checks for the forward-only P1-B bridge."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


MIGRATION = Path(__file__).resolve().parents[1] / 'alembic/versions/0022_p1b_forward_bridge.py'
spec = importlib.util.spec_from_file_location('commission_forward_revision', MIGRATION)
revision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(revision)


def test_bridge_is_idempotent_and_refuses_lossy_downgrade(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        for table in ('redemptions', 'connect_ledger_entries', 'orders', 'wallet_items'):
            connection.execute(text(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY)'))
            connection.execute(text(f'INSERT INTO {table}(id) VALUES (1)'))
        with Operations.context(MigrationContext.configure(connection)):
            revision.upgrade()
            revision.upgrade()
            assert 'commission_rate_snapshot' in {c['name'] for c in inspect(connection).get_columns('redemptions')}
            assert connection.execute(text('SELECT commission_rate_snapshot FROM redemptions WHERE id=1')).scalar_one() is None
            assert connection.execute(text('SELECT dispatch_started FROM connect_ledger_entries WHERE id=1')).scalar_one() is None
            connection.execute(text('UPDATE redemptions SET commission_rate_snapshot=0.10 WHERE id=1'))
            with pytest.raises(RuntimeError, match='Forward-only bridge'):
                revision.downgrade()
            assert connection.execute(text('SELECT count(*) FROM redemptions WHERE commission_rate_snapshot IS NOT NULL')).scalar_one() == 1
    engine.dispose()
