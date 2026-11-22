"""Upgrade legacy package/voucher records without inventing historical terms."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_prelisting_database_keeps_order_voucher_and_nullable_snapshots(tmp_path):
    api = Path(__file__).resolve().parents[1]
    database = tmp_path / 'legacy-package.db'
    env = {**os.environ, 'APP_ENV': 'dev', 'DATABASE_URL': f'sqlite:///{database}'}
    def alembic(*args):
        return subprocess.run([sys.executable, '-m', 'alembic', *args],
                              cwd=api, env=env, text=True, capture_output=True)
    result = alembic('upgrade', '0021_account_deletion')
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as db:
        db.execute('pragma foreign_keys=on')
        db.execute("insert into users (id,email,password_hash,role) values (1,'old@example.invalid','x','vendor')")
        db.execute("insert into shops (id,vendor_id,name) values (1,1,'Old Shop')")
        db.execute("insert into packages (id,shop_id,name,person_count,original_price,package_price,valid_from,valid_to,stock_total,stock_left,refund_policy,status) values (1,1,'Old package',2,200,150,'2026-01-01','2030-01-01',5,4,'anytime','active')")
        db.execute("insert into coupons (id,shop_id,vendor_id,coupon_type,coupon_name,valid_from,valid_to,status,kind,package_id,refund_policy) values (1,1,1,'Price','Old package','2026-01-01','2030-01-01','active','package',1,'anytime')")
        db.execute("insert into orders (id,user_id,order_type,package_id,quantity,amount,payment_status,refund_status,settlement_status) values (1,1,'package',1,1,150,'paid','none','pending')")
        db.execute("insert into wallet_items (id,user_id,coupon_id,status,source,order_id) values (1,1,1,1,'purchase',1)")
    result = alembic('upgrade', 'head')
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as db:
        assert db.execute('select coupon_name,is_package_template from coupons where id=1').fetchone() == ('Old package', None)
        assert db.execute('select amount,package_coupon_id from orders where id=1').fetchone() == (150, None)
        assert db.execute('select coupon_id,order_id from wallet_items where id=1').fetchone() == (1, 1)
        assert db.execute('pragma foreign_key_check').fetchall() == []
    result = alembic('downgrade', '0021_account_deletion')
    assert result.returncode != 0 and 'Cannot safely downgrade' in result.stderr
    with sqlite3.connect(database) as db:
        assert db.execute('select coupon_id,order_id from wallet_items where id=1').fetchone() == (1, 1)
