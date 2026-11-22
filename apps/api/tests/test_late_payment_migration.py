"""Migrate an actual pre-feature SQLite database without rewriting history."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_legacy_database_gains_compensation_table_and_keeps_records(tmp_path):
    api = Path(__file__).resolve().parents[1]
    path = tmp_path / 'legacy.db'
    env = {**os.environ, 'COUPON_GO_ENV': 'dev', 'DATABASE_URL': f'sqlite:///{path}'}
    def alembic(*args):
        return subprocess.run([sys.executable, '-m', 'alembic', *args], cwd=api,
                              env=env, text=True, capture_output=True)
    assert alembic('upgrade', '0021_account_deletion').returncode == 0
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE IF EXISTS late_payment_refunds')
        db.execute("INSERT INTO users (email,password_hash,role) VALUES ('legacy@example.invalid','none','customer')")
    result = alembic('upgrade', 'head')
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT version_num FROM alembic_version').fetchone()[0] == '0022_late_payment_refunds'
        assert db.execute("SELECT count(*) FROM users WHERE email='legacy@example.invalid'").fetchone()[0] == 1
        db.execute("INSERT INTO late_payment_refunds (payment_id,order_id,user_id,amount,currency,request_key,status,created_at,updated_at) VALUES (1,1,1,150,'HKD','retained','pending',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
    result = alembic('downgrade', '0021_account_deletion')
    assert result.returncode != 0 and 'Existing payment compensation records' in result.stderr
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM late_payment_refunds').fetchone()[0] == 1
