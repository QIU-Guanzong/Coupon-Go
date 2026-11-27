"""Both historical P1-A and listing databases upgrade without losing orders."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from tests.migration_contract import EXPECTED_MIGRATION_HEAD


@pytest.mark.parametrize('starting_revision', ['0021_p1a_paid_coupons', '0024_listing_template_versions'])
def test_old_database_upgrades_to_single_paid_coupon_head(tmp_path, starting_revision):
    api = Path(__file__).resolve().parents[1]
    database = tmp_path / 'legacy.db'
    env = {**os.environ, 'COUPON_GO_ENV': 'dev', 'DATABASE_URL': f'sqlite:///{database}'}
    def alembic(*args):
        return subprocess.run([sys.executable, '-m', 'alembic', *args], cwd=api,
                              env=env, text=True, capture_output=True)
    result = alembic('upgrade', starting_revision)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO users (id,email,password_hash,role) VALUES (1,'legacy@example.invalid','none','customer')")
        db.execute("INSERT INTO shops (id,vendor_id,name) VALUES (1,1,'legacy shop')")
        db.execute("INSERT INTO coupons (id,shop_id,vendor_id,coupon_type,coupon_name,sale_price,valid_from,valid_to,total,remaining,status,kind,refund_policy) VALUES (1,1,1,'Price','legacy coupon',19.95,'2026-01-01','2027-01-01',10,7,'active','coupon','anytime')")
        if starting_revision == '0021_p1a_paid_coupons':
            db.execute("INSERT INTO orders (id,user_id,order_type,coupon_id,quantity,amount,paid_amount,payment_status,refund_status,settlement_status) VALUES (1,1,'coupon',1,1,19.95,19.95,'paid','none','pending')")
        else:
            db.execute("INSERT INTO orders (id,user_id,order_type,quantity,amount,paid_amount,payment_status,refund_status,settlement_status) VALUES (1,1,'package',1,19.95,19.95,'paid','none','pending')")
    result = alembic('upgrade', 'head')
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT version_num FROM alembic_version').fetchall() == [(EXPECTED_MIGRATION_HEAD,)]
        assert db.execute('SELECT sale_price,remaining FROM coupons WHERE id=1').fetchone() == (19.95, 7)
        order = db.execute('SELECT order_type,coupon_id,amount,paid_amount FROM orders WHERE id=1').fetchone()
        assert order == (('coupon', 1, 19.95, 19.95) if starting_revision == '0021_p1a_paid_coupons'
                         else ('package', None, 19.95, 19.95))
    result = alembic('downgrade', '0024_listing_template_versions')
    assert result.returncode != 0 and 'coordinated restore' in result.stderr
