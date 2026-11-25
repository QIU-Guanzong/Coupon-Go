"""R1/R2: merchant review authority, durable disputes and SQL read paths."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base, get_db
from app.main import app
from app.models_line_b import Review, ReviewHelpfulVote
from app.models_line_f import ShopMember
from app.security import create_access_token

ROOT = "/api/v3/merchant/reviews"
OWNER = (1, "vendor")
OTHER_OWNER = (2, "vendor")
ADMIN = (3, "admin")
STAFF = (4, "merchant-admin")
CUSTOMER = (5, "customer")
CASHIER = (6, "merchant-admin")


def _headers(actor):
    return {"Authorization": f"Bearer {create_access_token(str(actor[0]), actor[1])}"}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'merchant_reviews.db'}")

    @event.listens_for(engine, "connect")
    def enforce_fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    factory = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    start = datetime(2026, 11, 24, 12)
    with factory.begin() as db:
        for user_id, role in [OWNER, OTHER_OWNER, ADMIN, STAFF, CUSTOMER, CASHIER]:
            db.add(models.User(id=user_id, email=f"user-{user_id}@example.test",
                               password_hash="unused", role=role, name=f"User {user_id}",
                               phone="private-phone"))
        db.flush()
        for shop_id, owner in [(11, 1), (12, 1), (13, 1), (22, 2)]:
            db.add(models.Shop(id=shop_id, vendor_id=owner, name=f"Shop {shop_id}"))
        db.flush()
        db.add_all([ShopMember(shop_id=11, user_id=4, role="merchant-admin", status="active"),
                    ShopMember(shop_id=11, user_id=6, role="cashier", status="active")])
        fixtures = [(101, 11, 1.00, "visible", None), (102, 11, 2.50, "visible", "Thanks"),
                    (103, 11, 4.50, "visible", ""), (104, 11, 4.49, "visible", "   "),
                    (105, 11, 5.00, "disputed", ""), (106, 11, 5.00, "hidden", ""),
                    (121, 12, 3.50, "visible", ""), (221, 22, 4.00, "disputed", ""),
                    (222, 22, 4.00, "visible", "")]
        for shop_id, owner in [(11, 1), (12, 1), (22, 2)]:
            db.add(models.Coupon(id=shop_id, shop_id=shop_id, vendor_id=owner,
                                 coupon_name="Cafe", valid_from=start,
                                 valid_to=start + timedelta(days=7)))
        db.flush()
        for rid, shop, stars, status, reply in fixtures:
            db.add(models.WalletItem(id=rid, user_id=5, coupon_id=shop, status=2))
        db.flush()
        for rid, shop, stars, status, reply in fixtures:
            db.add(models.Redemption(id=rid, wallet_item_id=rid, coupon_id=shop, shop_id=shop,
                                     scanned_by_vendor_id=2 if shop == 22 else 1))
        db.flush()
        for rid, shop, stars, status, reply in fixtures:
            # Same created_at deliberately checks the ID tie-break across pages.
            db.add(Review(id=rid, user_id=5, shop_id=shop, redemption_id=rid,
                          wallet_item_id=rid, taste=3, ambience=3, service=3,
                          overall=stars, status=status, reply_text=reply,
                          replied_at=start if reply and reply.strip() else None,
                          dispute_reason="sensitive reason", created_at=start))
        db.flush()
        db.execute(Review.__table__.update().where(Review.id == 101).values(reply_text=None))
        db.add_all([ReviewHelpfulVote(review_id=101, user_id=1),
                    ReviewHelpfulVote(review_id=101, user_id=2),
                    ReviewHelpfulVote(review_id=102, user_id=2),
                    ReviewHelpfulVote(review_id=103, user_id=3)])

    def get_test_db():
        with factory() as db:
            yield db

    monkeypatch.setitem(app.dependency_overrides, get_db, get_test_db)
    sql = []

    @event.listens_for(engine, "before_cursor_execute")
    def statements(_connection, _cursor, statement, _params, _context, _many):
        sql.append(statement)

    with TestClient(app) as c:
        yield c, sql
    engine.dispose()


def _get(client, actor=OWNER, suffix="", **params):
    response = client.get(ROOT + suffix, headers=_headers(actor), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _ids(payload):
    return [r["id"] for r in payload["reviews"]]


@pytest.mark.parametrize("suffix", ["", "/pending-count"])
def test_real_auth_owner_admin_other_shop_staff_and_customer(workspace, suffix):
    c, _ = workspace
    assert c.get(ROOT + suffix).status_code == 401
    for actor in [STAFF, CUSTOMER, CASHIER]:
        for params in [{}, {"shop_id": 11}]:
            assert c.get(ROOT + suffix, headers=_headers(actor),
                         params=params).status_code == 403
    for shop in [22, 999]:
        assert c.get(ROOT + suffix, headers=_headers(OWNER),
                     params={"shop_id": shop}).status_code == 403
    for actor in [OWNER, ADMIN]:
        _get(c, actor=actor, suffix=suffix, shop_id=11)
    _get(c, actor=OTHER_OWNER, suffix=suffix, shop_id=22)
    _get(c, actor=ADMIN, suffix=suffix, shop_id=22)


def test_merchant_durable_disputed_is_redacted_and_never_public(workspace):
    c, _ = workspace
    first = _get(c, status="disputed")
    assert _ids(first) == [105]
    assert first == _get(c, status="disputed")
    assert _ids(_get(c, actor=ADMIN, status="disputed")) == [221, 105]
    row = first["reviews"][0]
    assert row["status"] == "disputed"
    assert row["user_name"] == "User 5"
    assert not {"dispute_reason", "email", "phone", "replied_by", "user_id",
                "wallet_item_id", "redemption_id"} & row.keys()
    public = c.get("/api/v3/shops/11/reviews", headers=_headers(CUSTOMER))
    assert public.status_code == 200
    assert _ids(public.json()) == [104, 103, 102, 101]
    assert _ids(_get(c, status="all")) == [121, 105, 104, 103, 102, 101]


def test_sql_pagination_sort_filters_distribution_and_no_writes(workspace):
    c, sql = workspace
    latest = _get(c, shop_id=11, page_size=2)
    assert _ids(latest) == [104, 103]
    assert latest["total"] == 4
    assert latest["total_pages"] == 2
    assert latest["rating_distribution"] == {"1": 1, "2": 1, "3": 0, "4": 2, "5": 0}
    assert any("LIMIT" in s.upper() and "FROM reviews" in s for s in sql)
    assert any("GROUP BY" in s.upper() for s in sql)
    assert all(s.lstrip().upper().startswith("SELECT") for s in sql)
    assert _ids(_get(c, shop_id=11, page=2, page_size=2)) == [102, 101]
    last = _get(c, shop_id=11, page=99, page_size=2)
    assert last["current_page"] == 2
    assert _ids(last) == [102, 101]
    assert _ids(_get(c, shop_id=11, sort="helpful", page_size=2)) == [101, 103]
    helpful_item = _get(c, shop_id=11, sort="helpful", page_size=1)["reviews"][0]
    assert helpful_item["helpful_count"] == 2
    assert _ids(_get(c, shop_id=11, status="all", unreplied="true")) == [104, 103, 101]
    assert _ids(_get(c, shop_id=11, unreplied="false")) == [102]
    assert _ids(_get(c, shop_id=11, max_stars=2)) == [101]


def test_review_enrichment_query_count_does_not_grow_with_page_rows(workspace):
    c, sql = workspace
    sql.clear()
    _get(c, shop_id=11, page_size=1)
    first_count = len(sql)
    sql.clear()
    _get(c, shop_id=11, page_size=4)
    assert len(sql) == first_count


def test_pending_counts_all_pages_include_zero_do_not_include_other_or_disputed(workspace):
    c, sql = workspace
    payload = _get(c, suffix="/pending-count")
    assert payload == {"shop_id": None, "total": 4, "shops": [
        {"shop_id": 11, "pending_count": 3}, {"shop_id": 12, "pending_count": 1},
        {"shop_id": 13, "pending_count": 0},
    ]}
    assert _get(c, suffix="/pending-count", shop_id=11)["total"] == 3
    assert _get(c, actor=ADMIN, suffix="/pending-count")["total"] == 5
    assert any("GROUP BY" in s.upper() and "JOIN" in s.upper() for s in sql)
    assert all(s.lstrip().upper().startswith("SELECT") for s in sql)


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 101}, {"shop_id": -1},
                                     {"status": "hidden"}, {"sort": "unknown"},
                                     {"unreplied": "bogus"}, {"max_stars": 5.01}])
def test_input_boundaries(workspace, params):
    c, _ = workspace
    assert c.get(ROOT, headers=_headers(OWNER), params=params).status_code == 422
