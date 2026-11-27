"""Gap 8 tests: GET /nearby-shops returns rating / has_coupons /
has_packages / created_at and supports sort=rating|newest|distance.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_gap8_nearby_fields.py -v
Uses its own throwaway SQLite file so parallel gap workers do not
delete each other's test DB. Test shops are placed far from every
seeded shop (centre 22.05, 113.85) so a 10 km radius only sees them.
"""
import os
import sys
from datetime import datetime

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
from app.models_line_b import ShopRating  # noqa: E402
from app.seed import seed  # noqa: E402

from tests.db_isolation import sqlite_test_path  # noqa: E402

TEST_DB = sqlite_test_path("coupon_go_test_gap8.db")

engine = create_engine(f"sqlite:///{TEST_DB}",
                       connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine)

CENTER = {"lat": 22.05, "lng": 113.85}


@pytest.fixture(scope="module")
def client():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    Base.metadata.create_all(bind=engine)
    db = TestingSession()
    seed(db)
    # --- fixture shops (ids recorded for assertions) ---
    # A: nearest, no rating, has coupons only, oldest.
    # B: middle distance, rating 4.5, has packages only, newest.
    # C: farthest, rating 3.0, has coupons + packages, middle age.
    vendor = db.query(models.User).filter(
        models.User.email == "vendor@coupon-go.example").first()
    now = datetime(2026, 11, 17, 10, 0, 0)
    specs = [
        ("Gap8 A 店", 22.05, 113.85, datetime(2026, 1, 1, 9, 0, 0),
         None, True, False),
        ("Gap8 B 店", 22.06, 113.85, datetime(2026, 11, 1, 9, 0, 0),
         4.5, False, True),
        ("Gap8 C 店", 22.08, 113.85, datetime(2026, 6, 1, 9, 0, 0),
         3.0, True, True),
    ]
    shop_ids = {}
    for name, lat, lng, created, rating, has_coupon, has_package in specs:
        s = models.Shop(vendor_id=vendor.id, name=name, category_id=1,
                        area_id=1, address="測試地址", latitude=lat,
                        longitude=lng, is_approved=True,
                        created_at=created)
        db.add(s)
        db.flush()
        shop_ids[name] = s.id
        if rating is not None:
            db.add(ShopRating(shop_id=s.id, overall=rating,
                              taste=rating, ambience=rating,
                              service=rating, review_count=2))
        if has_coupon:
            db.add(models.Coupon(
                shop_id=s.id, vendor_id=vendor.id, coupon_type="Discount",
                coupon_name=f"{name} 券", percentage=90,
                valid_from=datetime(2026, 1, 1),
                valid_to=datetime(2027, 1, 1), total=10, remaining=10,
                status="active"))
        if has_package:
            db.add(models.Package(
                shop_id=s.id, name=f"{name} 套餐",
                original_price=200, package_price=150,
                valid_from=datetime(2026, 1, 1),
                valid_to=datetime(2027, 1, 1),
                stock_total=5, stock_left=5, status="active"))
    db.commit()
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


def _nearby(client, **params):
    q = {"lat": CENTER["lat"], "lng": CENTER["lng"]}
    q.update(params)
    r = client.get("/api/v3/nearby-shops", params=q)
    assert r.status_code == 200, r.text
    return r.json()


def _by_name(shops):
    return {s["shop_name"]: s for s in shops}


def test_nearby_returns_four_new_fields(client):
    body = _nearby(client)
    shops = _by_name(body["shops"])
    assert set(shops) == {"Gap8 A 店", "Gap8 B 店", "Gap8 C 店"}
    for s in shops.values():
        assert "rating" in s
        assert "has_coupons" in s
        assert "has_packages" in s
        assert "created_at" in s
    # Ratings: no review row -> null, never invented.
    assert shops["Gap8 A 店"]["rating"] is None
    assert shops["Gap8 B 店"]["rating"] == 4.5
    assert shops["Gap8 C 店"]["rating"] == 3.0
    # Flags match the actual data.
    assert shops["Gap8 A 店"]["has_coupons"] is True
    assert shops["Gap8 A 店"]["has_packages"] is False
    assert shops["Gap8 B 店"]["has_coupons"] is False
    assert shops["Gap8 B 店"]["has_packages"] is True
    assert shops["Gap8 C 店"]["has_coupons"] is True
    assert shops["Gap8 C 店"]["has_packages"] is True
    # created_at echoes the shop row.
    assert shops["Gap8 A 店"]["created_at"].startswith("2026-01-01T09:00:00")
    assert shops["Gap8 B 店"]["created_at"].startswith("2026-11-01T09:00:00")
    assert shops["Gap8 C 店"]["created_at"].startswith("2026-06-01T09:00:00")


def test_nearby_default_sort_is_distance_ascending(client):
    body = _nearby(client)
    assert body["sort"] == "distance"
    names = [s["shop_name"] for s in body["shops"]]
    assert names == ["Gap8 A 店", "Gap8 B 店", "Gap8 C 店"]
    dists = [s["distance_km"] for s in body["shops"]]
    assert dists == sorted(dists)


def test_nearby_sort_rating_desc_nulls_last(client):
    body = _nearby(client, sort="rating")
    assert body["sort"] == "rating"
    names = [s["shop_name"] for s in body["shops"]]
    assert names == ["Gap8 B 店", "Gap8 C 店", "Gap8 A 店"]


def test_nearby_sort_newest_first(client):
    body = _nearby(client, sort="newest")
    assert body["sort"] == "newest"
    names = [s["shop_name"] for s in body["shops"]]
    assert names == ["Gap8 B 店", "Gap8 C 店", "Gap8 A 店"]


def test_nearby_sort_invalid_rejected(client):
    r = client.get("/api/v3/nearby-shops",
                   params={"lat": CENTER["lat"], "lng": CENTER["lng"],
                           "sort": "price"})
    assert r.status_code == 400


def test_nearby_reads_merchants_and_metadata_in_one_query(client):
    from sqlalchemy import event

    selects = []

    def collect(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    event.listen(engine, "before_cursor_execute", collect)
    try:
        result = _nearby(client)
    finally:
        event.remove(engine, "before_cursor_execute", collect)
    assert result["total"] == 3
    assert len(selects) == 1
