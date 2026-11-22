"""Upgrade real pre-Connect schemas without erasing applications or ledgers."""
import pytest
from sqlalchemy import create_engine, inspect, text

from tests.test_p1c_allocation_bridge import database_url, alembic


@pytest.mark.parametrize("prior", ["0027_p1d_forward_bridge", "0028_p1d_approval_shop", "0023_p1b_payout_recovery"])
def test_old_schema_gains_application_connect_tables_preserving_records(database_url, prior):
    alembic(database_url, "upgrade", prior)
    engine = create_engine(database_url)
    with engine.begin() as conn:
        # Revision 0001 uses current model metadata: remove additions to
        # reproduce the schema of installations before this bridge.
        for table in ("application_connect_accounts", "shop_connect_operations"):
            if inspect(conn).has_table(table):
                conn.execute(text(f"DROP TABLE {table}"))
        columns = {c["name"] for c in inspect(conn).get_columns("merchant_applications")}
        if "approved_shop_id" in columns and prior != "0028_p1d_approval_shop":
            conn.execute(text("ALTER TABLE merchant_applications DROP COLUMN approved_shop_id"))
        account_columns = {c["name"] for c in inspect(conn).get_columns("connect_accounts")}
        if "transfers_enabled" in account_columns:
            conn.execute(text("ALTER TABLE connect_accounts DROP COLUMN transfers_enabled"))
        ledger_columns = {c["name"] for c in inspect(conn).get_columns("connect_ledger_entries")}
        if "transfer_account_id" in ledger_columns:
            conn.execute(text("ALTER TABLE connect_ledger_entries DROP COLUMN transfer_account_id"))
        conn.execute(text("INSERT INTO users(id,email,password_hash,role) VALUES (1,'legacy-connect@example.com','x','customer')"))
        conn.execute(text("INSERT INTO merchant_applications(id,user_id,shop_name,status,stripe_charges_enabled,stripe_payouts_enabled) VALUES (1,1,'existing','pending',false,false)"))
        conn.execute(text("INSERT INTO shops(id,vendor_id,name) VALUES (1,1,'existing')"))
        if prior == "0028_p1d_approval_shop":
            conn.execute(text("UPDATE merchant_applications SET approved_shop_id=1 WHERE id=1"))
        conn.execute(text("INSERT INTO connect_accounts(id,shop_id,stripe_account_id,charges_enabled,payouts_enabled,details_submitted,onboarding_status,manual_payouts) VALUES (1,1,'acct_legacy',true,true,true,'active',true)"))
        conn.execute(text("INSERT INTO connect_ledger_entries(id,shop_id,kind,status,gross_amount,commission,net_amount,pending_reversal,retry_count,manual_notified) VALUES (1,1,'transfer','transfer_unknown',100,10,90,false,1,true)"))
    alembic(database_url, "upgrade", "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0031_dianping_mgmt_bridge"
        expected_shop = 1 if prior == "0028_p1d_approval_shop" else None
        assert conn.execute(text("SELECT shop_name, approved_shop_id FROM merchant_applications WHERE id=1")).one() == ("existing", expected_shop)
        assert inspect(conn).has_table("application_connect_accounts")
        assert inspect(conn).has_table("shop_connect_operations")
        assert conn.execute(text("SELECT stripe_account_id,transfers_enabled FROM connect_accounts WHERE id=1")).one() == ("acct_legacy", None)
        assert conn.execute(text("SELECT status,net_amount,transfer_account_id FROM connect_ledger_entries WHERE id=1")).one() == ("transfer_unknown", 90, None)
    engine.dispose()
