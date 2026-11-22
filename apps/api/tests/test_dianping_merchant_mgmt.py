"""Dianping block 1 — merchant management (GAP §1: 1.1–1.6).

P0-1 approval creates a shop; P0-2 shop/branch creation + real
coupon-branch assignment; P1 staff roles (owner / manager / cashier /
finance); P1 shop self-maintenance + merchant image upload; P1
merchant notifications; P2-pending application edit.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_dianping_merchant_mgmt.py -q
Own throwaway SQLite file under a fresh tempdir (never /tmp shared
names, never production data).
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"
_TMP = tempfile.mkdtemp(prefix="coupon_go_dianping_merchant_")
os.environ["UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["MERCHANT_FILE_DIR"] = os.path.join(_TMP, "merchant-private")

from app import models  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

TEST_DB = os.path.join(_TMP, "test.db")

engine = create_engine(f"sqlite:///{TEST_DB}", connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine)


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
    return _login(client, "admin@coupon-go.example")


def _finance_headers(client):
    return _login(client, "finance@coupon-go.example")


def _uid(client, headers):
    return client.get("/api/v3/get-profile", headers=headers).json()["id"]


def _submit_application(client, headers, shop_name):
    r = client.post("/api/v3/admin/merchant-applications", json={
        "shop_name": shop_name, "contact_name": "陳老闆",
        "phone": "91234567", "address": "旺角花園街 1 號",
        "business_license": "BR-12345678", "id_card": "HKID-XXXX",
        "shop_photos": [], "industry_license": ""}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["application"]["id"]


def _review_application(client, app_id, approve, reason=""):
    if approve:
        with TestingSession() as db:
            application = db.get(models.MerchantApplication, app_id)
            email = db.get(models.User, application.user_id).email
        owner = _login(client, email)
        signed = client.post(f"/api/v3/merchant-applications/{app_id}/sign-contract",
                             json={}, headers=owner)
        assert signed.status_code == 201, signed.text
        simulated = client.post(f"/api/v3/admin/merchant-applications/{app_id}/stripe-onboarding",
                                json={"stripe_account_id": f"acct_test_application_{app_id}",
                                      "charges_enabled": True, "payouts_enabled": True},
                                headers=_admin_headers(client))
        assert simulated.status_code == 200, simulated.text
    r = client.post(
        f"/api/v3/admin/merchant-applications/{app_id}/review",
        json={"approve": approve, "reject_reason": reason},
        headers=_admin_headers(client))
    assert r.status_code == 200, r.text
    return r.json()


def _notifications(client, headers):
    return client.get("/api/v3/get-notification-list",
                      headers=headers).json()["notifications"]


def _make_coupon(client, shop_id, name="測試券"):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    body = {"shop_id": shop_id, "coupon_type": "Discount",
            "coupon_name": name, "percentage": 90,
            "valid_from": (now - timedelta(days=1)).isoformat(),
            "valid_to": (now + timedelta(days=30)).isoformat(),
            "total": 50}
    r = client.post("/api/v3/coupon-previewed", json=body,
                    headers=_vendor_headers(client))
    assert r.status_code == 200, r.text
    coupon_id = r.json()["coupon"]["id"]
    r = client.post(f"/api/v3/vendor/coupons/{coupon_id}/approve",
                    params={"approve": True}, headers=_admin_headers(client))
    assert r.status_code == 200, r.text
    return coupon_id


def _claim_and_present(client, email, coupon_id):
    h = _register_and_login(client, email)
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": coupon_id}, headers=h)
    assert r.status_code == 200, r.text
    wid = client.get("/api/v3/get-favorite-coupons-list",
                     headers=h).json()["items"][0]["id"]
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=h)
    assert qr.status_code == 200, qr.text
    return h, wid, qr.json()["code"]


# ---------- 1.1 approval creates a shop (P0-1) ----------
def test_approve_application_creates_shop_and_notifies(client):
    h = _register_and_login(client, "boss1@example.com")
    app_id = _submit_application(client, h, "阿婆豆腐花")
    out = _review_application(client, app_id, True)
    assert out["application"]["status"] == "approved"
    shop = out.get("shop")
    assert shop is not None, "批准後回應應帶出新建門店"
    assert shop["shop_name"] == "阿婆豆腐花"
    assert shop["address"] == "旺角花園街 1 號"
    assert shop["phone"] == "91234567"
    assert shop.get("is_approved") is True
    # applicant is now a vendor and owns the shop
    assert client.get("/api/v3/get-profile", headers=h).json()["role"] == "vendor"
    mine = client.get("/api/v3/get-company-list", headers=h).json()["companies"]
    assert [s["shop_name"] for s in mine] == ["阿婆豆腐花"]
    notes = _notifications(client, h)
    assert any("通過" in n["title"] for n in notes), notes


def test_reject_application_creates_no_shop_but_notifies(client):
    h = _register_and_login(client, "boss2@example.com")
    app_id = _submit_application(client, h, "無牌小食店")
    out = _review_application(client, app_id, False, "營業執照不清晰")
    assert out["application"]["status"] == "rejected"
    assert out.get("shop") is None
    # rejected applicant stays a customer with no shop
    assert client.get("/api/v3/get-profile", headers=h).json()["role"] == "customer"
    shops = client.get("/api/v3/admin/shops",
                       headers=_admin_headers(client)).json()["shops"]
    assert all(s["shop_name"] != "無牌小食店" for s in shops)
    notes = _notifications(client, h)
    assert any("拒絕" in n["title"] and "營業執照不清晰" in n["body"]
               for n in notes), notes


def test_second_approved_application_creates_second_shop(client):
    h = _register_and_login(client, "boss3@example.com")
    _review_application(client, _submit_application(client, h, "第一分店"), True)
    _review_application(client, _submit_application(client, h, "第二分店"), True)
    mine = client.get("/api/v3/get-company-list", headers=h).json()["companies"]
    assert sorted(s["shop_name"] for s in mine) == ["第一分店", "第二分店"]


# ---------- 1.2 shops / branches / real assign_branches (P0-2) ----------
def test_create_shop_and_branches(client):
    vh = _vendor_headers(client)
    r = client.post("/api/v3/merchant/shops", json={
        "name": "新開茶餐廳", "address": "深水埗欽州街 9 號",
        "phone": "92345678", "description": "平民茶餐廳"},
        headers=vh)
    assert r.status_code == 201, r.text
    shop_id = r.json()["shop"]["id"]
    assert r.json()["shop"]["is_approved"] is True
    r = client.post(f"/api/v3/merchant/shops/{shop_id}/branches", json={
        "name": "新開茶餐廳 · 太子分店", "address": "太子道 88 號",
        "phone": "92345679"}, headers=vh)
    assert r.status_code == 201, r.text
    branch_id = r.json()["branch"]["id"]
    r = client.get(f"/api/v3/merchant/shops/{shop_id}/branches", headers=vh)
    assert r.status_code == 200, r.text
    assert [b["id"] for b in r.json()["branches"]] == [branch_id]
    # a stranger cannot add branches to someone else's shop
    other = _register_and_login(client, "stranger@example.com")
    r = client.post(f"/api/v3/merchant/shops/{shop_id}/branches",
                    json={"name": "冒牌分店"}, headers=other)
    assert r.status_code == 403, r.text


def test_assign_branches_persists_and_enforces_at_redeem(client):
    vh = _vendor_headers(client)
    r = client.post("/api/v3/merchant/shops", json={"name": "分店測試店"},
                    headers=vh)
    shop_id = r.json()["shop"]["id"]
    b1 = client.post(f"/api/v3/merchant/shops/{shop_id}/branches",
                     json={"name": "分店 A"}, headers=vh).json()["branch"]["id"]
    b2 = client.post(f"/api/v3/merchant/shops/{shop_id}/branches",
                     json={"name": "分店 B"}, headers=vh).json()["branch"]["id"]
    coupon_id = _make_coupon(client, shop_id, "分店限定券")
    # assign only branch A — must persist, not just validate
    r = client.post("/api/v3/assign-branches",
                    json={"coupon_id": coupon_id, "branch_ids": [b1]},
                    headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["assigned_branch_ids"] == [b1]
    detail = client.get("/api/v3/coupon-details-new",
                        params={"coupon_id": coupon_id}).json()
    assert detail["coupon"]["branch_ids"] == [b1]
    # re-assign replaces the old set; clearing means "all branches"
    r = client.post("/api/v3/assign-branches",
                    json={"coupon_id": coupon_id, "branch_ids": [b1, b2]},
                    headers=vh)
    assert r.json()["assigned_branch_ids"] == [b1, b2]
    r = client.post("/api/v3/assign-branches",
                    json={"coupon_id": coupon_id, "branch_ids": []},
                    headers=vh)
    assert r.json()["assigned_branch_ids"] == []
    # Invalid branches must not silently clear or broaden the scope.
    r = client.post("/api/v3/assign-branches",
                    json={"coupon_id": coupon_id, "branch_ids": [b1, 999999]},
                    headers=vh)
    assert r.status_code == 400
    detail = client.get("/api/v3/coupon-details-new", params={"coupon_id": coupon_id}).json()
    assert detail["coupon"]["branch_ids"] == []
    r = client.post("/api/v3/assign-branches", json={"coupon_id": coupon_id, "branch_ids": [b1]}, headers=vh)
    assert r.status_code == 200 and r.json()["assigned_branch_ids"] == [b1]
    r = client.post("/api/v3/assign-branches", json={"coupon_id": coupon_id, "branch_ids": [999999]}, headers=vh)
    assert r.status_code == 400
    detail = client.get("/api/v3/coupon-details-new", params={"coupon_id": coupon_id}).json()
    assert detail["coupon"]["branch_ids"] == [b1]
    # branch-scoped redemption: B is out of scope, A is in scope
    _h, _wid, code = _claim_and_present(client, "branchbuyer@example.com",
                                        coupon_id)
    r = client.post("/api/v3/new-scan-coupon/prepare", json={"code": code}, headers=vh)
    assert r.status_code == 200 and r.json()["can_redeem"] is False
    assert r.json()["branch_selection_required"] is True
    assert r.json()["allowed_branches"] == [{"id": b1, "name": "分店 A", "address": ""}]
    r = client.post("/api/v3/new-scan-coupon", json={"code": code}, headers=vh)
    assert r.status_code == 400 and "選擇分店" in r.json()["detail"]
    r = client.post("/api/v3/new-scan-coupon/prepare",
                    json={"code": code, "branch_id": "invalid"}, headers=vh)
    assert r.status_code == 200 and "分店資料不正確" in r.json()["reason"]
    r = client.post("/api/v3/new-scan-coupon/prepare",
                    json={"code": code, "branch_id": b2}, headers=vh)
    assert r.status_code == 200, r.text
    assert r.json()["can_redeem"] is False
    assert "分店" in r.json()["reason"]
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": code, "branch_id": b2}, headers=vh)
    assert r.status_code == 400, r.text
    r = client.post("/api/v3/new-scan-coupon/prepare",
                    json={"code": code, "branch_id": b1}, headers=vh)
    assert r.status_code == 200 and r.json()["can_redeem"] is True
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": code, "branch_id": b1,
                          "prepare_token": r.json()["prepare_token"]}, headers=vh)
    assert r.status_code == 200, r.text
    with TestingSession() as db:
        assert db.get(models.Redemption, r.json()["redemption_id"]).branch_id == b1


def test_unrestricted_redemption_with_or_without_branches(client):
    vendor = _vendor_headers(client)
    for name, make_branch in [("無分店店舖", False), ("有分店店舖", True)]:
        shop = client.post("/api/v3/merchant/shops", json={"name": name}, headers=vendor)
        assert shop.status_code == 201, shop.text
        shop_id = shop.json()["shop"]["id"]
        if make_branch:
            response = client.post(f"/api/v3/merchant/shops/{shop_id}/branches",
                                   json={"name": "普通分店"}, headers=vendor)
            assert response.status_code == 201, response.text
        coupon_id = _make_coupon(client, shop_id, name + "不限分店券")
        _, _, code = _claim_and_present(client, f"buyer-{shop_id}@example.com", coupon_id)
        prepared = client.post("/api/v3/new-scan-coupon/prepare", json={"code": code}, headers=vendor)
        assert prepared.status_code == 200 and prepared.json()["can_redeem"] is True
        assert prepared.json()["branch_selection_required"] is False
        result = client.post("/api/v3/new-scan-coupon", json={"code": code}, headers=vendor)
        assert result.status_code == 200, result.text


def test_coupon_creation_persists_and_validates_branch_scope(client):
    vendor = _vendor_headers(client)
    branch = client.post("/api/v3/merchant/shops/1/branches",
                         json={"name": "新券分店"}, headers=vendor).json()["branch"]["id"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    body = {"shop_id": 1, "coupon_type": "Discount", "coupon_name": "限定新券",
            "percentage": 90, "valid_from": (now - timedelta(days=1)).isoformat(),
            "valid_to": (now + timedelta(days=30)).isoformat(), "total": 50,
            "branch_ids": [branch]}
    created = client.post("/api/v3/coupon-previewed", json=body, headers=vendor)
    assert created.status_code == 200 and created.json()["coupon"]["branch_ids"] == [branch]
    invalid = dict(body, branch_ids=[branch, 999999])
    assert client.post("/api/v3/coupon-previewed", json=invalid, headers=vendor).status_code == 400
    assert client.post("/api/v3/add-coupon-preview", json=invalid, headers=vendor).status_code == 400
    bulk = client.post("/api/v3/vendor/coupons/bulk",
                       json={"coupons": [body, invalid]}, headers=vendor)
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["created"][0]["branch_ids"] == [branch]
    assert [failure["index"] for failure in bulk.json()["failed"]] == [1]


# ---------- 1.3 staff roles (P1) ----------
def _invite_as(client, shop_id, email, role):
    h = _register_and_login(client, email)
    uid = _uid(client, h)
    r = client.post(f"/api/v3/admin/shops/{shop_id}/members",
                    json={"user_id": uid, "role": role},
                    headers=_vendor_headers(client))
    assert r.status_code == 201, r.text
    assert r.json()["member"]["role"] == role
    member_id = r.json()["member"]["id"]
    r = client.post(
        f"/api/v3/admin/shops/{shop_id}/members/{member_id}/confirm",
        headers=h)
    assert r.status_code == 200, r.text
    return h


def test_invite_role_is_validated(client):
    h = _register_and_login(client, "badrole@example.com")
    uid = _uid(client, h)
    r = client.post("/api/v3/admin/shops/1/members",
                    json={"user_id": uid, "role": "superuser"},
                    headers=_vendor_headers(client))
    assert r.status_code == 400, r.text


def test_paid_order_prevents_changing_assigned_branches(client):
    vendor = _vendor_headers(client)
    branch = client.post("/api/v3/merchant/shops/1/branches", json={"name": "付費券指定店"}, headers=vendor)
    assert branch.status_code == 201, branch.text
    branch_id = branch.json()["branch"]["id"]
    coupon_id = _make_coupon(client, 1, "已下單分店券")
    endpoint = "/api/v3/assign-branches"
    payload = {"coupon_id": coupon_id, "branch_ids": [branch_id]}
    assert client.post(endpoint, json=payload, headers=vendor).status_code == 200
    with TestingSession() as db:
        order = models.Order(user_id=_uid(client, vendor), order_type="coupon", coupon_id=coupon_id,
                             quantity=1, amount=10, paid_amount=0, payment_status="pending",
                             refund_status="none", settlement_status="pending")
        db.add(order)
        db.commit()
        order_id = order.id
    assert client.post(endpoint, json=payload, headers=vendor).status_code == 200
    denied = client.post(endpoint, json={"coupon_id": coupon_id, "branch_ids": []}, headers=vendor)
    assert denied.status_code == 409
    detail = client.get("/api/v3/coupon-details-new", params={"coupon_id": coupon_id}).json()
    assert detail["coupon"]["branch_ids"] == [branch_id]
    with TestingSession() as db:
        db.get(models.Order, order_id).payment_status = "cancelled"
        db.commit()
    assert client.post(endpoint, json={"coupon_id": coupon_id, "branch_ids": []}, headers=vendor).status_code == 200


def test_cashier_can_redeem_but_cannot_see_settlements(client):
    cashier = _invite_as(client, 1, "cashier1@example.com", "cashier")
    _h, _wid, code = _claim_and_present(client, "cashierbuyer@example.com", 2)
    r = client.post("/api/v3/new-scan-coupon/prepare",
                    json={"code": code}, headers=cashier)
    assert r.status_code == 200, r.text
    assert r.json()["can_redeem"] is True, r.text
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": code}, headers=cashier)
    assert r.status_code == 200, r.text
    # cashier must NOT see settlement money
    r = client.get("/api/v3/merchant/shops/1/settlements", headers=cashier)
    assert r.status_code == 403, r.text
    r = client.get("/api/v3/merchant/settlements", headers=cashier)
    assert r.json()["settlements"] == []
    r = client.get("/api/v3/merchant/shops/1/invoices", headers=cashier)
    assert r.status_code == 403, r.text
    r = client.get("/api/v3/merchant/connect/payouts/active", params={"shop_id": 1}, headers=cashier)
    assert r.status_code == 403, r.text


def test_manager_can_manage_coupons_but_not_settlements(client):
    manager = _invite_as(client, 1, "manager1@example.com", "manager")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    r = client.post("/api/v3/coupon-previewed", json={
        "shop_id": 1, "coupon_type": "Discount", "coupon_name": "店長建的券",
        "percentage": 90, "sale_price": 12.5,
        "valid_from": (now - timedelta(days=1)).isoformat(),
        "valid_to": (now + timedelta(days=30)).isoformat(),
        "total": 10}, headers=manager)
    assert r.status_code == 200, r.text
    coupon_id = r.json()["coupon"]["id"]
    with TestingSession() as db:
        coupon = db.get(models.Coupon, coupon_id)
        assert coupon.vendor_id == db.get(models.Shop, 1).vendor_id
        assert float(coupon.sale_price) == 12.5
    edited = client.patch(f"/api/v3/vendor/coupons/{coupon_id}",
                          json={"coupon_name": "店長更新的券"}, headers=manager)
    assert edited.status_code == 200, edited.text
    uploaded = client.post("/api/v3/uploads/merchant-images", headers=manager,
                           files={"files": ("coupon.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    assert uploaded.status_code == 201, uploaded.text
    r = client.get("/api/v3/merchant/shops/1/settlements", headers=manager)
    assert r.status_code == 403, r.text


def test_finance_member_reads_settlements_but_cannot_redeem(client):
    finance = _invite_as(client, 1, "shopfinance@example.com", "finance")
    r = client.get("/api/v3/merchant/shops/1/settlements", headers=finance)
    assert r.status_code == 200, r.text
    for path in ("/api/v3/merchant/connect/balance", "/api/v3/merchant/connect/payouts/active"):
        assert client.get(path, params={"shop_id": 1}, headers=finance).status_code == 200
        assert client.get(path, params={"shop_id": 2}, headers=finance).status_code == 403
    _h, _wid, code = _claim_and_present(client, "finbuyer@example.com", 2)
    r = client.post("/api/v3/new-scan-coupon/prepare",
                    json={"code": code}, headers=finance)
    assert r.status_code == 200, r.text
    assert r.json()["can_redeem"] is False
    assert "權限" in r.json()["reason"]
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": code}, headers=finance)
    assert r.status_code == 403, r.text


def test_legacy_merchant_admin_keeps_full_access(client):
    staff = _invite_as(client, 1, "legacy1@example.com", "merchant-admin")
    r = client.get("/api/v3/merchant/shops/1/settlements", headers=staff)
    assert r.status_code == 200, r.text
    members = client.get("/api/v3/admin/shops/1/members",
                         headers=_vendor_headers(client)).json()["members"]
    mine = [m for m in members if m["email"] == "legacy1@example.com"][0]
    assert "view_settlement" in mine["permissions"]
    assert "redeem" in mine["permissions"]


def test_owner_can_change_member_role(client):
    staff = _invite_as(client, 1, "rolechange@example.com", "cashier")
    uid = _uid(client, staff)
    r = client.patch(f"/api/v3/admin/shops/1/members/{uid}",
                     json={"role": "manager"},
                     headers=_vendor_headers(client))
    assert r.status_code == 200, r.text
    assert r.json()["member"]["role"] == "manager"
    assert "manage_coupons" in r.json()["member"]["permissions"]
    r = client.get("/api/v3/merchant/shops/1/settlements", headers=staff)
    assert r.status_code == 403, r.text  # manager still cannot see money
    r = client.patch(f"/api/v3/admin/shops/1/members/{uid}",
                     json={"role": "boss"}, headers=_vendor_headers(client))
    assert r.status_code == 400, r.text


# ---------- 1.4 shop self-maintenance (P1) ----------
def test_patch_shop_profile(client):
    vh = _vendor_headers(client)
    r = client.patch("/api/v3/merchant/shops/1", json={
        "business_hours": {"mon_fri": "11:00-22:00", "sat_sun": "10:00-23:00"},
        "gallery": ["/uploads/merchant-images/a.png",
                    "/uploads/merchant-images/b.png"],
        "announcement": "新店開幕，全場 9 折"},
        headers=vh)
    assert r.status_code == 200, r.text
    shop = r.json()["shop"]
    assert shop["business_hours"] == {"mon_fri": "11:00-22:00",
                                      "sat_sun": "10:00-23:00"}
    assert shop["gallery"] == ["/uploads/merchant-images/a.png",
                               "/uploads/merchant-images/b.png"]
    assert shop["announcement"] == "新店開幕，全場 9 折"
    detail = client.get("/api/v3/shop-details",
                        params={"shop_id": 1}).json()
    assert detail["shop"]["announcement"] == "新店開幕，全場 9 折"
    # gallery must be a list; strangers cannot edit
    r = client.patch("/api/v3/merchant/shops/1",
                     json={"gallery": "not-a-list"}, headers=vh)
    assert r.status_code == 400, r.text
    other = _register_and_login(client, "shopstranger@example.com")
    r = client.patch("/api/v3/merchant/shops/1",
                     json={"announcement": "冒牌公告"}, headers=other)
    assert r.status_code == 403, r.text


def test_merchant_image_upload(client):
    vh = _vendor_headers(client)
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    r = client.post("/api/v3/uploads/merchant-images",
                    files={"files": ("shop.png", png, "image/png")},
                    headers=vh)
    assert r.status_code == 201, r.text
    assert r.json()["images"][0].startswith("/uploads/merchant-images/")
    r = client.post("/api/v3/uploads/merchant-images",
                    files={"files": ("evil.txt", b"hello", "text/plain")},
                    headers=vh)
    assert r.status_code == 400, r.text


# ---------- 1.5 merchant notifications (P1) ----------
def test_coupon_approval_notifies_vendor(client):
    coupon_id = _make_coupon(client, 1, "通知測試券")
    notes = _notifications(client, _vendor_headers(client))
    assert any(n.get("target_coupon_id") == coupon_id for n in notes), notes


def test_settlement_generation_and_review_notify_vendor(client):
    _h, wid, code = _claim_and_present(client, "notifybuyer@example.com", 2)
    r = client.post("/api/v3/new-scan-coupon", json={"code": code},
                    headers=_vendor_headers(client))
    assert r.status_code == 200, r.text
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    r = client.post("/api/v3/admin/settlements/generate", json={
        "shop_id": 1, "period": period, "commission_rate": 0.1},
        headers=_finance_headers(client))
    assert r.status_code == 201, r.text
    notes = _notifications(client, _vendor_headers(client))
    assert any(n.get("target_shop_id") == 1 and "結算" in n["title"]
               for n in notes), notes
    r = client.post("/api/v3/reviews", json={
        "wallet_item_id": wid, "taste": 5, "ambience": 5, "service": 5,
        "content": "好食"}, headers=_h)
    assert r.status_code == 200, r.text
    notes = _notifications(client, _vendor_headers(client))
    assert any(n.get("target_shop_id") == 1 and "評價" in n["title"]
               for n in notes), notes


# ---------- 1.6 pending application edit (P2 in §1) ----------
def test_patch_pending_application(client):
    h = _register_and_login(client, "boss4@example.com")
    app_id = _submit_application(client, h, "舊店名")
    r = client.patch(f"/api/v3/admin/merchant-applications/{app_id}",
                     json={"shop_name": "新店名", "phone": "98765432"},
                     headers=_admin_headers(client))
    assert r.status_code == 200, r.text
    assert r.json()["application"]["shop_name"] == "新店名"
    assert r.json()["application"]["phone"] == "98765432"
    _review_application(client, app_id, True)
    r = client.patch(f"/api/v3/admin/merchant-applications/{app_id}",
                     json={"shop_name": "再改"}, headers=_admin_headers(client))
    assert r.status_code == 400, r.text
    # only admin may edit
    app_id2 = _submit_application(client, h, "另一家店")
    r = client.patch(f"/api/v3/admin/merchant-applications/{app_id2}",
                     json={"shop_name": "偷改"}, headers=h)
    assert r.status_code == 403, r.text


def test_signed_contract_keeps_application_shop_name(client):
    owner = _register_and_login(client, "signed-name@example.com")
    app_id = _submit_application(client, owner, "簽約時的店名")
    signed = client.post(f"/api/v3/merchant-applications/{app_id}/sign-contract", json={}, headers=owner)
    assert signed.status_code == 201, signed.text
    admin = _admin_headers(client)
    response = client.patch(f"/api/v3/admin/merchant-applications/{app_id}",
                            json={"shop_name": "另一個店名"}, headers=admin)
    assert response.status_code == 409
    detail = client.get(f"/api/v3/admin/merchant-applications/{app_id}", headers=admin)
    assert detail.json()["application"]["shop_name"] == "簽約時的店名"
    contact = client.patch(f"/api/v3/admin/merchant-applications/{app_id}",
                           json={"phone": "91230000"}, headers=admin)
    assert contact.status_code == 200, contact.text
