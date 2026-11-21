"""Upgrade and rollback checks against a disposable database only."""
import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

MIGRATION = Path(__file__).resolve().parents[1] / 'alembic/versions/0021_p1b_stripe_connect.py'
spec = importlib.util.spec_from_file_location('commission_revision', MIGRATION)
revision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(revision)


def test_legacy_upgrade_preserves_null_and_prevents_lossy_downgrade():
    pg = os.environ.get('COMMISSION_TEST_PG_URL')
    if pg:
        assert (make_url(pg).database or '').startswith('coupon_commission_test_')
    with TemporaryDirectory(prefix='commission-migration-') as directory:
        engine = create_engine(pg or f"sqlite:///{Path(directory) / 'migration.db'}")
        with engine.begin() as connection:
            for table in ('users', 'shops', 'redemptions'):
                connection.execute(text(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY)'))
            connection.execute(text('INSERT INTO redemptions(id) VALUES (1)'))
            with Operations.context(MigrationContext.configure(connection)):
                revision.upgrade()
                revision.upgrade()  # safe replay in a partially upgraded environment
                assert 'commission_rate_snapshot' in {c['name'] for c in inspect(connection).get_columns('redemptions')}
                assert connection.execute(text('SELECT commission_rate_snapshot FROM redemptions WHERE id=1')).scalar_one() is None
                connection.execute(text('UPDATE redemptions SET commission_rate_snapshot=0.10 WHERE id=1'))
                with pytest.raises(RuntimeError, match='snapshots require'):
                    revision.downgrade()
                assert connection.execute(text('SELECT count(*) FROM redemptions WHERE commission_rate_snapshot IS NOT NULL')).scalar_one() == 1
                connection.execute(text('UPDATE redemptions SET commission_rate_snapshot=NULL WHERE id=1'))
                revision.downgrade()
                assert 'commission_rate_snapshot' not in {c['name'] for c in inspect(connection).get_columns('redemptions')}
            for table in ('redemptions', 'shops', 'users'):
                connection.execute(text(f'DROP TABLE {table}'))
        engine.dispose()
