"""Line C (套餐 2A) tests: purchase -> pay -> voucher -> redeem -> refund.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_packages.py -v
Uses an in-memory SQLite DB (StaticPool) so these tests do not share the
/tmp file DB that the legacy suite uses.
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

from app import models  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

PG_URL = os.environ.get("PACKAGES_TEST_POSTGRES_URL")
engine = (create_engine(PG_URL, pool_size=5, max_overflow=5) if PG_URL else
          create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool))
TestingSession = sessionmaker(bind=engine, autoflush=False)


@pytest.fixture(scope="module")
def client():
    if PG_URL:
        Base.metadata.drop_all(bind=engine)
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
    if PG_URL:
        engine.dispose()


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


def _admin_headers(client):
    r = client.post("/api/v3/login", json={
        "email": "admin@coupon-go.example", "password": "Demo123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _stock_left(client, package_id):
    r = client.get("/api/v3/package-details", params={"package_id": package_id})
    assert r.status_code == 200, r.text
    return r.json()["package"]["stock_left"]


def _create_package(client, name="測試雙人套餐", refund_policy="anytime",
                    stock=5, price=150, original=200, shop_id=1):
    vh = _vendor_headers(client)
    body = {
        "shop_id": shop_id, "name": name,
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


def _purchase(client, headers, package_id, method="fps", client_ref=""):
    r = client.post(f"/api/v3/packages/{package_id}/purchase",
                    json={"payment_method": method, "client_ref": client_ref},
                    headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _pay(client, headers, order_id, outcome="succeeded", payment_id=None):
    body = {"outcome": outcome}
    if payment_id is not None:
        body["payment_id"] = payment_id
    r = client.post(f"/api/v3/orders/{order_id}/confirm-payment",
                    json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["order"]


def _buy_paid(client, headers, package_id, method="fps", client_ref=""):
    bought = _purchase(client, headers, package_id, method, client_ref)
    order = _pay(client, headers, bought["order"]["id"], "succeeded")
    assert order["payment_status"] == "paid"
    return bought, order


# ---------- packages ----------
def test_packages_list_and_details(client):
    r = client.get("/api/v3/packages")
    assert r.status_code == 200
    pkgs = r.json()["packages"]
    assert len(pkgs) >= 2  # seeded demo packages
    first = [p for p in pkgs if p["name"] == "櫻花壽司雙人套餐"][0]
    assert first["package_price"] == 298 and first["original_price"] == 428
    assert first["save_amount"] == 130
    assert first["refund_policy"] == "anytime"
    assert first["person_count"] == 2
    assert len(first["items"]) == 3
    r = client.get("/api/v3/package-details",
                   params={"package_id": first["id"]})
    assert r.status_code == 200
    assert r.json()["package"]["refund_policy_text"] != ""
    assert client.get("/api/v3/package-details",
                      params={"package_id": 99999}).status_code == 404


def test_vendor_create_package_and_customer_forbidden(client):
    pkg = _create_package(client, name="創建測試套餐", refund_policy="anytime")
    assert pkg["status"] == "active" and pkg["stock_left"] == 5
    h = _register_and_login(client, "pkg-customer@example.com")
    r = client.post("/api/v3/packages", json={
        "shop_id": 1, "name": "x", "original_price": 100,
        "package_price": 90, "valid_from": "2026-11-01T00:00:00",
        "valid_to": "2026-12-31T00:00:00"}, headers=h)
    assert r.status_code == 403


def test_package_coupon_cannot_be_claimed_free(client):
    pkg = _create_package(client, name="不可免費領套餐")
    db = TestingSession()
    coupon = (db.query(models.Coupon)
              .filter(models.Coupon.package_id == pkg["id"]).first())
    assert coupon is not None and coupon.kind == "package"
    db.close()
    h = _register_and_login(client, "pkg-claim@example.com")
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": coupon.id}, headers=h)
    assert r.status_code == 400  # package vouchers are bought, not claimed


# ---------- purchase -> pay -> wallet ----------
def test_purchase_pay_voucher_wallet_chain(client):
    h = _register_and_login(client, "pkg-buyer@example.com")
    pkg = _create_package(client, name="下單鏈路套餐", stock=5)
    before = _stock_left(client, pkg["id"])
    bought = _purchase(client, h, pkg["id"], method="fps",
                       client_ref="chain-001")
    order = bought["order"]
    assert order["payment_status"] == "pending"
    assert order["amount"] == 150 and order["payment_method"] == "fps"
    assert bought["payment"]["status"] == "requires_payment"
    # FIX-1: simulator pi_test_ ids are server-side only, never returned.
    assert bought["payment"]["gateway_ref"] == ""
    assert "pi_test_" not in bought.get("client_secret", "")
    assert bought["gateway"] == "stripe"
    assert "test_mode" not in bought  # P1-5: test_mode is not user-facing
    assert _stock_left(client, pkg["id"]) == before - 1  # stock reserved

    paid = _pay(client, h, order["id"], "succeeded")
    assert paid["payment_status"] == "paid"
    assert paid["paid_amount"] == 150
    assert paid["settlement_status"] == "pending"  # ledger booked, F reconciles
    assert paid["net_amount"] == 150 and paid["commission_amount"] == 0
    assert paid["wallet_item_id"] is not None

    wallet = client.get("/api/v3/get-favorite-coupons-list",
                        params={"status": 1}, headers=h).json()
    mine = [i for i in wallet["items"] if i["id"] == paid["wallet_item_id"]]
    assert len(mine) == 1
    assert mine[0]["is_package"] is True
    assert mine[0]["source"] == "purchase"
    assert mine[0]["order_id"] == paid["id"]
    assert mine[0]["status"] == 1  # 未使用

    orders = client.get("/api/v3/orders", headers=h).json()["orders"]
    assert any(o["id"] == paid["id"] for o in orders)
    detail = client.get(f"/api/v3/orders/{paid['id']}", headers=h).json()["order"]
    assert detail["package_name"] == "下單鏈路套餐"
    assert len(detail["payments"]) == 1


def test_purchase_idempotent_client_ref(client):
    h = _register_and_login(client, "pkg-idem@example.com")
    pkg = _create_package(client, name="冪等套餐", stock=5)
    before = _stock_left(client, pkg["id"])
    first = _purchase(client, h, pkg["id"], client_ref="idem-001")
    second = _purchase(client, h, pkg["id"], client_ref="idem-001")
    assert first["order"]["id"] == second["order"]["id"]
    assert _stock_left(client, pkg["id"]) == before - 1  # reserved once


def test_package_voucher_redeem_via_scan(client):
    h = _register_and_login(client, "pkg-redeem@example.com")
    pkg = _create_package(client, name="核銷套餐", stock=5)
    _, paid = _buy_paid(client, h, pkg["id"], method="card")
    wid = paid["wallet_item_id"]
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=h)
    assert qr.status_code == 200, qr.text
    vh = _vendor_headers(client)
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 200, r.text
    wallet = client.get("/api/v3/get-favorite-coupons-list",
                        params={"status": 2}, headers=h).json()
    assert any(i["id"] == wid for i in wallet["items"])
    # P1-C: paid package voucher earns per-HKD-spent points (150 paid
    # -> +150), no longer the old flat +20.
    assert client.get("/api/v3/points", headers=h).json()["balance"] == 150


# ---------- refunds ----------
def test_refund_unused_voids_voucher_and_blocks_redeem(client):
    h = _register_and_login(client, "pkg-refund@example.com")
    pkg = _create_package(client, name="退款套餐", stock=5)
    _, paid = _buy_paid(client, h, pkg["id"])
    wid = paid["wallet_item_id"]
    before = _stock_left(client, pkg["id"])
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=h)
    assert qr.status_code == 200
    r = client.post(f"/api/v3/orders/{paid['id']}/refund",
                    json={"reason": "行程有變"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["refund"]["status"] == "refunded"
    assert r.json()["refund"]["gateway_ref"].startswith("re_test_")
    assert r.json()["order"]["refund_status"] == "refunded"
    assert r.json()["order"]["settlement_status"] == "void"
    wallet = client.get("/api/v3/get-favorite-coupons-list",
                        params={"status": 3}, headers=h).json()
    assert any(i["id"] == wid for i in wallet["items"])  # 券已作廢
    assert _stock_left(client, pkg["id"]) == before + 1
    # Refunded voucher can never be redeemed.
    vh = _vendor_headers(client)
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 400
    # Second refund is rejected.
    r = client.post(f"/api/v3/orders/{paid['id']}/refund",
                    json={"reason": "再退一次"}, headers=h)
    assert r.status_code == 400


def test_refund_via_package_path(client):
    h = _register_and_login(client, "pkg-refund2@example.com")
    pkg = _create_package(client, name="套餐路徑退款", stock=5)
    _, paid = _buy_paid(client, h, pkg["id"])
    r = client.post(f"/api/v3/packages/{pkg['id']}/refund",
                    json={"reason": "不想去了"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["order"]["id"] == paid["id"]
    assert r.json()["order"]["refund_status"] == "refunded"


def test_non_refundable_package_refund_blocked(client):
    h = _register_and_login(client, "pkg-norefund@example.com")
    pkgs = client.get("/api/v3/packages").json()["packages"]
    special = [p for p in pkgs if p["name"] == "麻辣火鍋四人特價套餐"][0]
    assert special["refund_policy"] == "non_refundable"
    _, paid = _buy_paid(client, h, special["id"])
    r = client.post(f"/api/v3/orders/{paid['id']}/refund",
                    json={"reason": "想退"}, headers=h)
    assert r.status_code == 400
    assert "不設退款" in r.text


def test_refund_used_voucher_blocked(client):
    h = _register_and_login(client, "pkg-usedrefund@example.com")
    pkg = _create_package(client, name="已用退款攔截", stock=5)
    _, paid = _buy_paid(client, h, pkg["id"])
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": paid["wallet_item_id"]},
                     headers=h).json()
    vh = _vendor_headers(client)
    assert client.post("/api/v3/new-scan-coupon",
                       json={"code": qr["code"]}, headers=vh).status_code == 200
    r = client.post(f"/api/v3/orders/{paid['id']}/refund",
                    json={"reason": "已用還想退"}, headers=h)
    assert r.status_code == 400


# ---------- payment failure / timeout / retry / cancel ----------
def test_payment_failure_retry_then_success(client):
    h = _register_and_login(client, "pkg-retry@example.com")
    pkg = _create_package(client, name="重試套餐", stock=5)
    before = _stock_left(client, pkg["id"])
    bought = _purchase(client, h, pkg["id"], method="card")
    order = _pay(client, h, bought["order"]["id"], "failed")
    assert order["payment_status"] == "failed"
    assert _stock_left(client, pkg["id"]) == before  # stock released
    r = client.post(f"/api/v3/orders/{order['id']}/retry-payment",
                    json={"payment_method": "fps"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["order"]["payment_status"] == "pending"
    assert r.json()["order"]["payment_method"] == "fps"
    paid = _pay(client, h, order["id"], "succeeded")
    assert paid["payment_status"] == "paid"
    assert paid["wallet_item_id"] is not None
    detail = client.get(f"/api/v3/orders/{order['id']}",
                        headers=h).json()["order"]
    assert len(detail["payments"]) == 2  # failed attempt + retry attempt


def test_payment_timeout_and_cancel(client):
    h = _register_and_login(client, "pkg-timeout@example.com")
    pkg = _create_package(client, name="逾時取消套餐", stock=5)
    before = _stock_left(client, pkg["id"])
    bought = _purchase(client, h, pkg["id"])
    order = _pay(client, h, bought["order"]["id"], "timeout")
    assert order["payment_status"] == "timeout"
    assert _stock_left(client, pkg["id"]) == before
    # A timed-out order cannot be confirmed again, but can be cancelled? No:
    # only pending orders cancel; timeout orders retry instead.
    r = client.post(f"/api/v3/orders/{order['id']}/cancel", headers=h)
    assert r.status_code == 400

    bought2 = _purchase(client, h, pkg["id"], client_ref="cancel-001")
    assert _stock_left(client, pkg["id"]) == before - 1
    r = client.post(f"/api/v3/orders/{bought2['order']['id']}/cancel", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["order"]["payment_status"] == "cancelled"
    assert _stock_left(client, pkg["id"]) == before


def test_webhook_succeeded_issues_voucher(client):
    h = _register_and_login(client, "pkg-webhook@example.com")
    pkg = _create_package(client, name="Webhook 套餐", stock=5)
    bought = _purchase(client, h, pkg["id"], method="fps")
    # FIX-1: the pi_test_ id is no longer in the response; read it from
    # the DB (server-side only). Dev without a secret still accepts an
    # unsigned webhook; signed/non-dev paths are covered in
    # test_fix1_payment_gateway.py.
    from app.models_packages import Payment
    db = TestingSession()
    pay = (db.query(Payment)
           .filter(Payment.order_id == bought["order"]["id"]).first())
    ref = pay.gateway_ref
    db.close()
    assert ref.startswith("pi_test_")
    r = client.post("/api/v3/payments/webhook", json={
        "type": "payment_intent.succeeded",
        "data": {"object": {"id": ref}}})
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "succeeded"
    order = client.get(f"/api/v3/orders/{bought['order']['id']}",
                       headers=h).json()["order"]
    assert order["payment_status"] == "paid"
    assert order["wallet_item_id"] is not None


# ---------- expiry auto-refund ----------
def test_expired_package_auto_refund(client):
    h = _register_and_login(client, "pkg-expire@example.com")
    pkg = _create_package(client, name="過期自動退套餐", stock=5,
                          refund_policy="anytime")
    _, paid = _buy_paid(client, h, pkg["id"])
    # Expire the voucher by moving its coupon validity into the past.
    db = TestingSession()
    coupon = db.query(models.Coupon).filter(
        models.Coupon.package_id == pkg["id"]).first()
    coupon.valid_to = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    db.commit()
    db.close()
    ah = _admin_headers(client)
    r = client.post("/api/v3/orders/process-expired-refunds", headers=ah)
    assert r.status_code == 200, r.text
    assert r.json()["refunded"] >= 1
    order = client.get(f"/api/v3/orders/{paid['id']}",
                       headers=h).json()["order"]
    assert order["refund_status"] == "refunded"
    wallet = client.get("/api/v3/get-favorite-coupons-list",
                        params={"status": 3}, headers=h).json()
    assert any(i["id"] == paid["wallet_item_id"] for i in wallet["items"])


def test_expiry_only_manual_refund_blocked_then_auto_refunded(client):
    h = _register_and_login(client, "pkg-expireonly@example.com")
    pkg = _create_package(client, name="僅過期退套餐", stock=5,
                          refund_policy="expiry_only")
    _, paid = _buy_paid(client, h, pkg["id"])
    r = client.post(f"/api/v3/orders/{paid['id']}/refund",
                    json={"reason": "未過期想退"}, headers=h)
    assert r.status_code == 400
    db = TestingSession()
    coupon = db.query(models.Coupon).filter(
        models.Coupon.package_id == pkg["id"]).first()
    coupon.valid_to = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    db.commit()
    db.close()
    ah = _admin_headers(client)
    r = client.post("/api/v3/orders/process-expired-refunds", headers=ah)
    assert r.status_code == 200
    order = client.get(f"/api/v3/orders/{paid['id']}",
                       headers=h).json()["order"]
    assert order["refund_status"] == "refunded"


# ---------- free + paid vouchers coexist ----------
def test_free_coupon_and_package_voucher_coexist(client):
    h = _register_and_login(client, "pkg-coexist@example.com")
    r = client.post("/api/v3/new-favorite-coupon", params={"coupon_id": 2},
                    headers=h)
    assert r.status_code == 200, r.text
    pkg = _create_package(client, name="並行套餐", stock=5)
    _, paid = _buy_paid(client, h, pkg["id"])
    wallet = client.get("/api/v3/get-favorite-coupons-list",
                        params={"status": 1}, headers=h).json()
    sources = {i["id"]: i.get("source", "claim") for i in wallet["items"]}
    assert len(wallet["items"]) == 2
    assert sources[paid["wallet_item_id"]] == "purchase"
    assert any(s == "claim" for s in sources.values())


def test_multi_unit_refund_reconciles_only_on_gateway_success(client, monkeypatch):
    from app.routers import packages as service
    h = _register_and_login(client, "pkg-gateway@example.com")
    pid = _create_package(client, stock=2)["id"]
    body = {"quantity": 2, "client_ref": "two", "payment_method": "fps"}
    purchase = f"/api/v3/packages/{pid}/purchase"
    order = client.post(purchase, json=body, headers=h).json()["order"]
    oid = order["id"]
    assert client.post(purchase, json=body, headers=h).json()["order"]["id"] == oid
    assert _stock_left(client, pid) == 0
    url = f"/api/v3/orders/{oid}"
    paid = client.post(url + "/demo-payment", headers=h).json()["order"]
    assert paid["paid_amount"] == 300 and paid["quantity"] == 2
    with TestingSession() as db:
        coupon = db.query(models.Coupon).filter_by(package_id=pid).one()
        assert coupon.used_count == 0 and coupon.remaining == 0
        pay_ref = db.query(models.Payment).filter_by(order_id=oid).one().gateway_ref
    result = dict(gateway_ref="re_one", payment_intent=pay_ref, amount=300,
                  currency="hkd", status="pending")
    keys = []
    def pending(ref, amount, *, request_key):
        keys.append(request_key)
        return result
    monkeypatch.setattr(service, "refund_payment", pending)
    monkeypatch.setattr(service, "retrieve_refund", lambda ref: result)
    assert client.post(url + "/refund", json={}, headers=h).json()["refund"]["status"] == "requested"
    assert _stock_left(client, pid) == 0
    assert client.get(url, headers=h).json()["order"]["refund_amount"] == 0
    assert client.post("/api/v3/store-scaned-qr-values", headers=h,
                       params={"wallet_item_id": paid["wallet_item_id"]}).status_code == 400
    result["status"] = "failed"
    assert client.get(url, headers=h).json()["order"]["refundable"]
    assert _stock_left(client, pid) == 0
    result["gateway_ref"], result["status"] = "re_two", "succeeded"
    retry = client.post(url + "/refund", json={}, headers=h).json()["order"]
    assert retry["refund_amount"] == 300 and retry["settlement_status"] == "void"
    assert len(set(keys)) == 2
    assert _stock_left(client, pid) == 2
    assert client.post(url + "/refund", json={}, headers=h).status_code == 400
    with TestingSession() as db:
        coupon = db.query(models.Coupon).filter_by(package_id=pid).one()
        assert (coupon.used_count, coupon.remaining) == (0, 2)


def test_legacy_paid_missing_voucher_and_expired_wallet_reconcile(client):
    h = _register_and_login(client, "pkg-history@example.com")
    pid = _create_package(client, stock=3)["id"]
    _, order = _buy_paid(client, h, pid)
    with TestingSession() as db:
        db.delete(db.get(models.WalletItem, order["wallet_item_id"]))
        db.commit()
    wallet = "/api/v3/get-favorite-coupons-list"
    assert len(client.get(wallet, headers=h).json()["items"]) == 1
    assert len(client.get(wallet, headers=h).json()["items"]) == 1
    with TestingSession() as db:
        item = db.query(models.WalletItem).filter_by(order_id=order["id"]).one()
        item.coupon.valid_to = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        item.status = 3
        db.commit()
    for _ in range(2):
        items = client.get(wallet, headers=h).json()["items"]
        assert len(items) == 1 and items[0]["refund_status"] == "refunded"
        assert _stock_left(client, pid) == 3


def test_admin_refund_uses_same_voucher_and_records_reason(client):
    h = _register_and_login(client, "pkg-admin-refund@example.com")
    pid = _create_package(client, stock=2)["id"]
    _, paid = _buy_paid(client, h, pid)
    ah = _admin_headers(client)
    path = f"/api/v3/admin/orders/{paid['id']}"
    assert client.get(path, headers=ah).json()["order"]["admin_refundable"]
    assert client.post(path + "/refund", json={"reason": "  "}, headers=ah).status_code == 400
    assert client.post(path + "/refund", json={"reason": "訂單更正"}, headers=h).status_code == 403
    assert client.post(path + "/refund", json={"reason": "訂單更正"}, headers=ah).json()["order"]["refund_amount"] == 150
    assert not client.get(path, headers=ah).json()["order"]["admin_refundable"]
    assert _stock_left(client, pid) == 2
    from app.models_line_b import AdminAuditLog
    with TestingSession() as db:
        audit = db.query(AdminAuditLog).filter_by(action="request_refund", target_id=paid["id"]).one()
        assert audit.detail == "訂單更正"


def test_multi_unit_retry_and_refund_restore_purchase_limit(client):
    h = _register_and_login(client, "pkg-quantity-limit@example.com")
    pid = _create_package(client, stock=5)["id"]
    with TestingSession() as db:
        db.get(models.Package, pid).usage_rules = '{"per_user_limit":2}'
        db.commit()
    def details():
        return client.get("/api/v3/package-details", params={"package_id": pid}, headers=h).json()["package"]
    def purchase(ref):
        return client.post(f"/api/v3/packages/{pid}/purchase", headers=h,
                           json={"quantity": 2, "payment_method": "fps", "client_ref": ref})
    oid = purchase("multi").json()["order"]["id"]
    assert details()["sold_count"] == 0 and details()["limit_remaining"] == 0
    assert purchase("extra").status_code == 400
    _pay(client, h, oid, "failed")
    assert details()["stock_left"] == 5 and details()["limit_remaining"] == 2
    path = f"/api/v3/orders/{oid}"
    assert client.post(path + "/retry-payment", headers=h, json={"payment_method": "card"}).status_code == 200
    assert details()["stock_left"] == 3
    _pay(client, h, oid)
    assert details()["sold_count"] == 2
    assert client.post(path + "/refund", headers=h, json={}).status_code == 200
    pkg = details()
    assert (pkg["stock_left"], pkg["sold_count"], pkg["limit_remaining"]) == (5, 0, 2)


def test_refund_response_lost_resumes_same_request(client, monkeypatch):
    from app.routers import packages as service
    h = _register_and_login(client, "pkg-refund-resume@example.com")
    pid = _create_package(client, stock=2)["id"]
    _, paid = _buy_paid(client, h, pid)
    path = f"/api/v3/orders/{paid['id']}"
    send = service.refund_payment
    calls = []

    def lost_once(ref, amount, *, request_key):
        result = send(ref, amount, request_key=request_key)
        calls.append((request_key, result["gateway_ref"]))
        if len(calls) == 1:
            raise service.GatewayError("response lost")
        return result

    monkeypatch.setattr(service, "refund_payment", lost_once)
    assert client.post(path + "/refund", json={}, headers=h).status_code == 502
    assert _stock_left(client, pid) == 1
    order = client.get(path, headers=h).json()["order"]
    assert order["refund_amount"] == 150 and order["refund_status"] == "refunded"
    assert len(calls) == 2 and calls[0] == calls[1]
    assert _stock_left(client, pid) == 2


def test_refund_does_not_resell_units_promised_by_an_older_retry(client):
    h = _register_and_login(client, "pkg-old-oversale@example.com")
    pid = _create_package(client, stock=3)["id"]
    _, one = _buy_paid(client, h, pid)
    two = client.post(f"/api/v3/packages/{pid}/purchase", headers=h,
                      json={"quantity": 2, "payment_method": "fps"}).json()["order"]
    _pay(client, h, two["id"])
    # Two existing customers hold three units against effective capacity two.
    with TestingSession() as db:
        db.get(models.Package, pid).stock_total = 2
        db.commit()
    assert client.post(f"/api/v3/orders/{one['id']}/refund", json={}, headers=h).status_code == 200
    assert _stock_left(client, pid) == 0
    assert client.post(f"/api/v3/orders/{two['id']}/refund", json={}, headers=h).status_code == 200
    assert _stock_left(client, pid) == 2
