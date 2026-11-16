"""Line F tests: admin v2 (settlements / reconciliation / invoices /
CMS / roles / shop members), review-dispute resolution, review uploads.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/ -v
Own throwaway SQLite file; never touches production data.
"""
import os
import sys
from datetime import datetime, timezone

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
from app.seed import seed  # noqa: E402

TEST_DB = "/tmp/coupon_go_test_line_f.db"

engine = create_engine(f"sqlite:///{TEST_DB}", connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine)

PERIOD = datetime.now(timezone.utc).strftime("%Y-%m")
# Seed cash coupons (id -> (shop_id, cash_amount)); one shop per test so
# the per-(shop, period) settlement uniqueness never collides.
CASH = {2: (1, 30), 5: (3, 100), 8: (4, 15), 17: (9, 150), 22: (11, 10),
        24: (12, 20)}


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


def _admin_headers(client):
    return _login(client, "admin@coupon-go.example")


def _finance_headers(client):
    return _login(client, "finance@coupon-go.example")


def _auditor_headers(client):
    return _login(client, "auditor@coupon-go.example")


def _support_headers(client):
    return _login(client, "support@coupon-go.example")


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
    db = TestingSession()
    red = (db.query(models.Redemption)
           .filter(models.Redemption.wallet_item_id == wid).first())
    db.close()
    assert red is not None
    return h, wid, red.id


def _generate(client, headers, shop_id, rate=0.10):
    r = client.post("/api/v3/admin/settlements/generate", json={
        "shop_id": shop_id, "period": PERIOD, "commission_rate": rate},
        headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["settlement"]


# ---------- settlements ----------
def test_settlement_generate_approve_pay_chain(client):
    shop_id, face = CASH[2]
    _redeem(client, "f-settle@example.com", 2)
    fh = _finance_headers(client)
    s = _generate(client, fh, shop_id)
    assert s["status"] == "pending"
    assert s["redemption_count"] == 1
    assert s["gross_amount"] == face
    assert s["commission"] == round(face * 0.10, 2)
    assert s["net_amount"] == round(face * 0.90, 2)
    assert len(s["items"]) == 1

    # duplicate generate for the same shop+period is rejected
    r = client.post("/api/v3/admin/settlements/generate", json={
        "shop_id": shop_id, "period": PERIOD}, headers=fh)
    assert r.status_code == 409

    # pay from pending is rejected; approve then pay works
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/pay", headers=fh)
    assert r.status_code == 400
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/approve", headers=fh)
    assert r.status_code == 200 and r.json()["settlement"]["status"] == "approved"
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/pay", headers=fh)
    assert r.status_code == 200 and r.json()["settlement"]["status"] == "paid"
    assert r.json()["settlement"]["paid_at"] is not None

    # audit log recorded the chain
    logs = client.get("/api/v3/admin/audit-logs",
                      headers=_admin_headers(client)).json()["logs"]
    actions = {l["action"] for l in logs}
    assert {"settlement.generate", "settlement.approve",
            "settlement.pay"} <= actions


def test_settlement_permissions_and_dispute_resolve(client):
    shop_id, face = CASH[5]
    _redeem(client, "f-dispute@example.com", 5)
    cust = _register_and_login(client, "f-cust@example.com")
    vendor = _vendor_headers(client)
    fh = _finance_headers(client)
    # customers and vendors cannot generate settlements
    for h in (cust, vendor):
        r = client.post("/api/v3/admin/settlements/generate", json={
            "shop_id": shop_id, "period": PERIOD}, headers=h)
        assert r.status_code == 403
    s = _generate(client, fh, shop_id)
    # dispute -> resolve (reopen) -> dispute -> resolve (approve)
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/dispute",
                    json={"reason": "金額有疑問"}, headers=fh)
    assert r.status_code == 200
    assert r.json()["settlement"]["status"] == "disputed"
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/resolve",
                    json={"decision": "reopen", "note": "重新核對"}, headers=fh)
    assert r.status_code == 200
    assert r.json()["settlement"]["status"] == "pending"
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/dispute",
                    json={"reason": "再查一次"}, headers=fh)
    assert r.status_code == 200
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/resolve",
                    json={"decision": "approve", "note": "核對無誤"}, headers=fh)
    assert r.status_code == 200
    assert r.json()["settlement"]["status"] == "approved"
    # resolve on a non-disputed settlement is rejected
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/resolve",
                    json={"decision": "approve"}, headers=fh)
    assert r.status_code == 400
    # auditor can read but not write settlements
    ah = _auditor_headers(client)
    r = client.get("/api/v3/admin/settlements", headers=ah)
    assert r.status_code == 200 and r.json()["total"] >= 2
    r = client.post(f"/api/v3/admin/settlements/{s['id']}/pay", headers=ah)
    assert r.status_code == 403


def test_merchant_settlement_read(client):
    vh = _vendor_headers(client)
    r = client.get("/api/v3/merchant/shops/1/settlements", headers=vh)
    assert r.status_code == 200 and r.json()["total"] >= 1
    cust = _register_and_login(client, "f-merch-cust@example.com")
    r = client.get("/api/v3/merchant/shops/1/settlements", headers=cust)
    assert r.status_code == 403


# ---------- reconciliation ----------
def test_reconciliation_run_and_resolve(client):
    shop_id, face = CASH[8]
    _redeem(client, "f-recon-a@example.com", 8)
    _redeem(client, "f-recon-b@example.com", 8)
    fh = _finance_headers(client)
    # no settlement yet: report flags both redemptions as missing lines
    r = client.post("/api/v3/admin/reconciliation/run",
                    json={"shop_id": shop_id, "period": PERIOD}, headers=fh)
    assert r.status_code == 201, r.text
    rep = r.json()["report"]
    assert rep["status"] == "no_settlement"
    assert rep["discrepancy_count"] == 2
    assert all(d["discrepancy_type"] == "missing_settlement_line"
               for d in rep["discrepancies"])
    # generate the settlement, re-run: clean
    _generate(client, fh, shop_id)
    r = client.post("/api/v3/admin/reconciliation/run",
                    json={"shop_id": shop_id, "period": PERIOD}, headers=fh)
    assert r.status_code == 201, r.text
    assert r.json()["report"]["status"] == "resolved"
    assert r.json()["report"]["discrepancy_count"] == 0
    # one more redemption after the settlement: one open discrepancy
    _redeem(client, "f-recon-c@example.com", 8)
    r = client.post("/api/v3/admin/reconciliation/run",
                    json={"shop_id": shop_id, "period": PERIOD}, headers=fh)
    rep = r.json()["report"]
    assert rep["status"] == "open" and rep["discrepancy_count"] == 1
    d = rep["discrepancies"][0]
    assert d["discrepancy_type"] == "missing_settlement_line"
    assert d["expected_amount"] == face
    r = client.post(
        f"/api/v3/admin/reconciliation/discrepancies/{d['id']}/resolve",
        json={"resolution": "已補入下期結算單"}, headers=fh)
    assert r.status_code == 200
    assert r.json()["discrepancy"]["status"] == "resolved"
    r = client.get(f"/api/v3/admin/reconciliation/reports/{rep['id']}",
                   headers=fh)
    assert r.json()["report"]["status"] == "resolved"
    # customer cannot run reconciliation
    cust = _register_and_login(client, "f-recon-cust@example.com")
    r = client.post("/api/v3/admin/reconciliation/run",
                    json={"shop_id": shop_id, "period": PERIOD}, headers=cust)
    assert r.status_code == 403


# ---------- invoices ----------
def test_invoice_chain_and_cap(client):
    shop_id, face = CASH[17]
    _redeem(client, "f-inv@example.com", 17)
    fh = _finance_headers(client)
    s = _generate(client, fh, shop_id)
    net = s["net_amount"]
    client.post(f"/api/v3/admin/settlements/{s['id']}/approve", headers=fh)
    vh = _vendor_headers(client)
    # vendor (shop owner) may request an invoice for their own shop
    r = client.post("/api/v3/admin/invoices", json={
        "shop_id": shop_id, "settlement_id": s["id"], "amount": net,
        "title": "測試公司", "tax_id": "12345678"}, headers=vh)
    assert r.status_code == 201, r.text
    inv = r.json()["invoice"]
    assert inv["status"] == "pending"
    # the settlement's net is now fully invoiced: any further amount fails
    r = client.post("/api/v3/admin/invoices", json={
        "shop_id": shop_id, "settlement_id": s["id"], "amount": 1,
        "title": "測試公司"}, headers=vh)
    assert r.status_code == 400
    # issue before approve is rejected; approve -> issue works
    r = client.post(f"/api/v3/admin/invoices/{inv['id']}/issue", headers=fh)
    assert r.status_code == 400
    r = client.post(f"/api/v3/admin/invoices/{inv['id']}/approve", headers=fh)
    assert r.status_code == 200
    assert r.json()["invoice"]["status"] == "approved"
    r = client.post(f"/api/v3/admin/invoices/{inv['id']}/issue", headers=fh)
    assert r.status_code == 200
    assert r.json()["invoice"]["status"] == "issued"
    assert r.json()["invoice"]["issued_at"] is not None
    # vendor cannot approve invoices; customer cannot request them
    r = client.post(f"/api/v3/admin/invoices/{inv['id']}/approve", headers=vh)
    assert r.status_code == 403
    cust = _register_and_login(client, "f-inv-cust@example.com")
    r = client.post("/api/v3/admin/invoices", json={
        "shop_id": shop_id, "amount": 1, "title": "x"}, headers=cust)
    assert r.status_code == 403
    # merchant-scoped invoice list works for the shop owner
    r = client.get(f"/api/v3/merchant/shops/{shop_id}/invoices", headers=vh)
    assert r.status_code == 200 and r.json()["total"] >= 1


# ---------- roles ----------
def test_role_change_and_permission_matrix(client):
    ah = _admin_headers(client)
    h = _register_and_login(client, "f-role@example.com")
    uid = client.get("/api/v3/get-profile", headers=h).json()["id"]
    # unknown role / self-change are rejected
    r = client.post(f"/api/v3/admin/users/{uid}/role",
                    json={"role": "superuser"}, headers=ah)
    assert r.status_code == 400
    admin_id = client.get("/api/v3/get-profile",
                          headers=ah).json()["id"]
    r = client.post(f"/api/v3/admin/users/{admin_id}/role",
                    json={"role": "finance"}, headers=ah)
    assert r.status_code == 400
    # finance cannot change roles
    fh = _finance_headers(client)
    r = client.post(f"/api/v3/admin/users/{uid}/role",
                    json={"role": "finance"}, headers=fh)
    assert r.status_code == 403
    # promote to finance: the user gains settlement read/write
    r = client.post(f"/api/v3/admin/users/{uid}/role",
                    json={"role": "finance"}, headers=ah)
    assert r.status_code == 200 and r.json()["user"]["role"] == "finance"
    r = client.get("/api/v3/admin/overview", headers=h)
    assert r.status_code == 200
    assert "settlements_by_status" in r.json()
    # ...but still cannot change roles or write banners
    r = client.post(f"/api/v3/admin/users/{uid}/role",
                    json={"role": "support"}, headers=h)
    assert r.status_code == 403
    r = client.post("/api/v3/admin/banners",
                    json={"title": "x"}, headers=h)
    assert r.status_code == 403
    # customer gets no overview
    cust = _register_and_login(client, "f-role-cust@example.com")
    r = client.get("/api/v3/admin/overview", headers=cust)
    assert r.status_code == 403
    # role map is admin-only
    r = client.get("/api/v3/admin/roles", headers=ah)
    assert r.status_code == 200
    assert "finance" in r.json()["roles"]
    assert "settlements.write" in r.json()["permissions"]["finance"]
    r = client.get("/api/v3/admin/roles", headers=fh)
    assert r.status_code == 403


# ---------- review dispute resolution ----------
def test_review_dispute_resolve(client):
    shop_id, face = CASH[22]
    h, wid, rid = _redeem(client, "f-review@example.com", 22)
    # counted review (no points): enters the aggregate
    r = client.post("/api/v3/reviews", json={
        "redemption_id": rid, "taste": 5, "ambience": 4, "service": 3,
        "content": "好食", "claim_points": False}, headers=h)
    assert r.status_code == 200, r.text
    review_id = r.json()["review"]["id"]
    rating = client.get(f"/api/v3/shops/{shop_id}/rating").json()
    assert rating["review_count"] == 1
    # dispute (by the shop vendor): leaves the aggregate
    r = client.post(f"/api/v3/reviews/{review_id}/dispute",
                    json={"reason": "懷疑非真實評價"},
                    headers=_vendor_headers(client))
    assert r.status_code == 200, r.text
    assert client.get(f"/api/v3/shops/{shop_id}/rating").json()["review_count"] == 0
    # resolve is auditor/support/admin only
    cust = _register_and_login(client, "f-review-cust@example.com")
    r = client.post(f"/api/v3/reviews/{review_id}/resolve",
                    json={"decision": "dismiss"}, headers=cust)
    assert r.status_code == 403
    # dismiss: genuine review, back into the aggregate
    r = client.post(f"/api/v3/reviews/{review_id}/resolve",
                    json={"decision": "dismiss", "note": "有核銷記錄"},
                    headers=_auditor_headers(client))
    assert r.status_code == 200, r.text
    assert r.json()["review"]["status"] == "visible"
    assert client.get(f"/api/v3/shops/{shop_id}/rating").json()["review_count"] == 1
    # dispute again, then uphold: hidden, stays out of the aggregate
    client.post(f"/api/v3/reviews/{review_id}/dispute",
                json={"reason": "再次申訴"}, headers=_vendor_headers(client))
    r = client.post(f"/api/v3/reviews/{review_id}/resolve",
                    json={"decision": "uphold", "note": "多賬號雷同文案"},
                    headers=_support_headers(client))
    assert r.status_code == 200
    assert r.json()["review"]["status"] == "hidden"
    assert client.get(f"/api/v3/shops/{shop_id}/rating").json()["review_count"] == 0
    # resolving a non-disputed review is rejected
    r = client.post(f"/api/v3/reviews/{review_id}/resolve",
                    json={"decision": "dismiss"},
                    headers=_auditor_headers(client))
    assert r.status_code == 400
    # disputed queue is empty now
    r = client.get("/api/v3/admin/reviews/disputes",
                   headers=_auditor_headers(client))
    assert r.status_code == 200 and r.json()["total"] == 0


# ---------- CMS ----------
def test_cms_banners_announcements_categories(client):
    ah = _admin_headers(client)
    fh = _finance_headers(client)
    # banner: invalid window rejected; draft -> active appears publicly
    r = client.post("/api/v3/admin/banners", json={
        "title": "壞橫幅", "status": "active",
        "start_at": "2026-12-01T00:00:00", "end_at": "2026-11-01T00:00:00"},
        headers=ah)
    assert r.status_code == 400
    r = client.post("/api/v3/admin/banners", json={
        "title": "雙十一橫幅", "image": "assets/images/bg_main.png",
        "link_type": "coupon", "link_target": "2", "sort_order": 1,
        "status": "active"}, headers=ah)
    assert r.status_code == 201, r.text
    banner_id = r.json()["banner"]["id"]
    assert any(b["id"] == banner_id
               for b in client.get("/api/v3/banners").json()["banners"])
    # finance can read banners but not write them
    assert client.get("/api/v3/admin/banners", headers=fh).status_code == 200
    r = client.post("/api/v3/admin/banners", json={"title": "x"}, headers=fh)
    assert r.status_code == 403
    # archive removes it from the public list
    r = client.delete(f"/api/v3/admin/banners/{banner_id}", headers=ah)
    assert r.status_code == 200
    assert r.json()["banner"]["status"] == "archived"
    assert not any(b["id"] == banner_id
                   for b in client.get("/api/v3/banners").json()["banners"])
    # announcements
    r = client.post("/api/v3/admin/announcements", json={
        "title": "系統維護", "body": "凌晨維護兩小時", "status": "active"},
        headers=ah)
    assert r.status_code == 201, r.text
    ann_id = r.json()["announcement"]["id"]
    assert any(a["id"] == ann_id
               for a in client.get("/api/v3/announcements").json()["announcements"])
    r = client.put(f"/api/v3/admin/announcements/{ann_id}", json={
        "title": "系統維護（更新）", "body": "改期", "status": "active"},
        headers=ah)
    assert r.status_code == 200
    assert r.json()["announcement"]["title"] == "系統維護（更新）"
    # categories: create / duplicate / deactivate / activate
    r = client.post("/api/v3/admin/categories",
                    json={"name": "測試分類", "name_en": "Test"}, headers=ah)
    assert r.status_code == 201, r.text
    cat_id = r.json()["category"]["id"]
    r = client.post("/api/v3/admin/categories",
                    json={"name": "測試分類"}, headers=ah)
    assert r.status_code == 400
    r = client.post(f"/api/v3/admin/categories/{cat_id}/deactivate", headers=ah)
    assert r.status_code == 200 and r.json()["category"]["is_active"] is False
    r = client.post(f"/api/v3/admin/categories/{cat_id}/activate", headers=ah)
    assert r.status_code == 200 and r.json()["category"]["is_active"] is True
    cust = _register_and_login(client, "f-cms-cust@example.com")
    assert client.get("/api/v3/admin/categories", headers=cust).status_code == 403


# ---------- shop members ----------
def test_shop_members(client):
    ah = _admin_headers(client)
    vh = _vendor_headers(client)
    h = _register_and_login(client, "f-member@example.com")
    uid = client.get("/api/v3/get-profile", headers=h).json()["id"]
    # shop owner adds a staff member; the user becomes merchant-admin
    r = client.post("/api/v3/admin/shops/1/members",
                    json={"user_id": uid}, headers=vh)
    assert r.status_code == 201, r.text
    assert client.get("/api/v3/get-profile", headers=h).json()["role"] == "merchant-admin"
    # member can read their shop's settlements, but not another shop's
    assert client.get("/api/v3/merchant/shops/1/settlements",
                      headers=h).status_code == 200
    assert client.get("/api/v3/merchant/shops/2/settlements",
                      headers=h).status_code == 403
    # duplicate add rejected; owner removes the member
    r = client.post("/api/v3/admin/shops/1/members",
                    json={"user_id": uid}, headers=vh)
    assert r.status_code == 400
    r = client.delete(f"/api/v3/admin/shops/1/members/{uid}", headers=vh)
    assert r.status_code == 200
    assert client.get("/api/v3/merchant/shops/1/settlements",
                      headers=h).status_code == 403
    # a customer cannot add members
    cust = _register_and_login(client, "f-member-cust@example.com")
    r = client.post("/api/v3/admin/shops/1/members",
                    json={"user_id": uid}, headers=cust)
    assert r.status_code == 403


# ---------- uploads ----------
def test_review_image_upload(client):
    h = _register_and_login(client, "f-upload@example.com")
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    # unauthenticated upload is rejected
    r = client.post("/api/v3/uploads/review-images",
                    files=[("files", ("a.png", png, "image/png"))])
    assert r.status_code == 401
    # non-image rejected; >9 files rejected
    r = client.post("/api/v3/uploads/review-images",
                    files=[("files", ("a.txt", b"hello", "text/plain"))],
                    headers=h)
    assert r.status_code == 400
    many = [("files", (f"{i}.png", png, "image/png")) for i in range(10)]
    r = client.post("/api/v3/uploads/review-images", files=many, headers=h)
    assert r.status_code == 400
    # two images upload, are served back, and can be attached to a review
    r = client.post("/api/v3/uploads/review-images",
                    files=[("files", ("a.png", png, "image/png")),
                           ("files", ("b.png", png, "image/png"))],
                    headers=h)
    assert r.status_code == 201, r.text
    paths = r.json()["images"]
    assert len(paths) == 2
    assert all(p.startswith("/uploads/review-images/") for p in paths)
    assert client.get(paths[0]).status_code == 200
    _, wid, rid = _redeem(client, "f-upload-review@example.com", 24)
    uh = _login(client, "f-upload-review@example.com")
    r = client.post("/api/v3/reviews", json={
        "redemption_id": rid, "taste": 5, "ambience": 5, "service": 5,
        "content": "有圖評價", "images": paths, "claim_points": False},
        headers=uh)
    assert r.status_code == 200, r.text
    assert r.json()["review"]["images"] == paths
