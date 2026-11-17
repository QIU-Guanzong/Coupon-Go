"""Gap 11 tests: review-image upload wired through POST /uploads/review-images.

The chain under test: upload image files -> server returns
/uploads/review-images/... paths -> POST /reviews with those paths ->
review list returns the image addresses. Invalid / oversized files are
rejected with the uploads endpoint's own validation (image/* only,
extension allow-list, <= UPLOAD_MAX_BYTES, max 9 files).

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_gap11_review_image_upload.py -v
Uses its own throwaway SQLite file so parallel gap workers do not
delete each other's test DB.
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import config  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

TEST_DB = "/tmp/coupon_go_test_gap11.db"

engine = create_engine(f"sqlite:///{TEST_DB}", connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


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


def _redeem(client, email, coupon_id):
    h = _register_and_login(client, email)
    r = client.post("/api/v3/new-favorite-coupon",
                    params={"coupon_id": coupon_id}, headers=h)
    assert r.status_code == 200, r.text
    wid = client.get("/api/v3/get-favorite-coupons-list",
                     headers=h).json()["items"][0]["id"]
    qr = client.post("/api/v3/store-scaned-qr-values",
                     params={"wallet_item_id": wid}, headers=h)
    assert qr.status_code == 200, qr.text
    vh = _login(client, "vendor@coupon-go.example")
    r = client.post("/api/v3/new-scan-coupon",
                    json={"code": qr.json()["code"]}, headers=vh)
    assert r.status_code == 200, r.text
    rid = client.get("/api/v3/get-user-record-list",
                     headers=h).json()["records"][0]["id"]
    return h, wid, rid


def test_upload_then_review_with_images(client):
    h = _register_and_login(client, "gap11-up@example.com")
    r = client.post("/api/v3/uploads/review-images",
                    files=[("files", ("a.png", PNG, "image/png")),
                           ("files", ("b.png", PNG, "image/png"))],
                    headers=h)
    assert r.status_code == 201, r.text
    paths = r.json()["images"]
    assert len(paths) == 2
    assert all(p.startswith("/uploads/review-images/") for p in paths)
    # uploaded files are served back
    assert client.get(paths[0]).status_code == 200

    uh, wid, rid = _redeem(client, "gap11-review@example.com", 2)
    r = client.post("/api/v3/reviews", json={
        "redemption_id": rid, "taste": 5, "ambience": 5, "service": 5,
        "content": "有圖評價", "images": paths, "claim_points": False},
        headers=uh)
    assert r.status_code == 200, r.text
    assert r.json()["review"]["images"] == paths
    shop_id = r.json()["review"]["shop_id"]
    lst = client.get(f"/api/v3/shops/{shop_id}/reviews").json()["reviews"]
    assert [x for x in lst if x["id"] == r.json()["review"]["id"]][0]["images"] == paths


def test_upload_invalid_type_rejected(client):
    h = _register_and_login(client, "gap11-badtype@example.com")
    # plain-text file with a .txt name
    r = client.post("/api/v3/uploads/review-images",
                    files=[("files", ("a.txt", b"hello", "text/plain"))],
                    headers=h)
    assert r.status_code == 400
    # .exe extension is not on the allow-list even with an image mime type
    r = client.post("/api/v3/uploads/review-images",
                    files=[("files", ("a.exe", PNG, "image/png"))],
                    headers=h)
    assert r.status_code == 400


def test_upload_oversize_rejected(client):
    h = _register_and_login(client, "gap11-big@example.com")
    big = b"\x00" * (config.UPLOAD_MAX_BYTES + 1)
    r = client.post("/api/v3/uploads/review-images",
                    files=[("files", ("big.png", big, "image/png"))],
                    headers=h)
    assert r.status_code == 400


def test_upload_requires_login(client):
    r = client.post("/api/v3/uploads/review-images",
                    files=[("files", ("a.png", PNG, "image/png"))])
    assert r.status_code == 401


def test_review_images_capped_at_nine(client):
    uh, wid, rid = _redeem(client, "gap11-cap@example.com", 3)
    r = client.post("/api/v3/reviews", json={
        "redemption_id": rid, "taste": 5, "ambience": 5, "service": 5,
        "content": "太多圖", "images": [f"/uploads/review-images/{i}.png" for i in range(10)],
        "claim_points": False}, headers=uh)
    assert r.status_code == 422
