"""M4 tests: review points / stars split self-check (2026-11-17).

Locks in the M4 self-check findings at the interface level.
NOTE (M7, same day): the points/stars SPLIT itself was then removed —
every visible review now counts toward the rating, points or not
(see test_m7_review_rating.py). The item-1 test below was updated to
the M7 rule; the points-ledger / cashback-ledger separation it also
checks is unchanged.
1. Points and stars are separate books: a review moves the points
   ledger (+15, reason "review") and — since M7 — the shop rating,
   but never the cashback ledger (CashbackTransaction).
2. Review eligibility is enforced at the interface: only a redeemed
   coupon / order can be reviewed, one review per redemption, and the
   package-order path goes through the same POST /reviews (there is no
   separate review path in packages.py — a package purchase only issues
   a WalletItem, redeemed through the same scan flow).
3. reviews.redemption_id has a DB UNIQUE constraint (model +
   migration 0002_line_b) as a backstop behind the app-layer check;
   a concurrent double submit must come back 400, never 500.
4. EXIF end-to-end: an image uploaded with EXIF, attached to a review,
   is stored EXIF-free (unit-level stripping is covered by
   test_p1_upload_exif.py).

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_m4_review_self_check.py -v
Uses its own throwaway SQLite file so parallel workers do not delete
each other's test DB.
"""
import os
import struct
import sys
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import models  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models_cashback import CashbackTransaction  # noqa: E402
from app.models_line_b import Review  # noqa: E402
from app.seed import seed  # noqa: E402

from tests.db_isolation import sqlite_test_path  # noqa: E402

TEST_DB = sqlite_test_path("coupon_go_test_m4.db")

engine = create_engine(f"sqlite:///{TEST_DB}",
                       connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine)

MARKER = b"SECRET-GPS-MARKER-1234"


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



def _admin_headers(client):
    r = client.post("/api/v3/login", json={
        "email": "admin@coupon-go.example", "password": "Demo123!"})
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


def _create_package(client, name="M4 套餐", refund_policy="anytime",
                    stock=5, price=150, original=200):
    vh = _vendor_headers(client)
    body = {
        "shop_id": 1, "name": name,
        "items": [{"name": "主菜一份", "qty": 1}, {"name": "飲品兩杯", "qty": 2}],
        "person_count": 2, "original_price": original, "package_price": price,
        "valid_from": "2026-11-01T00:00:00",
        "valid_to": "2026-12-31T00:00:00",
        "usage_rules": {"need_booking": True, "includes": ["加一服務費"]},
        "stock_total": stock, "refund_policy": refund_policy,
    }
    r = client.post("/api/v3/packages", json=body, headers=vh)
    assert r.status_code == 201, r.text
    pkg = r.json()["package"]
    # Support both optional manual review and default auto-approval.
    if pkg["status"] == "pending_review":
        ah = _admin_headers(client)
        r = client.post(f"/api/v3/admin/packages/{pkg['id']}/approve",
                        params={"approve": True}, headers=ah)
        assert r.status_code == 200, r.text
    r = client.get("/api/v3/package-details", params={"package_id": pkg["id"]})
    assert r.status_code == 200, r.text
    return r.json()["package"]


def _buy_paid(client, headers, package_id, method="fps"):
    r = client.post(f"/api/v3/packages/{package_id}/purchase",
                    json={"payment_method": method, "client_ref": ""},
                    headers=headers)
    assert r.status_code == 200, r.text
    order_id = r.json()["order"]["id"]
    r = client.post(f"/api/v3/orders/{order_id}/confirm-payment",
                    json={"outcome": "succeeded"}, headers=headers)
    assert r.status_code == 200, r.text
    order = r.json()["order"]
    assert order["payment_status"] == "paid"
    return order


def _jpeg_with_exif():
    app0 = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    exif = b"Exif\x00\x00" + MARKER + b"\x00" * 10
    out = b"\xff\xd8"
    out += b"\xff\xe0" + struct.pack(">H", len(app0) + 2) + app0
    out += b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif
    out += b"\xff\xd9"
    return out


# ---------- M4 item 1: points / rating / cashback are three separate books ----------
def test_m4_points_review_moves_points_not_rating_nor_cashback(client):
    """M7 update: a +15-points review moves BOTH the points ledger and
    the shop rating; it still never touches the cashback ledger."""
    h, wid, rid = _redeem(client, "m4-points@example.com", 2)
    db = TestingSession()
    user_id = db.query(models.WalletItem).filter(
        models.WalletItem.id == wid).first().user_id
    points_before = db.query(models.PointTransaction).filter(
        models.PointTransaction.user_id == user_id).count()
    cashback_before = db.query(CashbackTransaction).filter(
        CashbackTransaction.user_id == user_id).count()
    db.close()

    r = client.post("/api/v3/reviews", json=_review_body(wid), headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["review"]["points_given"] is True
    assert r.json()["review"]["counts_toward_rating"] is True

    db = TestingSession()
    points_after = db.query(models.PointTransaction).filter(
        models.PointTransaction.user_id == user_id).count()
    cashback_after = db.query(CashbackTransaction).filter(
        CashbackTransaction.user_id == user_id).count()
    review_tx = db.query(models.PointTransaction).filter(
        models.PointTransaction.user_id == user_id,
        models.PointTransaction.reason == "review").all()
    db.close()
    # exactly one +15 review-points row; the cashback ledger is untouched
    assert points_after == points_before + 1
    assert [t.delta for t in review_tx] == [15]
    assert cashback_after == cashback_before

    rating = client.get("/api/v3/shops/1/rating").json()
    assert rating["review_count"] == 1 and rating["overall"] == 4.0
    # M7: the star distribution and the averages now cover the same
    # set (all visible reviews).
    assert sum(rating["rating_distribution"].values()) == 1
    assert sum(rating["rating_distribution"].values()) == rating["review_count"]


# ---------- M4 item 2: package orders review through the same path ----------
def test_m4_package_order_review_uses_same_path(client):
    h = _register_and_login(client, "m4-package@example.com")
    pkg = _create_package(client, name="M4 套餐評價")
    paid = _buy_paid(client, h, pkg["id"], method="card")
    wid = paid["wallet_item_id"]
    # unredeemed package voucher cannot be reviewed yet
    r = client.post("/api/v3/reviews", json=_review_body(wid), headers=h)
    assert r.status_code == 400
    # redeem through the same scan flow as a plain coupon
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=h)
    assert qr.status_code == 200, qr.text
    vh = _vendor_headers(client)
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 200, r.text
    # redeemed package order reviews via POST /reviews, no separate path
    rating_before = client.get("/api/v3/shops/1/rating").json()
    r = client.post("/api/v3/reviews",
                    json=_review_body(wid, claim_points=False), headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["review"]["counts_toward_rating"] is True
    assert r.json()["review"]["overall"] == 4.0
    rating = client.get("/api/v3/shops/1/rating").json()
    # M7: the earlier points review in this module already counts toward
    # shop 1, so assert the delta, not an absolute count (overall stays
    # 4.0 — both reviews score 5/4/3).
    assert rating["review_count"] == rating_before["review_count"] + 1
    assert rating["overall"] == 4.0
    # one review per redemption, via either identifier
    r = client.post("/api/v3/reviews", json=_review_body(wid), headers=h)
    assert r.status_code == 400
    r = client.post("/api/v3/review", params={"wallet_item_id": wid}, headers=h)
    assert r.status_code == 400


# ---------- M4 item 2 (backstop): DB UNIQUE on reviews.redemption_id ----------
def test_m4_redemption_id_unique_constraint_backstop(client):
    assert Review.__table__.c.redemption_id.unique is True
    h, wid, rid = _redeem(client, "m4-unique@example.com", 2)
    r = client.post("/api/v3/reviews", json=_review_body(wid), headers=h)
    assert r.status_code == 200, r.text
    first = r.json()["review"]
    db = TestingSession()
    try:
        dup = Review(user_id=first["user_id"], shop_id=first["shop_id"],
                     redemption_id=rid, wallet_item_id=wid,
                     taste=5, ambience=5, service=5, overall=5.0,
                     content="dup", points_given=False,
                     counts_toward_rating=True, status="visible")
        db.add(dup)
        with pytest.raises(IntegrityError):
            db.flush()
    finally:
        db.rollback()
        db.close()


# ---------- M4 item 2 (race): concurrent double submit -> 400, never 500 ----------
def test_m4_concurrent_duplicate_review_returns_400(client):
    h, wid, rid = _redeem(client, "m4-race@example.com", 2)
    body = _review_body(wid)
    barrier = threading.Barrier(2)
    results = []

    def _post():
        with TestClient(app) as c:
            barrier.wait(timeout=10)
            results.append(c.post("/api/v3/reviews", json=body,
                                  headers=h).status_code)

    threads = [threading.Thread(target=_post) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(results) == 2
    assert sorted(results) == [200, 400], results
    # exactly one review row exists for the redemption
    db = TestingSession()
    try:
        n = db.query(Review).filter(Review.redemption_id == rid).count()
    finally:
        db.close()
    assert n == 1


# ---------- M4 item 3: EXIF stripped end-to-end (upload -> review -> stored) ----------
def test_m4_exif_stripped_end_to_end_upload_review_stored(client):
    h = _register_and_login(client, "m4-exif-up@example.com")
    r = client.post("/api/v3/uploads/review-images",
                    files=[("files", ("a.jpg", _jpeg_with_exif(),
                                      "image/jpeg"))],
                    headers=h)
    assert r.status_code == 201, r.text
    path = r.json()["images"][0]
    stored = client.get(path)
    assert stored.status_code == 200
    assert MARKER not in stored.content
    assert b"Exif" not in stored.content

    uh, wid, rid = _redeem(client, "m4-exif-review@example.com", 2)
    r = client.post("/api/v3/reviews", json={
        "redemption_id": rid, "taste": 5, "ambience": 5, "service": 5,
        "content": "有圖評價", "images": [path], "claim_points": False},
        headers=uh)
    assert r.status_code == 200, r.text
    assert r.json()["review"]["images"] == [path]
    # the stored file behind the review is still EXIF-free
    stored = client.get(path)
    assert stored.status_code == 200
    assert MARKER not in stored.content
    assert b"JFIF" in stored.content  # non-EXIF segments untouched
