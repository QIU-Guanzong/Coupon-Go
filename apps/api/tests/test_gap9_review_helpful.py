"""Gap 9 tests: review helpful votes + server-side star distribution.

- POST /reviews/{id}/helpful — one vote per user, cancellable.
- GET /shops/{shop_id}/reviews and GET /shops/{shop_id}/rating return
  rating_distribution computed over ALL visible reviews (not page 1).
- GET /shops/{shop_id}/reviews supports sort=helpful.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_gap9_review_helpful.py -v
Uses its own throwaway SQLite file so parallel gap workers do not
delete each other's test DB.
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import models  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models_line_b import Review  # noqa: E402
from app.seed import seed  # noqa: E402

from tests.db_isolation import sqlite_test_path  # noqa: E402

TEST_DB = sqlite_test_path("coupon_go_test_gap9.db")

engine = create_engine(f"sqlite:///{TEST_DB}", connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine)


@pytest.fixture(scope="module")
def client():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    Base.metadata.create_all(bind=engine)
    db = TestingSession()
    seed(db)
    db.close()

    def _get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _register_and_login(client, email, role="customer"):
    assert client.post("/api/v3/new-register", json={
        "email": email, "password": "Demo123!", "name": "Test",
        "role": role}).status_code == 200
    code = client.get("/api/v3/_dev/otp", params={"email": email}).json()["code"]
    r = client.post("/api/v3/new-verify-otp", json={
        "email": email, "code": code, "purpose": "register"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _login(client, email):
    r = client.post("/api/v3/login", json={"email": email, "password": "Demo123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _redeem(client, email, coupon_id):
    """Register, claim coupon, QR, vendor scan. Returns (headers, wid, rid)."""
    h = _register_and_login(client, email)
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": coupon_id}, headers=h)
    assert r.status_code == 200, r.text
    wid = client.get("/api/v3/get-favorite-coupons-list",
                     headers=h).json()["items"][0]["id"]
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=h)
    assert qr.status_code == 200, qr.text
    vh = _login(client, "vendor@coupon-go.example")
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 200, r.text
    rid = client.get("/api/v3/get-user-record-list",
                     headers=h).json()["records"][0]["id"]
    return h, wid, rid


def _make_review(client, email, coupon_id, **over):
    h, wid, rid = _redeem(client, email, coupon_id)
    body = {"wallet_item_id": wid, "taste": 5, "ambience": 5, "service": 5,
            "content": "好食", "claim_points": False}
    body.update(over)
    r = client.post("/api/v3/reviews", json=body, headers=h)
    assert r.status_code == 200, r.text
    return h, r.json()["review"]


# ---------- helpful votes ----------
def test_helpful_vote_adds_one(client):
    _, review = _make_review(client, "gap9-vote@example.com", 2)
    rid = review["id"]
    voter = _register_and_login(client, "gap9-voter1@example.com")
    r = client.post(f"/api/v3/reviews/{rid}/helpful",
                    json={"helpful": True}, headers=voter)
    assert r.status_code == 200, r.text
    assert r.json()["helpful_count"] == 1
    assert r.json()["helped_by_me"] is True
    # list shows the count, and helped_by_me is per-user
    lst = client.get("/api/v3/shops/%s/reviews" % review["shop_id"],
                     headers=voter).json()["reviews"]
    mine = [x for x in lst if x["id"] == rid][0]
    assert mine["helpful_count"] == 1
    assert mine["helped_by_me"] is True
    anon = client.get("/api/v3/shops/%s/reviews" % review["shop_id"]).json()["reviews"]
    assert [x for x in anon if x["id"] == rid][0]["helped_by_me"] is False


def test_helpful_vote_idempotent(client):
    _, review = _make_review(client, "gap9-vote2@example.com", 3)
    rid = review["id"]
    voter = _register_and_login(client, "gap9-voter2@example.com")
    for _ in range(2):
        r = client.post(f"/api/v3/reviews/{rid}/helpful",
                        json={"helpful": True}, headers=voter)
        assert r.status_code == 200, r.text
        assert r.json()["helpful_count"] == 1
    # a second user votes: count becomes 2
    voter2 = _register_and_login(client, "gap9-voter2b@example.com")
    r = client.post(f"/api/v3/reviews/{rid}/helpful",
                    json={"helpful": True}, headers=voter2)
    assert r.json()["helpful_count"] == 2


def test_helpful_cancel_decrements(client):
    _, review = _make_review(client, "gap9-vote3@example.com", 4)
    rid = review["id"]
    voter = _register_and_login(client, "gap9-voter3@example.com")
    assert client.post(f"/api/v3/reviews/{rid}/helpful",
                       json={"helpful": True}, headers=voter).json()["helpful_count"] == 1
    r = client.post(f"/api/v3/reviews/{rid}/helpful",
                    json={"helpful": False}, headers=voter)
    assert r.status_code == 200, r.text
    assert r.json()["helpful_count"] == 0
    assert r.json()["helped_by_me"] is False
    # cancelling again is a no-op, never negative
    r = client.post(f"/api/v3/reviews/{rid}/helpful",
                    json={"helpful": False}, headers=voter)
    assert r.json()["helpful_count"] == 0


def test_helpful_requires_login_and_existing_review(client):
    _, review = _make_review(client, "gap9-vote4@example.com", 5)
    r = client.post(f"/api/v3/reviews/{review['id']}/helpful",
                    json={"helpful": True})
    assert r.status_code == 401
    voter = _register_and_login(client, "gap9-voter4@example.com")
    r = client.post("/api/v3/reviews/999999/helpful",
                    json={"helpful": True}, headers=voter)
    assert r.status_code == 404


def test_helpful_sort_orders_by_votes(client):
    # Direct-DB seed (same shop, no redeem flow): 3 visible reviews.
    db = TestingSession()
    vendor = db.query(models.User).filter(
        models.User.email == "vendor@coupon-go.example").first()
    shop = models.Shop(vendor_id=vendor.id, name="Gap9 排序店")
    db.add(shop)
    db.flush()
    coupon = db.query(models.Coupon).first()
    rids = []
    for n in range(3):
        u = models.User(email=f"gap9-sort-u{n}@example.com",
                        password_hash="x", name=f"s{n}")
        db.add(u)
        db.flush()
        item = models.WalletItem(user_id=u.id, coupon_id=coupon.id,
                                 status=2, code="000000", reviewed=True)
        db.add(item)
        db.flush()
        red = models.Redemption(wallet_item_id=item.id, coupon_id=coupon.id,
                                shop_id=shop.id,
                                scanned_by_vendor_id=vendor.id)
        db.add(red)
        db.flush()
        rev = Review(user_id=u.id, shop_id=shop.id, redemption_id=red.id,
                     wallet_item_id=item.id, taste=5, ambience=5, service=5,
                     overall=5.0, content="", images="[]", tags="[]",
                     points_given=False, counts_toward_rating=True,
                     status="visible")
        db.add(rev)
        db.flush()
        rids.append(rev.id)
    shop_id = shop.id
    db.commit()
    db.close()
    ra_id, rb_id, rc_id = rids
    v1 = _register_and_login(client, "gap9-sort-v1@example.com")
    v2 = _register_and_login(client, "gap9-sort-v2@example.com")
    # A: 2 votes, B: 1 vote, C: 0 votes
    for h in (v1, v2):
        assert client.post(f"/api/v3/reviews/{ra_id}/helpful",
                           json={"helpful": True}, headers=h).status_code == 200
    assert client.post(f"/api/v3/reviews/{rb_id}/helpful",
                       json={"helpful": True}, headers=v1).status_code == 200
    got = client.get(f"/api/v3/shops/{shop_id}/reviews",
                     params={"sort": "helpful"}).json()["reviews"]
    ids = [x["id"] for x in got]
    assert ids.index(ra_id) < ids.index(rb_id) < ids.index(rc_id)
    assert [x for x in got if x["id"] == ra_id][0]["helpful_count"] == 2


# ---------- star distribution (all visible reviews, not page 1) ----------
def _seed_distribution_shop(client):
    """25 visible reviews on a fresh shop: 10x5, 6x4, 4x3, 3x2, 2x1."""
    db = TestingSession()
    vendor = db.query(models.User).filter(
        models.User.email == "vendor@coupon-go.example").first()
    shop = models.Shop(vendor_id=vendor.id, name="Gap9 分布店")
    db.add(shop)
    db.flush()
    coupon = db.query(models.Coupon).first()
    counts = {5: 10, 4: 6, 3: 4, 2: 3, 1: 2}
    n = 0
    for star, cnt in counts.items():
        for _ in range(cnt):
            n += 1
            u = models.User(email=f"gap9-dist-{n}@example.com",
                            password_hash="x", name=f"u{n}")
            db.add(u)
            db.flush()
            item = models.WalletItem(user_id=u.id, coupon_id=coupon.id,
                                     status=2, code="000000", reviewed=True)
            db.add(item)
            db.flush()
            red = models.Redemption(wallet_item_id=item.id, coupon_id=coupon.id,
                                    shop_id=shop.id,
                                    scanned_by_vendor_id=vendor.id)
            db.add(red)
            db.flush()
            db.add(Review(user_id=u.id, shop_id=shop.id, redemption_id=red.id,
                          wallet_item_id=item.id, taste=star, ambience=star,
                          service=star, overall=float(star), content="",
                          images="[]", tags="[]", points_given=False,
                          counts_toward_rating=True, status="visible"))
    # Direct DB inserts bypass the API, so rebuild the aggregate row the
    # same way POST /reviews does (recompute_rating).
    from app.routers.reviews import recompute_rating  # noqa: E402
    recompute_rating(db, shop.id)
    db.commit()
    shop_id = shop.id
    db.close()
    return shop_id


def test_rating_distribution_covers_all_pages(client):
    shop_id = _seed_distribution_shop(client)
    expected = {"5": 10, "4": 6, "3": 4, "2": 3, "1": 2}
    page1 = client.get(f"/api/v3/shops/{shop_id}/reviews",
                       params={"page": 1}).json()
    assert page1["total"] == 25
    assert len(page1["reviews"]) == 20  # page size is 20
    # distribution sums to the full 25, not the 20 on page 1
    assert page1["rating_distribution"] == expected
    assert sum(page1["rating_distribution"].values()) == 25
    page2 = client.get(f"/api/v3/shops/{shop_id}/reviews",
                       params={"page": 2}).json()
    assert page2["rating_distribution"] == expected
    rating = client.get(f"/api/v3/shops/{shop_id}/rating").json()
    assert rating["rating_distribution"] == expected
    assert rating["review_count"] == 25
