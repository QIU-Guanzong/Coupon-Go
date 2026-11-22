"""Upgrade real pre-Connect schemas without erasing applications or ledgers."""
import pytest
from sqlalchemy import create_engine, inspect, text

from tests.test_p1c_allocation_bridge import database_url, alembic


@pytest.mark.parametrize("prior", ["0027_p1d_forward_bridge", "0023_p1b_payout_recovery"])
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
        if "approved_shop_id" in columns:
            conn.execute(text("ALTER TABLE merchant_applications DROP COLUMN approved_shop_id"))
        conn.execute(text("INSERT INTO users(id,email,password_hash,role) VALUES (1,'legacy-connect@example.com','x','customer')"))
        conn.execute(text("INSERT INTO merchant_applications(id,user_id,shop_name,status,stripe_charges_enabled,stripe_payouts_enabled) VALUES (1,1,'existing','pending',false,false)"))
    alembic(database_url, "upgrade", "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0028_application_connect_bridge"
        assert conn.execute(text("SELECT shop_name, approved_shop_id FROM merchant_applications WHERE id=1")).one() == ("existing", None)
        assert inspect(conn).has_table("application_connect_accounts")
        assert inspect(conn).has_table("shop_connect_operations")
    engine.dispose()
