"""M7 tests: review rating basis correction (2026-11-17).

M7 decouples points from stars: EVERY visible review counts toward
the shop rating, whether or not it took the +15 review points
(claim_points only controls the points, never counts_toward_rating).
The old split (points review -> counts_toward_rating=False) is gone;
test_line_b.py::test_review_points_do_not_move_rating and
test_m4_review_self_check.py::test_m4_points_review_moves_points_not_rating_nor_cashback
were updated to the new rule alongside this file.

Also covered here:
1. A revoked redemption cannot be reviewed afterwards (400).
2. Revoking a redemption claws back the review points it earned
   (-15 PointTransaction, reason "review_revoke") and hides the
   review — decision: HIDE, not "keep visible but stop counting".
   Rationale: M7's invariant is "every visible review counts", so a
   review that must stop counting cannot stay visible; hiding keeps
   the row (audit trail, reversible by admin) instead of deleting it.
   The wallet item's `reviewed` flag is reset so a later re-redemption
   of the same (returned) coupon can be reviewed fresh.
3. Migration 0015 backfills counts_toward_rating=True for legacy rows.
4. No duplicate UNIQUE constraint on reviews.redemption_id was added:
   M4 verified it already exists in the model (models_line_b.py:57-58)
   and in migration 0002_line_b.py:42-43 (DB-level since line B v1);
   this file locks that verification in as a regression guard.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_m7_review_rating.py -v
Uses its own throwaway SQLite file so parallel workers do not delete
each other's test DB.
"""
import importlib.util
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

TEST_DB = "/tmp/coupon_go_test_m7.db"

engine = create_engine(f"sqlite:///{TEST_DB}",
                       connect_args={"check_same_thread": False})
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


def _vendor_headers(client):
    return _login(client, "vendor@coupon-go.example")


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
    vh = _vendor_headers(client)
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 200, r.text
    rid = client.get("/api/v3/get-user-record-list",
                     headers=h).json()["records"][0]["id"]
    return h, wid, rid


def _review_body(wid, **over):
    body = {"wallet_item_id": wid, "taste": 5, "ambience": 4, "service": 3,
            "content": "好食，服務唔錯", "tags": ["壽司"], "claim_points": True}
    body.update(over)
    return body


def _balance(client, headers):
    return client.get("/api/v3/points", headers=headers).json()["balance"]


def _rating(client, shop_id):
    return client.get(f"/api/v3/shops/{shop_id}/rating").json()


# ---------- M7 req 1: points reviews count toward the rating ----------
def test_m7_points_review_counts_toward_rating(client):
    h, wid, rid = _redeem(client, "m7-points@example.com", 2)
    before = _rating(client, 1)
    bal_before = _balance(client, h)
    r = client.post("/api/v3/reviews", json=_review_body(wid), headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["review"]["points_given"] is True
    # M7: claim_points no longer decides the rating flag.
    assert r.json()["review"]["counts_toward_rating"] is True
    assert r.json()["balance"] == bal_before + 15
    after = _rating(client, 1)
    assert after["review_count"] == before["review_count"] + 1
    assert after["overall"] == 4.0  # (5+4+3)/3, the only review so far
    assert sum(after["rating_distribution"].values()) == after["review_count"]


def test_m7_no_points_review_still_counts(client):
    h, wid, rid = _redeem(client, "m7-nopoints@example.com", 3)
    before = _rating(client, 2)
    bal_before = _balance(client, h)
    r = client.post("/api/v3/reviews",
                    json=_review_body(wid, claim_points=False), headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["review"]["points_given"] is False
    assert r.json()["review"]["counts_toward_rating"] is True
    assert r.json()["balance"] == bal_before  # no points, still counted
    after = _rating(client, 2)
    assert after["review_count"] == before["review_count"] + 1
    assert after["overall"] == 4.0


# ---------- M7 req 2a: a revoked redemption cannot be reviewed ----------
def test_m7_cannot_review_revoked_redemption(client):
    h, wid, rid = _redeem(client, "m7-revoked@example.com", 2)
    vh = _vendor_headers(client)
    r = client.post(f"/api/v3/redemptions/{rid}/revoke", headers=vh)
    assert r.status_code == 200, r.text
    # via redemption_id
    r = client.post("/api/v3/reviews",
                    json={"redemption_id": rid, "taste": 5, "ambience": 5,
                          "service": 5}, headers=h)
    assert r.status_code == 400, r.text
    assert "撤销" in r.json()["detail"]
    # via wallet_item_id (resolves to the same revoked redemption)
    r = client.post("/api/v3/reviews", json=_review_body(wid), headers=h)
    assert r.status_code == 400, r.text
    assert "撤销" in r.json()["detail"]


# ---------- M7 req 2b: revoke claws back review points + hides review ----------
def test_m7_revoke_claws_back_review_points_and_hides_review(client):
    h, wid, rid = _redeem(client, "m7-clawback@example.com", 2)
    vh = _vendor_headers(client)
    rating_before = _rating(client, 1)
    bal_after_redeem = _balance(client, h)
    r = client.post("/api/v3/reviews", json=_review_body(wid), headers=h)
    assert r.status_code == 200, r.text
    review_id = r.json()["review"]["id"]
    assert _balance(client, h) == bal_after_redeem + 15
    assert _rating(client, 1)["review_count"] == rating_before["review_count"] + 1

    r = client.post(f"/api/v3/redemptions/{rid}/revoke", headers=vh)
    assert r.status_code == 200, r.text
    # the revoke response reports what happened to the review
    assert r.json()["review"] is not None
    assert r.json()["review"]["review_id"] == review_id
    assert r.json()["review"]["points_clawed_back"] == 15
    assert r.json()["review"]["status"] == "hidden"
    # points: +15 review points AND +20 redeem points are both reversed
    assert _balance(client, h) == bal_after_redeem - 20
    # the ledger shows the clawback row (same book as the redeem reversal)
    db = TestingSession()
    try:
        tx = db.query(models.PointTransaction).filter(
            models.PointTransaction.user_id ==
            db.query(models.WalletItem).filter(
                models.WalletItem.id == wid).first().user_id,
            models.PointTransaction.reason == "review_revoke").all()
        assert [t.delta for t in tx] == [-15]
        review = db.query(Review).filter(Review.id == review_id).first()
        assert review is not None  # row kept, not deleted
        assert review.status == "hidden"
        assert review.counts_toward_rating is False
    finally:
        db.close()
    # rating and public list no longer include the hidden review
    assert _rating(client, 1)["review_count"] == rating_before["review_count"]
    listed = client.get("/api/v3/shops/1/reviews").json()
    assert all(rv["id"] != review_id for rv in listed["reviews"])


def test_m7_revoke_without_review_reports_no_review(client):
    h, wid, rid = _redeem(client, "m7-noreview@example.com", 2)
    vh = _vendor_headers(client)
    bal_before = _balance(client, h)
    r = client.post(f"/api/v3/redemptions/{rid}/revoke", headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["review"] is None
    # only the +20 redeem points are reversed
    assert _balance(client, h) == bal_before - 20


def test_m7_redeemed_again_after_revoke_can_review_again(client):
    """The revoked coupon returns to unused; re-redeem -> fresh review."""
    h, wid, rid = _redeem(client, "m7-rereview@example.com", 2)
    vh = _vendor_headers(client)
    r = client.post("/api/v3/reviews", json=_review_body(wid), headers=h)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/v3/redemptions/{rid}/revoke", headers=vh)
    assert r.status_code == 200, r.text
    # present the returned coupon again and scan it
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=h)
    assert qr.status_code == 200, qr.text
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 200, r.text
    # the new redemption can be reviewed (the old hidden review does not
    # block it: reviewed flag was reset on revoke, new redemption_id)
    r = client.post("/api/v3/reviews",
                    json=_review_body(wid, taste=4, ambience=4, service=4),
                    headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["review"]["counts_toward_rating"] is True


# ---------- M7 req 3: migration 0015 backfills legacy rows ----------
def _load_migration_0015():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "alembic", "versions",
                        "0015_m7_review_rating_backfill.py")
    spec = importlib.util.spec_from_file_location("m7_migration_0015", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_m7_backfill_migration_sets_counts_true(client):
    db = TestingSession()
    db.add(Review(user_id=1, shop_id=1, redemption_id=990101,
                  wallet_item_id=990101, taste=5, ambience=5, service=5,
                  overall=5.0, content="舊積分評價", points_given=True,
                  counts_toward_rating=False, status="visible"))
    db.add(Review(user_id=1, shop_id=1, redemption_id=990102,
                  wallet_item_id=990102, taste=4, ambience=4, service=4,
                  overall=4.0, content="舊無積分評價", points_given=False,
                  counts_toward_rating=True, status="visible"))
    db.commit()
    db.close()

    mod = _load_migration_0015()
    assert mod.down_revision == "0014_m6_settlement_basis_backfill"
    with engine.begin() as conn:
        mod.backfill_counts_toward_rating(conn)

    db = TestingSession()
    try:
        old_points = db.query(Review).filter(
            Review.redemption_id == 990101).first()
        old_plain = db.query(Review).filter(
            Review.redemption_id == 990102).first()
        assert old_points.counts_toward_rating is True
        assert old_plain.counts_toward_rating is True
    finally:
        db.close()


# ---------- M7 req 4: unique constraint already exists — do not duplicate ----------
def test_m7_redemption_unique_constraint_already_exists():
    # Model-level (models_line_b.py:57-58) and migration-level
    # (0002_line_b.py:42-43) since line B v1; M7 adds no new constraint.
    assert Review.__table__.c.redemption_id.unique is True
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "alembic", "versions",
                           "0002_line_b.py"), encoding="utf-8") as f:
        assert "unique=True" in f.read()
    with open(os.path.join(here, "..", "alembic", "versions",
                           "0015_m7_review_rating_backfill.py"),
              encoding="utf-8") as f:
        m7 = f.read()
    assert "create_unique_constraint" not in m7
    assert "UniqueConstraint" not in m7
