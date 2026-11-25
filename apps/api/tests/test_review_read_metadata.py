"""R4: annotate only scoped page rows, using the reviewed redemption."""
from contextlib import contextmanager
from datetime import datetime

from app import models
from app.database import get_db
from app.main import app
from tests.test_merchant_reviews_read import CUSTOMER, OWNER, ROOT, _headers, workspace


EARLY = datetime(2026, 11, 11, 16, 30)
LATE = datetime(2026, 11, 24, 10, 30)


@contextmanager
def _database():
    # Access the isolated fixture's SQLite session, never the real dependency.
    iterator = app.dependency_overrides[get_db]()
    try:
        yield next(iterator)
    finally:
        iterator.close()


def _public(client, shop=11, **params):
    result = client.get(f"/api/v3/shops/{shop}/reviews", params=params)
    assert result.status_code == 200, result.text
    return {r["id"]: r for r in result.json()["reviews"]}


def _merchant(client, actor=OWNER, shop=11, **params):
    result = client.get(ROOT, headers=_headers(actor),
                        params={"shop_id": shop, **params})
    assert result.status_code == 200, result.text
    return {r["id"]: r for r in result.json()["reviews"]}


def test_public_and_merchant_use_review_exact_redemption_not_replayed_wallet(workspace):
    c, _ = workspace
    with _database() as db:
        target = db.get(models.Redemption, 101)
        target.scanned_at = EARLY
        # Later redemption reused the wallet item, even though the review
        # explicitly remains attached to the first redemption.
        db.add(models.Coupon(id=91, shop_id=11, vendor_id=1, coupon_name="Replacement offer",
                             valid_from=EARLY, valid_to=LATE))
        db.flush()
        db.add(models.Redemption(id=901, wallet_item_id=101, coupon_id=91, shop_id=11,
                                 scanned_by_vendor_id=1, scanned_at=LATE))
        db.get(models.Coupon, 11).coupon_name = "  Current catalog name  "
        db.commit()
    for rows in (_public(c), _merchant(c)):
        row = rows[101]
        assert row["coupon_name"] == "Current catalog name"
        assert row["redeemed_on"] == "2026-11-12"
        assert "redeemed_at" not in row
        assert row["replied_at"] is None
        assert "reply_edited" not in row
        assert rows[102]["replied_at"] == "2026-11-24T12:00:00"
        assert "reply_edited" not in rows[102]


def test_missing_package_cross_shop_and_inconsistent_links_do_not_invent_labels(workspace):
    c, _ = workspace
    with _database() as db:
        db.add(models.Coupon(id=91, shop_id=11, vendor_id=1, coupon_name="Package mirror",
                             kind="package", valid_from=EARLY, valid_to=LATE))
        db.flush()
        packaged = db.get(models.Redemption, 101)
        packaged.coupon_id = 91
        packaged.scanned_at = EARLY
        conflicting = db.get(models.Redemption, 102)
        conflicting.coupon_id = 22
        conflicting.scanned_at = EARLY
        db.get(models.Redemption, 103).wallet_item_id = 104
        db.commit()
        # Simulate a legacy orphan of the coupon row in the fixture only.
        db.connection().exec_driver_sql("PRAGMA foreign_keys=OFF")
        db.get(models.Redemption, 104).coupon_id = 99999
        db.get(models.Redemption, 104).scanned_at = EARLY
        db.commit()
        db.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
    for rows in (_public(c), _merchant(c)):
        for rid in (101, 102, 104):
            assert rows[rid]["coupon_name"] is None
            assert rows[rid]["redeemed_on"] == "2026-11-12"
            assert "redeemed_at" not in rows[rid]
        assert rows[103]["coupon_name"] is None
        assert rows[103]["redeemed_on"] is None


def test_visibility_auth_and_query_cost_are_unchanged_across_page_sizes(workspace):
    c, sql = workspace
    assert 105 not in _public(c)  # disputed is never public
    assert 106 not in _public(c)  # hidden is never public
    disputed_owned = _merchant(c, status="disputed")
    assert list(disputed_owned) == [105]
    assert disputed_owned[105]["coupon_name"] == "Cafe"
    assert not {"redemption_id", "user_id", "wallet_item_id", "dispute_reason"} & disputed_owned[105].keys()
    assert c.get(ROOT, headers=_headers(CUSTOMER),
                 params={"shop_id": 11, "status": "disputed"}).status_code == 403
    assert c.get(ROOT, headers=_headers(OWNER),
                 params={"shop_id": 22, "status": "disputed"}).status_code == 403
    for load in (_public, _merchant):
        totals = []
        for page_size in (1, 4):
            sql.clear()
            rows = load(c, page_size=page_size)
            assert len(rows) == page_size
            assert all(s.lstrip().upper().startswith("SELECT") for s in sql)
            assert len([s for s in sql if "JOIN redemptions" in s]) == 1
            totals.append(len(sql))
        assert totals[0] == totals[1]
