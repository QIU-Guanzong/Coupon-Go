"""Upgrade pre-payout-recovery ledgers while retaining uncertain history."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


@pytest.mark.parametrize('incompatible', [False, True])
def test_existing_connect_ledger_is_preserved_or_rejected_before_changes(tmp_path, incompatible):
    api = Path(__file__).resolve().parents[1]
    path = tmp_path / 'legacy.db'
    env = {**os.environ, 'COUPON_GO_ENV': 'dev', 'DATABASE_URL': f'sqlite:///{path}'}
    def alembic(*args):
        return subprocess.run([sys.executable, '-m', 'alembic', *args], cwd=api,
                              env=env, text=True, capture_output=True)
    result = alembic('upgrade', '0022_p1b_forward_bridge')
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as db:
        # 0001 imports today's ORM, so replace only this synthetic table
        # with the earlier Connect layout rather than masking missing columns.
        db.execute('DROP TABLE connect_ledger_entries')
        extra = ', payout_request_id INTEGER' if incompatible else ''
        db.execute('CREATE TABLE connect_ledger_entries (id INTEGER PRIMARY KEY, shop_id INTEGER NOT NULL, '
                   'redemption_id INTEGER, kind VARCHAR(20) NOT NULL, status VARCHAR(20) NOT NULL, '
                   'net_amount NUMERIC(12,2) NOT NULL, note VARCHAR(255)' + extra + ')')
        db.execute("INSERT INTO connect_ledger_entries (id,shop_id,kind,status,net_amount,note) VALUES (1,1,'payout','processing',-60,'legacy')")
    result = alembic('upgrade', 'head')
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT id,net_amount,status,note FROM connect_ledger_entries').fetchone() == (1,-60,'processing','legacy')
        columns = {row[1] for row in db.execute('PRAGMA table_info(connect_ledger_entries)')}
        if incompatible:
            assert result.returncode != 0 and 'Incompatible Connect ledger column' in result.stderr
            assert 'payout_operation_id' not in columns
        else:
            assert result.returncode == 0, result.stderr
            assert {'payout_request_id', 'payout_operation_id', 'payout_account_id', 'payout_started_at', 'payout_last_polled_at'} <= columns
            assert db.execute('SELECT payout_request_id,payout_operation_id FROM connect_ledger_entries').fetchone() == (None,None)
    if not incompatible:
        result = alembic('downgrade', '0022_p1b_forward_bridge')
        assert result.returncode != 0 and 'coordinated restore' in result.stderr
