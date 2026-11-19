"""Gap 6 tests: a failed withdrawal carries a server-side reason.

Two failure surfaces, both carrying `failure_reason`:
  * the POST /cashback/withdraw error response (a business-rule failure
    raised by cashback_service.withdraw — e.g. below the HK$50 minimum,
    or an account name that does not match the registered name);
  * the withdrawal record in GET /cashback/withdrawals (a payout that
    was accepted as pending and later failed at payout time — there is
    no live FPS/bank payout integration, so ops marks it failed; the
    test simulates that by setting status + failure_reason in the DB,
    exactly as the ops path would).

The success path is untouched: a successful withdrawal has
failure_reason null.

Run from the gap-2 workdir:
  ~/workspace/coupon-go-fullstack/.venv/bin/python -m pytest apps/api/tests/test_gap6_withdraw_failure_reason.py -q
Uses an in-memory SQLite DB; never touches production data.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import models, models_cashback  # noqa: E402,F401
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture(scope="module")
def client():
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


def _register_and_login(client, email):
    assert client.post("/api/v3/new-register", json={
        "email": email, "password": "Demo123!", "name": "Test",
        "role": "customer"}).status_code == 200
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


def _make_coupon(shop_id=1, discount_price=1200):
    db = TestingSession()
    try:
        vendor = db.query(models.User).filter(
            models.User.email == "vendor@coupon-go.example").first()
        shop = db.query(models.Shop).filter(models.Shop.id == shop_id).first()
        c = models.Coupon(
            shop_id=shop.id, vendor_id=vendor.id, coupon_type="Cash",
            coupon_name="測試券", category_id=shop.category_id,
            discount_price=discount_price,
            valid_from=_now() - timedelta(days=1),
            valid_to=_now() + timedelta(days=30),
            total=100, remaining=100, status="active")
        db.add(c)
        db.commit()
        return c.id
    finally:
        db.close()


def _redeem(client, h, vh, coupon_id):
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": coupon_id}, headers=h)
    assert r.status_code == 200, r.text
    wid = r.json()["wallet_item_id"]
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=h)
    assert qr.status_code == 200, qr.text
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 200, r.text
    return r.json()


def _earn_withdrawable(client, h):
    """Two dine-in redemptions at shop 1: HK$60 unlocked, withdrawable."""
    vh = _vendor_headers(client)
    _redeem(client, h, vh, _make_coupon(shop_id=1, discount_price=1200))
    _redeem(client, h, vh, _make_coupon(shop_id=1, discount_price=1200))
    bal = client.get("/api/v3/cashback", headers=h).json()
    assert bal["withdrawable_balance"] == 60.0, bal


def _withdraw(client, h, amount, account_name="Test"):
    return client.post("/api/v3/cashback/withdraw", json={
        "amount": amount, "method": "fps", "account_name": account_name,
        "account_ref": "12345678"}, headers=h)


def _mark_withdrawal_failed(withdrawal_id, reason):
    """Simulate the ops payout failure via the service helper
    (cashback_service.mark_withdrawal_failed)."""
    from app import cashback_service as svc  # noqa: E402
    db = TestingSession()
    try:
        svc.mark_withdrawal_failed(db, withdrawal_id, reason)
        db.commit()
    finally:
        db.close()


def test_withdraw_below_minimum_returns_failure_reason(client):
    h = _register_and_login(client, "gap6-alice@example.com")
    _earn_withdrawable(client, h)
    r = _withdraw(client, h, 5)
    assert r.status_code == 400, r.text
    assert r.json()["failure_reason"], r.json()
    assert "50" in r.json()["failure_reason"]
    # No withdrawal row was created for a rejected request.
    wds = client.get("/api/v3/cashback/withdrawals", headers=h).json()["withdrawals"]
    assert wds == []


def test_withdraw_name_mismatch_returns_failure_reason(client):
    h = _register_and_login(client, "gap6-bob@example.com")
    _earn_withdrawable(client, h)
    r = _withdraw(client, h, 50, account_name="Someone Else")
    assert r.status_code == 400, r.text
    assert r.json()["failure_reason"], r.json()
    assert "註冊姓名" in r.json()["failure_reason"]


def test_failed_withdrawal_record_carries_failure_reason(client):
    h = _register_and_login(client, "gap6-carol@example.com")
    _earn_withdrawable(client, h)
    r = _withdraw(client, h, 50)
    assert r.status_code == 200, r.text
    wid = r.json()["withdrawal"]["id"]
    # Success path: no failure reason on the fresh record.
    assert r.json()["withdrawal"]["failure_reason"] is None
    _mark_withdrawal_failed(wid, "銀行退回：賬戶不存在")
    wds = client.get("/api/v3/cashback/withdrawals", headers=h).json()["withdrawals"]
    assert len(wds) == 1
    assert wds[0]["status"] == "failed"
    assert wds[0]["failure_reason"] == "銀行退回：賬戶不存在"


def test_review_release_then_pay_reconciles_fifo_and_replays(client):
    h = _register_and_login(client, "review-reconcile@example.com")
    _earn_withdrawable(client, h)
    vh = _vendor_headers(client)
    _redeem(client, h, vh, _make_coupon())
    finance = client.post("/api/v3/login", json={"email": "finance@coupon-go.example",
                                                  "password": "Demo123!"}).json()
    fh = {"Authorization": "Bearer " + finance["access_token"]}
    body = {"amount": 75, "account_name": "Test", "account_ref": "demo-ref", "request_key": "first"}
    def apply():
        response = client.post("/api/v3/cashback/withdraw", headers=h, json=body)
        assert response.status_code == 200, response.text
        return response.json()["withdrawal"]["id"]
    def act(wid, status, detail=""):
        return client.post(f"/api/v3/admin/withdrawals/{wid}", headers=fh, json={"status":status, "detail": detail})
    def check(available, held, paid):
        balance = client.get("/api/v3/cashback", headers=h).json()
        assert [balance[k] for k in ("withdrawable_balance", "withdrawal_held", "withdrawal_paid")] == [available, held, paid]
        assert available + held + paid + balance["pending_balance"] == balance["total_earned"]
    assert client.get("/api/v3/admin/withdrawals", headers=h).status_code == 403
    first = apply()
    assert apply() == first
    check(45, 75, 0)
    assert act(first, "paid", "premature").status_code == 400
    assert act(first, "approved").status_code == 200
    check(45, 75, 0)
    for _ in range(2):
        assert act(first, "failed", "示範銀行退回").status_code == 200
        check(120, 0, 0)
    assert apply() == first
    body["request_key"] = "second"
    second = apply()
    assert second != first
    assert act(second, "approved").status_code == 200
    for _ in range(2):
        assert act(second, "paid", "TEST-REF-02").status_code == 200
        check(45, 0, 75)
    assert act(second, "failed", "late").status_code == 400
    history = client.get("/api/v3/cashback/withdrawals", headers=h).json()["withdrawals"]
    assert history[1]["hold_released"]
    assert [e["status"] for e in history[0]["events"]] == ["pending", "approved", "paid"]
    assert history[0]["events"][-1]["detail"] == "TEST-REF-02"


def test_legacy_failed_hold_recovered_once(client):
    h = _register_and_login(client, "legacy-reconcile@example.com")
    _earn_withdrawable(client, h)
    wid = _withdraw(client, h, 50).json()["withdrawal"]["id"]
    with TestingSession() as db:
        db.query(models_cashback.WithdrawalAllocation).filter_by(withdrawal_id=wid).delete()
        db.query(models_cashback.WithdrawalEvent).filter_by(withdrawal_id=wid).delete()
        db.get(models_cashback.CashbackWithdrawal, wid).status = "failed"
        db.commit()
    assert client.get("/api/v3/cashback", headers=h).json()["withdrawal_unreconciled"] == 50
    for _ in range(2):
        _mark_withdrawal_failed(wid, "舊單退回")
    assert client.get("/api/v3/cashback", headers=h).json()["withdrawal_unreconciled"] == 0
    assert client.get("/api/v3/cashback", headers=h).json()["withdrawable_balance"] == 60


def test_demo_catalogue_repeat_preserves_stock(client):
    from app.seed_showcase import seed_showcase, MERCHANTS
    with TestingSession() as db:
        seed_showcase(db)
        shop = db.query(models.Shop).filter_by(name=MERCHANTS[0][0]).one()
        pkg = db.query(models.Package).filter_by(shop_id=shop.id).first()
        pkg.stock_left -= 1
        db.commit()
        remaining = pkg.stock_left
        seed_showcase(db)
        assert db.query(models.Shop).count() == 26
        assert db.query(models.Package).count() == 14
        assert db.get(models.Package, pkg.id).stock_left == remaining
        assert db.query(models.Coupon).filter_by(package_id=pkg.id).count() == 1

        from app.demo_covers import OFFER_COVERS, SHOP_COVERS, BARGAIN_COVERS, cover_path
        from app.models_bargain import BargainCampaign
        from app.routers.bargain import campaign_out
        from app.seed_bargain import seed_bargain_campaigns
        from app.serializers_packages import package_out
        seed_bargain_campaigns(db)
        covers = [s.image for s in db.query(models.Shop).all()]
        for (merchant, name), slug in OFFER_COVERS.items():
            offer = db.query(models.Coupon).join(models.Shop).filter(
                models.Shop.name_en == merchant, models.Coupon.coupon_name == name).one()
            assert offer.image == cover_path(slug)
            covers.append(offer.image)
        for package in db.query(models.Package):
            assert package_out(db, package)["images"][0].startswith("assets/images/mvp-set-")
        covers.extend(campaign_out(db, c)["image"] for c in db.query(BargainCampaign))
        assert len(SHOP_COVERS) == 20 and len(BARGAIN_COVERS) == 2
        assert len(covers) == len(set(covers)) == 79
        photo = db.query(models.Coupon).filter_by(package_id=pkg.id).one()
        photo.image = "https://merchant.example/custom-cover.jpg"
        db.commit()
        seed_showcase(db)
        assert photo.image == "https://merchant.example/custom-cover.jpg"


def test_failed_hold_after_earning_revoked_offsets_clawback(client):
    h = _register_and_login(client, "revoked-hold@example.com")
    vendor = _vendor_headers(client)
    first = _redeem(client, h, vendor, _make_coupon())
    _redeem(client, h, vendor, _make_coupon())
    wid = _withdraw(client, h, 60).json()["withdrawal"]["id"]
    from app import cashback_service as svc
    with TestingSession() as db:
        redemption = db.get(models.Redemption, first["redemption_id"])
        redemption.revoked_at = _now()
        svc.void_for_revocation(db, redemption)
        db.commit()
    _mark_withdrawal_failed(wid, "退回")
    bal = client.get("/api/v3/cashback", headers=h).json()
    assert [bal[k] for k in ("withdrawable_balance", "withdrawal_held", "adjustment_total")] == [0, 0, 0]
    assert sorted(a["amount"] for a in bal["adjustments"]) == [-60, 60]


def test_history_can_reach_oldest_request(client):
    h = _register_and_login(client, "history-pages@example.com")
    with TestingSession() as db:
        uid = db.query(models.User.id).filter_by(email="history-pages@example.com").scalar()
        db.add_all([models_cashback.CashbackWithdrawal(user_id=uid, amount=50, method="fps",
                    account_name="Test", status="paid") for _ in range(53)])
        db.commit()
    first = client.get("/api/v3/cashback/withdrawals", headers=h).json()
    second = client.get("/api/v3/cashback/withdrawals", params={"before":first["next"]}, headers=h).json()
    ids = [w["id"] for page in (first, second) for w in page["withdrawals"]]
    assert len(ids) == len(set(ids)) == 53
    assert ids == sorted(ids, reverse=True)
    assert second["next"] is None
