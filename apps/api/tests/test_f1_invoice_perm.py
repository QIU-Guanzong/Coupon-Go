"""F1 tests: POST /api/v3/admin/invoices must not let an auditor (or any
platform role without invoices.write) create an invoice for an arbitrary
shop via the can_access_shop fallback.

can_access_shop returns True for auditor/finance unconditionally, so the
old fallback `if not has_perm(...): if not can_access_shop(...): 403`
let auditor POST any shop_id and get 201. The fix narrows the fallback
to the shop owner (shop.vendor_id == user.id) or a shop_members member;
platform roles without invoices.write get 403. finance/admin keep
invoices.write and are unaffected; vendor own-shop and staff behaviour
are preserved (see test_invoice_chain_and_cap in test_line_f.py).

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/ -v
Own throwaway SQLite file; never touches production data.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

TEST_DB = "/tmp/coupon_go_test_f1.db"

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


def _invoice(client, headers, shop_id):
    return client.post("/api/v3/admin/invoices", json={
        "shop_id": shop_id, "amount": 1, "title": "測試公司",
        "tax_id": "12345678"}, headers=headers)


# ---------- F1: auditor privilege escalation ----------
def test_f1_auditor_cannot_create_invoice_for_any_shop(client):
    # Seeded auditor owns no shop; pre-fix this returned 201 because
    # can_access_shop() is unconditionally True for auditor.
    ah = _login(client, "auditor@coupon-go.example")
    for shop_id in (1, 2):
        r = _invoice(client, ah, shop_id)
        assert r.status_code == 403, r.text


def test_f1_support_cannot_create_invoice(client):
    sh = _login(client, "support@coupon-go.example")
    r = _invoice(client, sh, 1)
    assert r.status_code == 403, r.text


def test_f1_finance_can_create_invoice(client):
    fh = _login(client, "finance@coupon-go.example")
    r = _invoice(client, fh, 1)
    assert r.status_code == 201, r.text
    assert r.json()["invoice"]["status"] == "pending"


def test_f1_vendor_can_create_invoice_for_own_shop(client):
    # All seeded shops belong to vendor@coupon-go.example.
    vh = _login(client, "vendor@coupon-go.example")
    r = _invoice(client, vh, 1)
    assert r.status_code == 201, r.text


def test_f1_vendor_cannot_create_invoice_for_other_shop(client):
    # A different vendor owns no seeded shop, so every shop is "other".
    other = _register_and_login(client, "f1-other-vendor@example.com",
                                role="vendor")
    r = _invoice(client, other, 1)
    assert r.status_code == 403, r.text


def test_f1_customer_cannot_create_invoice(client):
    cust = _register_and_login(client, "f1-cust@example.com")
    r = _invoice(client, cust, 1)
    assert r.status_code == 403, r.text


def test_f1_shop_member_can_create_invoice_for_own_shop_only(client):
    # Staff fallback is preserved: a shop_members member may invoice
    # their own shop, but not another shop.
    vh = _login(client, "vendor@coupon-go.example")
    h = _register_and_login(client, "f1-member@example.com")
    uid = client.get("/api/v3/get-profile", headers=h).json()["id"]
    r = client.post("/api/v3/admin/shops/1/members",
                    json={"user_id": uid}, headers=vh)
    assert r.status_code == 201, r.text
    # F8: invite is pending until the invitee confirms it.
    r = client.post(
        f"/api/v3/admin/shops/1/members/{r.json()['member']['id']}/confirm",
        headers=h)
    assert r.status_code == 200, r.text
    assert client.get("/api/v3/get-profile",
                      headers=h).json()["role"] == "merchant-admin"
    r = _invoice(client, h, 1)
    assert r.status_code == 201, r.text
    r = _invoice(client, h, 2)
    assert r.status_code == 403, r.text
