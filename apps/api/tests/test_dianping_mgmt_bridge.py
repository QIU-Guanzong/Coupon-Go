"""Forward upgrades from historical management and current Connect heads."""
import pytest
from sqlalchemy import create_engine, inspect, text

from tests.test_p1c_allocation_bridge import alembic, database_url


@pytest.mark.parametrize("prior", ["0023_dianping_merchant_mgmt", "0029_application_connect_bridge"])
def test_forward_bridge_preserves_existing_shop_and_branch_links(database_url, prior):
    alembic(database_url, "upgrade", prior)
    engine = create_engine(database_url)
    with engine.begin() as conn:
        if prior == "0029_application_connect_bridge":
            # 0001 uses current metadata: reconstruct the actual prior schema.
            if inspect(conn).has_table("coupon_branches"):
                conn.execute(text("DROP TABLE coupon_branches"))
            for name in ("business_hours", "gallery", "announcement"):
                if name in {c["name"] for c in inspect(conn).get_columns("shops")}:
                    conn.execute(text(f"ALTER TABLE shops DROP COLUMN {name}"))
        conn.execute(text("INSERT INTO users(id,email,password_hash,role) VALUES (1,'old-merchant@example.com','x','vendor')"))
        conn.execute(text("INSERT INTO shops(id,vendor_id,name) VALUES (1,1,'existing shop')"))
        conn.execute(text("INSERT INTO branches(id,shop_id,name) VALUES (1,1,'existing branch')"))
        conn.execute(text("INSERT INTO coupons(id,shop_id,vendor_id,coupon_type,coupon_name,valid_from,valid_to,status,kind,refund_policy) VALUES (1,1,1,'Discount','old coupon','2026-01-01','2028-01-01','active','coupon','anytime')"))
        if prior == "0023_dianping_merchant_mgmt":
            conn.execute(text("UPDATE shops SET business_hours='09:00-18:00',gallery='[]',announcement='old notice' WHERE id=1"))
            conn.execute(text("INSERT INTO coupon_branches(id,coupon_id,branch_id,created_at) VALUES (1,1,1,'2026-01-01')"))
    alembic(database_url, "upgrade", "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0030_dianping_mgmt_bridge"
        assert conn.execute(text("SELECT name FROM shops WHERE id=1")).scalar_one() == "existing shop"
        if prior == "0023_dianping_merchant_mgmt":
            assert conn.execute(text("SELECT business_hours,announcement FROM shops WHERE id=1")).one() == ("09:00-18:00", "old notice")
            assert conn.execute(text("SELECT coupon_id,branch_id FROM coupon_branches WHERE id=1")).one() == (1, 1)
        else:
            assert conn.execute(text("SELECT count(*) FROM coupon_branches")).scalar_one() == 0
    engine.dispose()
