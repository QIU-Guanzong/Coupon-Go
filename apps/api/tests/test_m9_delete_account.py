"""M9-7 tests: true account deletion (POST /delete-account).

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_m9_delete_account.py -v
Own throwaway SQLite file; never touches production data.

Rules (M9-7, FEATURE_GAP_20261120 §3 + frontend R1/D5 contract):
- Delete deactivates the account (is_active=False, deleted_at + reason
  recorded); audit/settlement rows are kept, nothing is hard-deleted.
- Unused wallet coupons are voided (status 1 -> 3, expired); already
  used coupons are untouched.
- Points are voided: a negative PointTransaction brings the balance to 0
  (ledger kept for audit).
- A withdrawable cashback balance REFUSES the delete (409 + the balance,
  fail closed) so the user's money is never silently deleted; the
  account, coupons and points are left untouched. Pending cashback also
  blocks deletion until its disposition can preserve the user's rights.
- reason is optional: POST {} deletes fine.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import models  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.deps import points_balance  # noqa: E402
from app.main import app  # noqa: E402
from app import models_cashback  # noqa: E402
from app.seed import seed  # noqa: E402

engine = None
TestingSession = None


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    global engine, TestingSession
    database = tmp_path_factory.mktemp("m97-deletion") / "test.db"
    engine = create_engine(f"sqlite:///{database}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine)
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
    engine.dispose()


def _register_and_login(client, email):
    # M9-2 (merged main): GET /legal/current -> {"required", "documents": {...}};
    # in dev with no LEGAL_* configured, documents is {} and registration
    # needs no accepted versions (LegalVersionsIn fields are Optional).
    _legal = client.get("/api/v3/legal/current").json()
    docs = _legal.get("documents") or {}
    body = {"email": email, "password": "Demo123!", "name": "Test",
            "role": "customer"}
    if docs:
        body["accepted_terms_version"] = docs["terms"]["version"]
        body["accepted_privacy_version"] = docs["privacy"]["version"]
    assert client.post("/api/v3/new-register", json=body).status_code == 200
    code = client.get("/api/v3/_dev/otp", params={"email": email}).json()["code"]
    r = client.post("/api/v3/new-verify-otp", json={
        "email": email, "code": code, "purpose": "register"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _vendor_headers(client):
    r = client.post("/api/v3/login", json={
        "email": "vendor@coupon-go.example", "password": "Demo123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_coupon(shop_id=1, discount_price=None, name="刪除測試券"):
    """Insert a fresh active coupon and return its id."""
    db = TestingSession()
    try:
        vendor = db.query(models.User).filter(
            models.User.email == "vendor@coupon-go.example").first()
        shop = db.query(models.Shop).filter(models.Shop.id == shop_id).first()
        c = models.Coupon(
            shop_id=shop.id, vendor_id=vendor.id, coupon_type="Cash",
            coupon_name=name, category_id=shop.category_id,
            min_spend=0, discount_price=discount_price,
            valid_from=_now() - timedelta(days=1),
            valid_to=_now() + timedelta(days=30),
            total=100, remaining=100, status="active")
        db.add(c)
        db.commit()
        return c.id
    finally:
        db.close()


def _claim(client, h, coupon_id):
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": coupon_id}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["wallet_item_id"]


def _redeem(client, h, vh, wallet_item_id):
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wallet_item_id}, headers=h)
    assert qr.status_code == 200, qr.text
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 200, r.text
    return r.json()


def _user(email):
    db = TestingSession()
    try:
        return db.query(models.User).filter(models.User.email == email).first()
    finally:
        db.close()


def _insert_cashback_txn(email, amount, status):
    """Seed one cashback earning straight in the DB (no API can create
    one anymore since FIX-2); needs a claimed wallet item for the bare
    redemption row. Returns the transaction id."""
    db = TestingSession()
    try:
        user = db.query(models.User).filter(models.User.email == email).first()
        vendor = db.query(models.User).filter(
            models.User.email == "vendor@coupon-go.example").first()
        item = db.query(models.WalletItem).filter(
            models.WalletItem.user_id == user.id).first()
        assert item is not None
        red = models.Redemption(wallet_item_id=item.id, coupon_id=item.coupon_id,
                                shop_id=1, scanned_by_vendor_id=vendor.id)
        db.add(red)
        db.flush()
        t = models_cashback.CashbackTransaction(
            user_id=user.id, merchant_id=1, redemption_id=red.id,
            channel="online", amount=amount, rate=0.05, status=status,
            funded_by="merchant_commission", expected_confirm_at=None,
            expires_at=None)
        db.add(t)
        db.commit()
        return t.id
    finally:
        db.close()


def _delete(client, h, body=None):
    return client.post("/api/v3/delete-account",
                       json={} if body is None else body, headers=h)


# ---------- happy path ----------

def test_delete_deactivates_and_voids_unused_coupons_and_points(client):
    h = _register_and_login(client, "del-alice@example.com")
    user_id = _user("del-alice@example.com").id
    vh = _vendor_headers(client)
    unused = _claim(client, h, _make_coupon(name="未用券"))
    used = _claim(client, h, _make_coupon(name="已用券"))
    _redeem(client, h, vh, used)
    assert points_balance(TestingSession(), _user("del-alice@example.com").id) == 40  # 2 claims +10, 1 redeem +20

    r = _delete(client, h, {"reason": "不再使用"})
    assert r.status_code == 200, r.text

    db = TestingSession()
    try:
        user = db.get(models.User, user_id)
        assert user.is_active is False
        assert user.deleted_at is not None
        assert user.email != "del-alice@example.com" and user.name == "" and user.phone == ""
        assert user.delete_reason is None
        items = {i.id: i.status for i in db.query(models.WalletItem).filter(
            models.WalletItem.user_id == user.id).all()}
        assert items[unused] == 3  # voided
        assert items[used] == 2    # already used: untouched
        assert points_balance(db, user.id) == 0
        void = db.query(models.PointTransaction).filter(
            models.PointTransaction.user_id == user.id,
            models.PointTransaction.reason == "account_delete").all()
        assert len(void) == 1 and void[0].delta == -40
        note = db.query(models.Notification).filter(
            models.Notification.user_id == user.id,
            models.Notification.title == "帳戶刪除").first()
        assert note is not None and "不再使用" not in note.body
    finally:
        db.close()

    # A deleted account can no longer log in or call authed endpoints.
    r = client.post("/api/v3/login", json={
        "email": "del-alice@example.com", "password": "Demo123!"})
    assert r.status_code == 401, r.text
    r = client.get("/api/v3/me", headers=h)
    assert r.status_code == 401, r.text


def test_delete_without_reason_succeeds(client):
    h = _register_and_login(client, "del-bob@example.com")
    user_id = _user("del-bob@example.com").id
    r = _delete(client, h)  # POST {} — the app sends an empty body today
    assert r.status_code == 200, r.text
    db = TestingSession()
    try:
        user = db.get(models.User, user_id)
        assert user.is_active is False and user.deleted_at is not None
        assert user.delete_reason is None
    finally:
        db.close()


def test_delete_removes_public_review_content_and_recomputes_rating(client, monkeypatch):
    departing = _register_and_login(client, "del-review@example.com")
    remaining = _register_and_login(client, "del-review-other@example.com")
    departing_id = _user("del-review@example.com").id
    vendor = _vendor_headers(client)
    review_ids = []
    for headers, score, content in ((departing, 1, "private text"),
                                    (remaining, 5, "still visible")):
        item = _claim(client, headers, _make_coupon(name=content))
        _redeem(client, headers, vendor, item)
        response = client.post("/api/v3/reviews", headers=headers, json={
            "wallet_item_id": item, "taste": score, "ambience": score,
            "service": score, "content": content,
            "images": ["https://example.test/private-photo.jpg"],
            "tags": ["private tag"], "claim_points": False,
        })
        assert response.status_code == 200, response.text
        review_ids.append(response.json()["review"]["id"])
    for headers, review_id in ((departing, review_ids[1]), (remaining, review_ids[0])):
        assert client.post(f"/api/v3/reviews/{review_id}/helpful", headers=headers,
                           json={"helpful": True}).status_code == 200
    db = TestingSession()
    try:
        db.add(models.Notification(user_id=departing_id, title="帳戶刪除",
                                   body="private old notification"))
        db.commit()
    finally:
        db.close()
    from app.routers import reviews as reviews_router
    with monkeypatch.context() as patcher:
        def fail_recompute(*args, **kwargs):
            raise RuntimeError("simulated rating failure")
        patcher.setattr(reviews_router, "recompute_rating", fail_recompute)
        with pytest.raises(RuntimeError, match="simulated rating failure"):
            _delete(client, departing)
    db = TestingSession()
    try:
        assert db.get(models.User, departing_id).deleted_at is None
        assert db.get(models.Review, review_ids[0]).content == "private text"
        assert db.get(models.Review, review_ids[0]).status == "visible"
        assert db.query(models.Notification).filter_by(user_id=departing_id,
                                                       body="private old notification").count() == 1
    finally:
        db.close()
    assert _delete(client, departing).status_code == 200
    listing = client.get("/api/v3/shops/1/reviews").json()
    assert [r["id"] for r in listing["reviews"] if r["id"] in review_ids] == [review_ids[1]]
    rating = client.get("/api/v3/shops/1/rating").json()
    assert rating["review_count"] == listing["total"]
    assert rating["rating_distribution"]["1"] == 0
    db = TestingSession()
    try:
        removed = db.get(models.Review, review_ids[0])
        assert removed.status == "hidden" and not removed.counts_toward_rating
        assert removed.content == "" and removed.images_list == [] and removed.tags_list == []
        assert db.query(models.ReviewHelpfulVote).filter(
            models.ReviewHelpfulVote.review_id.in_(review_ids)).count() == 0
        notes = db.query(models.Notification).filter_by(user_id=departing_id).all()
        assert len(notes) == 1 and notes[0].body == "帳戶已刪除"
        assert db.get(models.Review, review_ids[1]).content == "still visible"
    finally:
        db.close()


# ---------- cashback guard (fail closed) ----------

def test_delete_refused_with_withdrawable_cashback(client):
    h = _register_and_login(client, "del-carol@example.com")
    wid = _claim(client, h, _make_coupon(name="返現券"))
    _insert_cashback_txn("del-carol@example.com", 60, "confirmed")
    assert points_balance(TestingSession(),
                          _user("del-carol@example.com").id) == 10

    r = _delete(client, h, {"reason": "想刪號"})
    assert r.status_code == 409, r.text
    assert r.json()["withdrawable_balance"] == 60.0
    assert r.json()["code"] == "withdrawable_cashback"
    assert "請先提現" in r.json()["detail"]

    # Nothing changed: fail closed, the user can still withdraw.
    user = _user("del-carol@example.com")
    assert user.is_active is True and user.deleted_at is None
    db = TestingSession()
    try:
        item = db.get(models.WalletItem, wid)
        assert item.status == 1
        assert points_balance(db, user.id) == 10
    finally:
        db.close()


@pytest.mark.parametrize("status", ["pending", "tracked"])
def test_delete_voids_unearned_cashback_but_preserves_ledger(client, status):
    email = f"del-{status}@example.com"
    headers = _register_and_login(client, email)
    user_id = _user(email).id
    _claim(client, headers, _make_coupon(name="待確認券"))
    txn_id = _insert_cashback_txn(email, 30, status)

    response = _delete(client, headers)
    assert response.status_code == 200, response.text
    assert client.get("/api/v3/me", headers=headers).status_code == 401
    with TestingSession() as db:
        user = db.get(models.User, user_id)
        assert not user.is_active and user.deleted_at is not None
        txn = db.get(models_cashback.CashbackTransaction, txn_id)
        assert (txn.user_id, float(txn.amount), txn.status, txn.void_reason) == (
            user_id, 30, "void", "account_delete",
        )
        assert txn.redemption_id is not None


def test_delete_preserves_online_cashback_already_due_for_withdrawal(client):
    email = "del-due@example.com"
    h = _register_and_login(client, email)
    user_id = _user(email).id
    _claim(client, h, _make_coupon(name="待確認券"))
    txn_id = _insert_cashback_txn(email, 30, "pending")
    with TestingSession() as db:
        db.get(models_cashback.CashbackTransaction, txn_id).expected_confirm_at = _now() - timedelta(seconds=1)
        db.commit()
    # A later sweep must not strand the value on a deleted identity.
    assert _delete(client, h).status_code == 409
    db = TestingSession()
    try:
        from app import cashback_service
        cashback_service.sweep_user(db, user_id)
        db.commit()
        assert db.get(models.User, user_id).is_active is True
        assert db.get(models_cashback.CashbackTransaction, txn_id).status == "withdrawable"
        assert cashback_service.balances(db, user_id)["withdrawable_balance"] == 30
    finally:
        db.close()


def test_cashback_void_rolls_back_if_deletion_fails(client, monkeypatch):
    from app.routers import auth

    email = "del-rollback@example.com"
    headers = _register_and_login(client, email)
    user_id = _user(email).id
    wallet_id = _claim(client, headers, _make_coupon(name="回滾券"))
    txn_id = _insert_cashback_txn(email, 30, "pending")

    def fail_points(*args, **kwargs):
        raise RuntimeError("synthetic failure after ledger write")

    monkeypatch.setattr(auth, "add_points", fail_points)
    with pytest.raises(RuntimeError, match="synthetic failure after ledger write"):
        _delete(client, headers)
    with TestingSession() as db:
        assert db.get(models.User, user_id).deleted_at is None
        assert db.get(models.WalletItem, wallet_id).status == 1
        txn = db.get(models_cashback.CashbackTransaction, txn_id)
        assert (txn.status, txn.void_reason) == ("pending", None)
    assert client.get("/api/v3/me", headers=headers).status_code == 200


def test_delete_requires_login(client):
    r = client.post("/api/v3/delete-account", json={})
    assert r.status_code == 401, r.text
