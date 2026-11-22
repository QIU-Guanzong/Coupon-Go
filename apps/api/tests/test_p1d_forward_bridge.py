"""Onboarding and current main upgrade without rewriting historical records."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

API = Path(__file__).resolve().parents[1]
HEAD = '0027_p1d_forward_bridge'


def alembic(url, *args, ok=True):
    env = dict(os.environ, DATABASE_URL=url, APP_ENV='dev', COUPON_GO_ENV='dev', SEED_ON_STARTUP='0')
    result = subprocess.run([sys.executable, '-m', 'alembic', *args], cwd=API,
                            env=env, text=True, capture_output=True)
    assert (result.returncode == 0) == ok, result.stdout + result.stderr
    return result


@pytest.mark.parametrize('prior', ['0022_p1d_merchant_onboarding', '0026_p1c_allocation_bridge'])
def test_both_histories_upgrade_without_rewriting_application(tmp_path, prior):
    url = f"sqlite:///{tmp_path / 'onboarding.db'}"
    alembic(url, 'upgrade', prior)
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users(id,email,password_hash,role) VALUES (1,'legacy@example.invalid','x','customer')"))
        conn.execute(text("INSERT INTO merchant_applications(id,user_id,shop_name,status,business_license,br_number,stripe_account_id,stripe_charges_enabled,stripe_payouts_enabled) VALUES (1,1,'legacy shop','pending','license-record','12345678','acct_existing',1,1)"))
        conn.execute(text("INSERT INTO merchant_files(id,application_id,uploader_id,kind,stored_name,size_bytes) VALUES (1,1,1,'contract_pdf','archived.pdf',123)"))
        version_id = conn.execute(text('SELECT id FROM merchant_contract_versions WHERE is_current')).scalar_one_or_none()
        if version_id is None:
            conn.execute(text("INSERT INTO merchant_contract_versions(id,version,title,body_text,is_current) VALUES (1,'old','old contract','old terms',1)"))
            version_id = 1
        conn.execute(text('INSERT INTO merchant_contract_signatures(id,application_id,user_id,contract_version_id,pdf_file_id) VALUES (1,1,1,:version,1)'), {'version': version_id})
    alembic(url, 'upgrade', 'head')
    with engine.connect() as conn:
        assert list(conn.execute(text('SELECT version_num FROM alembic_version')).scalars()) == [HEAD]
        assert conn.execute(text('SELECT business_license,br_number,stripe_account_id FROM merchant_applications WHERE id=1')).one() == ('license-record', '12345678', 'acct_existing')
        assert conn.execute(text('SELECT pdf_file_id FROM merchant_contract_signatures WHERE id=1')).scalar_one() == 1
        assert conn.execute(text('SELECT stored_name FROM merchant_files WHERE id=1')).scalar_one() == 'archived.pdf'
    result = alembic(url, 'downgrade', prior, ok=False)
    assert 'coordinated restore' in result.stderr
    engine.dispose()


def test_incompatible_onboarding_schema_fails_before_version_advances(tmp_path):
    url = f"sqlite:///{tmp_path / 'invalid.db'}"
    alembic(url, 'upgrade', '0026_p1c_allocation_bridge')
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE merchant_applications DROP COLUMN br_number'))
        conn.execute(text('ALTER TABLE merchant_applications ADD COLUMN br_number INTEGER'))
    result = alembic(url, 'upgrade', 'head', ok=False)
    assert 'Incompatible onboarding column: merchant_applications.br_number' in result.stderr
    with engine.connect() as conn:
        assert HEAD not in set(conn.execute(text('SELECT version_num FROM alembic_version')).scalars())
    engine.dispose()
