"""M9-7 tests: true account deletion (POST /delete-account).

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_m9_delete_account.py -v
Own throwaway SQLite file in an independent temp dir (queue global
rule 4: never a shared /tmp/*.db); never touches production data.

Rules (M9-7, FEATURE_GAP_20261120 §3 + frontend R1/D5 contract,
merge queue §A):
- Delete deactivates the account (is_active=False, deleted_at + reason
  recorded); audit/settlement rows are kept, nothing is hard-deleted.
- Unused wallet coupons are voided (status 1 -> 3, expired); already
  used coupons are untouched.
- Points are voided: a negative PointTransaction brings the balance to 0
  (ledger kept for audit).
- A withdrawable cashback balance REFUSES the delete (409 + the balance,
  fail closed) so the user's money is never silently deleted; the
  account, coupons and points are left untouched. Pending (not yet
  withdrawable) cashback does not block deletion.
- reason is optional: POST {} deletes fine.
- Queue §A (revive hole, auditor reproduced): deletion anonymises the
  profile (email -> deleted+{user_id}@invalid, name/phone cleared,
  password hash voided); resend / verify / forgot / reset / login all
  refuse a deleted account (login: 403 「此帳戶已刪除」); the old email
  can re-register as a brand-new account; a merchant account with an
  operating shop, unsettled money or in-progress orders is refused
  with 409 and a reason.
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
from app import models_cashback  # noqa: E402
from app import models_line_f  # noqa: E402
from app import models_packages  # noqa: E402
from app import security  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.deps import points_balance  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

engine = None
TestingSession = None


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    global engine, TestingSession
    db_file = tmp_path_factory.mktemp("m9_delete") / "test.db"
    engine = create_engine(f"sqlite:///{db_file}",
                           connect_args={"check_same_thread": False})
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


def _register_and_login(client, email, role="customer"):
    # M9-2 (merged main): GET /legal/current -> {"required", "documents": {...}};
    # in dev with no LEGAL_* configured, documents is {} and registration
    # needs no accepted versions (LegalVersionsIn fields are Optional).
    _legal = client.get("/api/v3/legal/current").json()
    docs = _legal.get("documents") or {}
    body = {"email": email, "password": "Demo123!", "name": "Test",
            "role": role, "phone": "91234567"}
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


def _user_by_id(uid):
    db = TestingSession()
    try:
        return db.get(models.User, uid)
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


def _insert_deleted_user(email):
    """A soft-deleted row whose email was NOT anonymised (pre-fix data):
    the deleted-account guards must still refuse it everywhere."""
    db = TestingSession()
    try:
        u = models.User(
            email=email, password_hash=security.hash_password("Demo123!"),
            name="舊用戶", phone="91234567", role="customer", is_active=False,
            deleted_at=_now(), delete_reason="test")
        db.add(u)
        db.commit()
        db.add(models.OtpCode(
            email=email, code="123456", purpose="register",
            expires_at=_now() + timedelta(minutes=10)))
        db.add(models.OtpCode(
            email=email, code="654321", purpose="forgot",
            expires_at=_now() + timedelta(minutes=10)))
        db.commit()
        return u.id
    finally:
        db.close()


# ---------- happy path ----------

def test_delete_deactivates_and_voids_unused_coupons_and_points(client):
    h = _register_and_login(client, "del-alice@example.com")
    vh = _vendor_headers(client)
    unused = _claim(client, h, _make_coupon(name="未用券"))
    used = _claim(client, h, _make_coupon(name="已用券"))
    _redeem(client, h, vh, used)
    uid = _user("del-alice@example.com").id
    assert points_balance(TestingSession(), uid) == 40  # 2 claims +10, 1 redeem +20

    r = _delete(client, h, {"reason": "不再使用"})
    assert r.status_code == 200, r.text

    db = TestingSession()
    try:
        user = db.get(models.User, uid)
        assert user.is_active is False
        assert user.deleted_at is not None
        assert user.delete_reason == "不再使用"
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
        assert note is not None and "不再使用" in note.body
    finally:
        db.close()

    # The email no longer maps to any account, so the old password fails
    # like a nonexistent account; authed endpoints reject the old token.
    r = client.post("/api/v3/login", json={
        "email": "del-alice@example.com", "password": "Demo123!"})
    assert r.status_code == 401, r.text
    r = client.get("/api/v3/me", headers=h)
    assert r.status_code == 401, r.text


def test_delete_without_reason_succeeds(client):
    h = _register_and_login(client, "del-bob@example.com")
    uid = _user("del-bob@example.com").id
    r = _delete(client, h)  # POST {} — the app sends an empty body today
    assert r.status_code == 200, r.text
    user = _user_by_id(uid)
    assert user.is_active is False and user.deleted_at is not None
    assert user.delete_reason in (None, "")


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


def test_delete_with_only_pending_cashback_succeeds(client):
    h = _register_and_login(client, "del-dave@example.com")
    _claim(client, h, _make_coupon(name="待確認券"))
    txn_id = _insert_cashback_txn("del-dave@example.com", 30, "pending")
    uid = _user("del-dave@example.com").id

    r = _delete(client, h)
    assert r.status_code == 200, r.text
    assert _user_by_id(uid).is_active is False
    db = TestingSession()
    try:
        # Pending (not withdrawable) earnings stay in the ledger untouched.
        assert db.get(models_cashback.CashbackTransaction, txn_id).status == "pending"
    finally:
        db.close()


def test_delete_requires_login(client):
    r = client.post("/api/v3/delete-account", json={})
    assert r.status_code == 401, r.text


# ---------- queue §A.1: anonymisation ----------

def test_delete_anonymizes_profile_fields(client):
    h = _register_and_login(client, "anon@example.com")
    uid = _user("anon@example.com").id
    old_hash = _user("anon@example.com").password_hash
    assert _user("anon@example.com").name == "Test"
    assert _user("anon@example.com").phone == "91234567"

    assert _delete(client, h, {"reason": "私隱"}).status_code == 200, "delete failed"

    u = _user_by_id(uid)
    assert u.id == uid  # id and ledger links are kept
    assert u.email == f"deleted+{uid}@invalid"
    assert u.name == ""
    assert u.phone == ""
    assert u.password_hash != old_hash
    assert not security.verify_password("Demo123!", u.password_hash)
    assert u.is_active is False
    assert u.deleted_at is not None
    assert u.delete_reason == "私隱"


# ---------- queue §A.2: revive attack must fail ----------

def test_revive_attack_fails(client):
    """Auditor's repro, end to end: register -> delete -> resend the
    same email's OTP -> verify -> log in with the old password.
    After anonymisation the email maps to no account at all, so every
    step of the chain must fail and no token may be issued."""
    email = "revive-alice@example.com"
    h = _register_and_login(client, email)
    # A forgot OTP issued BEFORE deletion, still unused and unexpired.
    assert client.post("/api/v3/password/forgot",
                       json={"email": email}).status_code == 200
    old_code = client.get("/api/v3/_dev/otp",
                          params={"email": email}).json()["code"]
    assert _delete(client, h).status_code == 200

    # The pre-delete code cannot reset the password: the delete voids
    # unused OTPs for the old email, so the code is dead (400); even
    # without that, no account maps to the email anymore (403/404).
    r = client.post("/api/v3/password/reset", json={
        "email": email, "code": old_code, "new_password": "Attack123!"})
    assert r.status_code in (400, 403, 404), r.text
    # A freshly resent code cannot verify either.
    r = client.post("/api/v3/new-resent-otp",
                    json={"email": email, "purpose": "register"})
    assert r.status_code == 200, r.text  # unknown email: generic reply, no user
    r = client.post("/api/v3/new-verify-otp", json={
        "email": email, "code": "000000", "purpose": "register"})
    assert r.status_code in (400, 403, 404), r.text
    # Old password login fails; the account is gone.
    r = client.post("/api/v3/login", json={
        "email": email, "password": "Demo123!"})
    assert r.status_code == 401, r.text
    assert "access_token" not in r.text


def test_deleted_account_refused_everywhere(client):
    """A deleted row whose email was not anonymised (pre-fix data) must
    be refused with the distinct message on every auth entry point."""
    email = "legacy-deleted@example.com"
    _insert_deleted_user(email)

    r = client.post("/api/v3/login", json={
        "email": email, "password": "Demo123!"})
    assert r.status_code == 403, r.text
    assert "此帳戶已刪除" in r.text
    for path, body in [
        ("/api/v3/new-resent-otp", {"email": email, "purpose": "register"}),
        ("/api/v3/resent-otp", {"email": email, "purpose": "forgot"}),
        ("/api/v3/password/forgot", {"email": email}),
    ]:
        r = client.post(path, json=body)
        assert r.status_code == 403, (path, r.text)
        assert "此帳戶已刪除" in r.text
    r = client.post("/api/v3/new-verify-otp", json={
        "email": email, "code": "123456", "purpose": "register"})
    assert r.status_code == 403, r.text
    assert "此帳戶已刪除" in r.text
    r = client.post("/api/v3/password/reset", json={
        "email": email, "code": "654321", "new_password": "Attack123!"})
    assert r.status_code == 403, r.text
    assert "此帳戶已刪除" in r.text


# ---------- queue §A.3: same email re-registers as a new account ----------

def test_reregister_same_email_after_delete(client):
    email = "re-reg@example.com"
    h = _register_and_login(client, email)
    uid = _user(email).id
    assert _delete(client, h).status_code == 200

    # Old row is anonymised, so the email is free again.
    assert _user(email) is None
    h2 = _register_and_login(client, email)
    new = _user(email)
    assert new is not None
    assert new.id != uid
    assert new.is_active is True
    assert new.deleted_at is None
    assert new.name == "Test"
    # New account starts clean; old ledger rows still belong to the old id.
    assert points_balance(TestingSession(), new.id) == 0
    assert _user_by_id(uid).email == f"deleted+{uid}@invalid"
    # New account can log in; old credentials do not leak across.
    r = client.post("/api/v3/login", json={
        "email": email, "password": "Demo123!"})
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == new.id
    # The old token is dead.
    assert client.get("/api/v3/me", headers=h).status_code == 401


def test_reregister_frees_legacy_deleted_email(client):
    """Pre-fix deleted row still holding the email: registering that
    email anonymises the legacy row and creates a fresh account."""
    email = "legacy-re-reg@example.com"
    old_id = _insert_deleted_user(email)
    h = _register_and_login(client, email)
    new = _user(email)
    assert new.id != old_id
    assert new.is_active is True
    assert _user_by_id(old_id).email == f"deleted+{old_id}@invalid"


# ---------- queue §A.4: merchant guard ----------

def test_merchant_delete_refused_with_active_shop(client):
    vh = _vendor_headers(client)  # seed vendor owns approved shop 1
    r = _delete(client, vh)
    assert r.status_code == 409, r.text
    assert "店鋪" in r.json()["detail"]
    assert _user("vendor@coupon-go.example").is_active is True


def test_merchant_delete_refused_with_unsettled_settlement(client):
    h = _register_and_login(client, "vendor2@example.com", role="vendor")
    uid = _user("vendor2@example.com").id
    db = TestingSession()
    try:
        shop = models.Shop(vendor_id=uid, name="未結算店", category_id=1,
                           area_id=1, is_approved=False)
        db.add(shop)
        db.commit()
        db.add(models_line_f.Settlement(
            shop_id=shop.id, period="2026-11",
            period_start=_now() - timedelta(days=30),
            period_end=_now(), status="pending"))
        db.commit()
        shop_id = shop.id
    finally:
        db.close()

    r = _delete(client, h)
    assert r.status_code == 409, r.text
    assert "結算" in r.json()["detail"]
    assert _user_by_id(uid).is_active is True

    # Once the settlement is paid, deletion succeeds.
    db = TestingSession()
    try:
        s = db.query(models_line_f.Settlement).filter_by(
            shop_id=shop_id).first()
        s.status = "paid"
        db.commit()
    finally:
        db.close()
    assert _delete(client, h).status_code == 200


def test_merchant_delete_refused_with_in_progress_order(client):
    h = _register_and_login(client, "vendor3@example.com", role="vendor")
    uid = _user("vendor3@example.com").id
    db = TestingSession()
    try:
        shop = models.Shop(vendor_id=uid, name="訂單店", category_id=1,
                           area_id=1, is_approved=False)
        db.add(shop)
        db.commit()
        pkg = models_packages.Package(
            shop_id=shop.id, name="套餐", original_price=100,
            package_price=80, valid_from=_now() - timedelta(days=1),
            valid_to=_now() + timedelta(days=30))
        db.add(pkg)
        db.commit()
        order = models_packages.Order(
            user_id=uid, order_type="package", package_id=pkg.id,
            amount=80, paid_amount=80, payment_status="paid",
            refund_status="none", settlement_status="pending")
        db.add(order)
        db.commit()
        order_id = order.id
    finally:
        db.close()

    r = _delete(client, h)
    assert r.status_code == 409, r.text
    assert "訂單" in r.json()["detail"]
    assert _user_by_id(uid).is_active is True

    # Once the order is refunded, deletion succeeds.
    db = TestingSession()
    try:
        o = db.get(models_packages.Order, order_id)
        o.refund_status = "refunded"
        db.commit()
    finally:
        db.close()
    assert _delete(client, h).status_code == 200
