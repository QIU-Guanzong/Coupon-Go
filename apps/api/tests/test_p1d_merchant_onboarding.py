"""P1-D tests: merchant onboarding, Hong Kong fields (BR/CI/director ID),
private file uploads with signed download links, e-contract signing with
PDF archive, and the two approval gates (current contract signed +
Stripe onboarding complete).

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_p1d_merchant_onboarding.py -v
Own throwaway SQLite file; never touches production data.
"""
import hashlib
import hmac
import io
import os
import sys
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import config, models  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

# Real-Postgres run (same pattern as M3 / FIX-3 / F3): set
# P1D_POSTGRES_URL to run this module against Postgres instead of
# SQLite; unset means SQLite (default, fast, deterministic).
PG_URL = os.environ.get("P1D_POSTGRES_URL", "")


def _pg_reachable():
    if not PG_URL:
        return False
    try:
        from sqlalchemy import text as _text
        probe = create_engine(PG_URL, connect_args={"connect_timeout": 2})
        with probe.connect() as conn:
            conn.execute(_text("SELECT 1"))
        probe.dispose()
        return True
    except Exception:
        return False


if PG_URL and not _pg_reachable():
    raise RuntimeError(f"P1D_POSTGRES_URL set but unreachable: "
                       f"{PG_URL.split('@')[-1]}")

engine = None
TestingSession = None


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    global engine, TestingSession
    config.MERCHANT_FILE_DIR = str(tmp_path_factory.mktemp("p1d-files"))
    if PG_URL:
        engine = create_engine(PG_URL, pool_size=5, max_overflow=5)
    else:
        database = tmp_path_factory.mktemp("p1d-db") / "test.db"
        engine = create_engine(f"sqlite:///{database}",
                               connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine)
    if PG_URL:
        # The Postgres DB under test is built by alembic (see
        # p1d_pg_run.py) and may hold tables whose models are not on
        # this branch yet (e.g. M9-2's user_consents / legal_documents,
        # still unmerged). Plain Base.metadata.drop_all() cannot drop
        # `users` while the orphan user_consents FK references it.
        # Reflect into a THROWAWAY MetaData (never into Base.metadata:
        # reflected tables carry Postgres-only DDL such as
        # nextval('...seq'::regclass), which would break the app's own
        # startup create_all on its SQLite engine) and drop everything
        # it finds; create_all below then rebuilds only modeled tables.
        from sqlalchemy import MetaData as _MetaData
        _seen = _MetaData()
        _seen.reflect(bind=engine)
        _seen.drop_all(bind=engine)
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


def _pdf_bytes():
    return b"%PDF-1.4\n% fake pdf content for tests\n"


def _upload(client, headers, app_id, kind, filename="BR.pdf",
            content=None, content_type="application/pdf"):
    data = {"kind": kind}
    if app_id is not None:
        data["application_id"] = app_id
    return client.post("/api/v3/merchant-files",
                       data=data,
                       files={"file": (filename, content if content is not None else _pdf_bytes(),
                                       content_type)},
                       headers=headers)


def _upload_unattached(client, headers, kind, filename="BR.pdf",
                       content=None, content_type="application/pdf"):
    return _upload(client, headers, None, kind, filename=filename,
                   content=content, content_type=content_type)


def _submit(client, headers, **over):
    body = {"shop_name": "茶餐廳 A", "contact_name": "陳大文",
            "phone": "91234567", "address": "旺角彌敦道 123 號",
            "br_number": "12345678"}
    body.update(over)
    return client.post("/api/v3/admin/merchant-applications",
                       json=body, headers=headers)


def _submit_with_files(client, headers, email_tag):
    """Upload BR/CI/director-ID/shop-photo files first (unattached), then
    submit an application referencing them. Returns (app_id, file_ids)."""
    ids = {}
    for kind, name, ctype in (("br", "BR.pdf", "application/pdf"),
                              ("ci", "CI.pdf", "application/pdf"),
                              ("director_id", "ID.pdf", "application/pdf"),
                              ("shop_photo", "shop.jpg", "image/jpeg")):
        content = _pdf_bytes() if ctype == "application/pdf" else b"\xff\xd8\xff\xe0fake-jpeg"
        up = _upload_unattached(client, headers, kind, filename=name,
                                content=content, content_type=ctype)
        assert up.status_code == 201, up.text
        ids[kind] = up.json()["file"]["id"]
    r = _submit(client, headers, br_file_id=ids["br"], ci_file_id=ids["ci"],
                director_id_file_id=ids["director_id"],
                shop_photo_file_ids=[ids["shop_photo"]])
    assert r.status_code == 201, r.text
    return r.json()["application"]["id"], ids


def _sign_current(client, headers, app_id):
    return client.post(
        f"/api/v3/merchant-applications/{app_id}/sign-contract",
        json={}, headers=headers)


def _set_stripe_onboarding(client, admin_headers, app_id, **over):
    body = {"stripe_account_id": "acct_test123",
            "charges_enabled": True, "payouts_enabled": True}
    body.update(over)
    return client.post(
        f"/api/v3/admin/merchant-applications/{app_id}/stripe-onboarding",
        json=body, headers=admin_headers)


def _approve(client, admin_headers, app_id):
    return client.post(
        f"/api/v3/admin/merchant-applications/{app_id}/review",
        json={"approve": True}, headers=admin_headers)


# ---------- HK fields + validation ----------
def test_submit_hk_fields_stored(client):
    h = _register_and_login(client, "p1d-applicant@example.com")
    app_id, ids = _submit_with_files(client, h, "hk")
    ah = _admin_headers(client)
    detail = client.get(f"/api/v3/admin/merchant-applications/{app_id}",
                        headers=ah).json()["application"]
    assert detail["br_number"] == "12345678"
    assert detail["documents"]["br"]["id"] == ids["br"]
    assert detail["documents"]["ci"]["id"] == ids["ci"]
    assert detail["documents"]["director_id"]["id"] == ids["director_id"]
    assert [f["id"] for f in detail["shop_photo_files"]] == [ids["shop_photo"]]
    assert detail["stripe_account_id"] in (None, "")
    assert detail["stripe_charges_enabled"] is False
    assert detail["contract"] is None


def test_submit_bad_br_number_rejected(client):
    h = _register_and_login(client, "p1d-badbr@example.com")
    assert _submit(client, h, br_number="1234").status_code in (400, 422)
    assert _submit(client, h, br_number="ABCDEFGH").status_code in (400, 422)


def test_submit_wrong_kind_file_rejected(client):
    h = _register_and_login(client, "p1d-wrongkind@example.com")
    up = _upload_unattached(client, h, "ci")
    assert up.status_code == 201, up.text
    ci_id = up.json()["file"]["id"]
    # br_file_id must point at a kind=br file, not the CI file
    r = _submit(client, h, br_file_id=ci_id)
    assert r.status_code == 400


def test_submit_other_users_file_rejected(client):
    h = _register_and_login(client, "p1d-owner@example.com")
    other = _register_and_login(client, "p1d-other@example.com")
    up = _upload_unattached(client, h, "br")
    assert up.status_code == 201, up.text
    br_id = up.json()["file"]["id"]
    r = _submit(client, other, br_file_id=br_id)
    assert r.status_code == 400


def test_legacy_fields_preserved(client):
    h = _register_and_login(client, "p1d-legacy@example.com")
    r = _submit(client, h, business_license="BR12345678",
                id_card="HKID****1234", industry_license="食牌 FE20260001")
    assert r.status_code == 201, r.text
    app = r.json()["application"]
    assert app["business_license"] == "BR12345678"
    assert app["id_card"] == "HKID****1234"
    assert app["industry_license"] == "食牌 FE20260001"


# ---------- file upload limits ----------
def test_upload_rejects_bad_type(client):
    h = _register_and_login(client, "p1d-badtype@example.com")
    r = _submit(client, h)
    app_id = r.json()["application"]["id"]
    up = _upload(client, h, app_id, "br", filename="evil.exe",
                 content=b"MZ...", content_type="application/octet-stream")
    assert up.status_code == 400
    up = _upload(client, h, app_id, "br", filename="note.txt",
                 content=b"hello", content_type="text/plain")
    assert up.status_code == 400


def test_upload_rejects_oversize(client):
    h = _register_and_login(client, "p1d-big@example.com")
    r = _submit(client, h)
    app_id = r.json()["application"]["id"]
    big = b"x" * (config.UPLOAD_MAX_BYTES + 1)
    up = _upload(client, h, app_id, "br", filename="big.pdf",
                 content=big, content_type="application/pdf")
    assert up.status_code == 400


def test_upload_returns_no_public_url(client):
    h = _register_and_login(client, "p1d-nourl@example.com")
    r = _submit(client, h)
    app_id = r.json()["application"]["id"]
    up = _upload(client, h, app_id, "br")
    assert up.status_code == 201, up.text
    body = up.json()["file"]
    assert "url" not in body and "path" not in body
    assert body["kind"] == "br"
    assert body["size_bytes"] == len(_pdf_bytes())
    assert body["sha256"] == hashlib.sha256(_pdf_bytes()).hexdigest()


def test_upload_by_non_owner_rejected(client):
    h = _register_and_login(client, "p1d-up-owner@example.com")
    other = _register_and_login(client, "p1d-up-other@example.com")
    r = _submit(client, h)
    app_id = r.json()["application"]["id"]
    up = _upload(client, other, app_id, "br")
    assert up.status_code == 403


# ---------- signed download links ----------
def _download_link(client, headers, file_id):
    return client.post(f"/api/v3/merchant-files/{file_id}/download-link",
                       headers=headers)


def test_download_link_owner_and_admin_only(client):
    h = _register_and_login(client, "p1d-dl-owner@example.com")
    other = _register_and_login(client, "p1d-dl-other@example.com")
    ah = _admin_headers(client)
    app_id, ids = _submit_with_files(client, h, "dl")
    fid = ids["br"]
    assert _download_link(client, other, fid).status_code == 403
    r = _download_link(client, h, fid)
    assert r.status_code == 200, r.text
    assert r.json()["download_url"].startswith(
        f"/api/v3/merchant-files/{fid}/download?")
    assert "token=" in r.json()["download_url"]
    r = _download_link(client, ah, fid)
    assert r.status_code == 200, r.text


def test_download_with_valid_link(client):
    h = _register_and_login(client, "p1d-dl-ok@example.com")
    app_id, ids = _submit_with_files(client, h, "dlok")
    link = _download_link(client, h, ids["br"]).json()["download_url"]
    r = client.get(link)
    assert r.status_code == 200, r.text
    assert r.content == _pdf_bytes()
    assert r.headers["content-type"].startswith("application/pdf")


def test_download_expired_link_rejected(client):
    from app.routers.merchant_onboarding import _sign_token
    h = _register_and_login(client, "p1d-dl-exp@example.com")
    app_id, ids = _submit_with_files(client, h, "dlexp")
    fid = ids["br"]
    exp = int(time.time()) - 60
    token = _sign_token(fid, exp)
    r = client.get(f"/api/v3/merchant-files/{fid}/download?exp={exp}&token={token}")
    assert r.status_code == 403


def test_download_tampered_token_rejected(client):
    h = _register_and_login(client, "p1d-dl-tamper@example.com")
    app_id, ids = _submit_with_files(client, h, "dltamper")
    link = _download_link(client, h, ids["br"]).json()["download_url"]
    bad = link[:-2] + ("00" if link[-2:] != "00" else "11")
    assert client.get(bad).status_code == 403
    assert client.get(
        f"/api/v3/merchant-files/{ids['br']}/download").status_code == 403


# ---------- e-contract ----------
def test_current_contract_exists(client):
    h = _register_and_login(client, "p1d-contract@example.com")
    r = client.get("/api/v3/merchant-contracts/current", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == "1"
    assert r.json()["body_text"]


def test_sign_contract_records_version_time_ip_and_pdf(client):
    h = _register_and_login(client, "p1d-sign@example.com")
    app_id, ids = _submit_with_files(client, h, "sign")
    cur = client.get("/api/v3/merchant-contracts/current",
                     headers=h).json()["version"]
    r = _sign_current(client, h, app_id)
    assert r.status_code == 201, r.text
    sig = r.json()["signature"]
    assert sig["contract_version"] == cur
    assert sig["signed_at"]
    assert sig["ip_address"]
    assert sig["pdf_file_id"]
    # signing the same version again is idempotent
    r2 = _sign_current(client, h, app_id)
    assert r2.status_code == 200, r2.text
    assert r2.json()["signature"]["id"] == sig["id"]
    # the archived PDF downloads as a real PDF
    pdf = client.get(f"/api/v3/merchant-applications/{app_id}/contract-pdf",
                     headers=h)
    assert pdf.status_code == 200, pdf.text
    dl = client.get(pdf.json()["download_url"])
    assert dl.status_code == 200, dl.text
    assert dl.content.startswith(b"%PDF")
    assert len(dl.content) > 500


def test_sign_contract_by_non_owner_rejected(client):
    h = _register_and_login(client, "p1d-sign-owner@example.com")
    other = _register_and_login(client, "p1d-sign-other@example.com")
    app_id, ids = _submit_with_files(client, h, "signother")
    assert _sign_current(client, other, app_id).status_code == 403


def test_admin_can_publish_new_version(client):
    ah = _admin_headers(client)
    r = client.post("/api/v3/merchant-contracts", json={
        "version": "2", "title": "商户合作协议 v2",
        "body_text": "第二版正文。"}, headers=ah)
    assert r.status_code == 201, r.text
    assert r.json()["contract"]["is_current"] is True
    cur = client.get("/api/v3/merchant-contracts/current",
                     headers=ah).json()
    assert cur["version"] == "2"
    # duplicate version is refused
    r = client.post("/api/v3/merchant-contracts", json={
        "version": "2", "title": "重複", "body_text": "x"}, headers=ah)
    assert r.status_code == 409
    # non-admin cannot publish
    h = _register_and_login(client, "p1d-pub-user@example.com")
    r = client.post("/api/v3/merchant-contracts", json={
        "version": "3", "title": "x", "body_text": "x"}, headers=h)
    assert r.status_code == 403


# ---------- approval gates ----------
def test_approve_blocked_without_contract(client):
    h = _register_and_login(client, "p1d-gate1@example.com")
    ah = _admin_headers(client)
    app_id, ids = _submit_with_files(client, h, "gate1")
    _set_stripe_onboarding(client, ah, app_id)
    r = _approve(client, ah, app_id)
    assert r.status_code == 400
    assert "合約" in r.json()["detail"]


def test_approve_blocked_without_stripe_onboarding(client):
    h = _register_and_login(client, "p1d-gate2@example.com")
    ah = _admin_headers(client)
    app_id, ids = _submit_with_files(client, h, "gate2")
    _sign_current(client, h, app_id)
    r = _approve(client, ah, app_id)
    assert r.status_code == 400
    assert "Stripe" in r.json()["detail"]


def test_approve_blocked_with_partial_stripe_onboarding(client):
    h = _register_and_login(client, "p1d-gate3@example.com")
    ah = _admin_headers(client)
    app_id, ids = _submit_with_files(client, h, "gate3")
    _sign_current(client, h, app_id)
    _set_stripe_onboarding(client, ah, app_id, payouts_enabled=False)
    r = _approve(client, ah, app_id)
    assert r.status_code == 400
    _set_stripe_onboarding(client, ah, app_id, charges_enabled=False,
                           payouts_enabled=True)
    r = _approve(client, ah, app_id)
    assert r.status_code == 400


def test_approve_blocked_when_only_old_contract_signed(client):
    h = _register_and_login(client, "p1d-gate4@example.com")
    ah = _admin_headers(client)
    app_id, ids = _submit_with_files(client, h, "gate4")
    _sign_current(client, h, app_id)  # signs whatever is current now
    # publish a newer version: the earlier signature is now stale
    r = client.post("/api/v3/merchant-contracts", json={
        "version": "9", "title": "商户合作协议 v9",
        "body_text": "新版正文。"}, headers=ah)
    assert r.status_code == 201, r.text
    _set_stripe_onboarding(client, ah, app_id)
    r = _approve(client, ah, app_id)
    assert r.status_code == 400
    assert "合約" in r.json()["detail"]
    # re-sign the new current version, then approval passes
    assert _sign_current(client, h, app_id).status_code == 201
    assert _approve(client, ah, app_id).status_code == 200


def test_approve_passes_with_contract_and_stripe(client):
    h = _register_and_login(client, "p1d-gate5@example.com")
    ah = _admin_headers(client)
    app_id, ids = _submit_with_files(client, h, "gate5")
    assert _sign_current(client, h, app_id).status_code == 201
    assert _set_stripe_onboarding(client, ah, app_id).status_code == 200
    r = _approve(client, ah, app_id)
    assert r.status_code == 200, r.text
    assert r.json()["application"]["status"] == "approved"
    assert client.get("/api/v3/get-profile",
                      headers=h).json()["role"] == "vendor"


def test_reject_still_needs_no_gates(client):
    h = _register_and_login(client, "p1d-gate6@example.com")
    ah = _admin_headers(client)
    app_id, ids = _submit_with_files(client, h, "gate6")
    r = client.post(f"/api/v3/admin/merchant-applications/{app_id}/review",
                    json={"approve": False, "reject_reason": "文件不清"},
                    headers=ah)
    assert r.status_code == 200, r.text


# ---------- applicant self-service ----------
def test_mine_lists_only_own_applications(client):
    h = _register_and_login(client, "p1d-mine-a@example.com")
    other = _register_and_login(client, "p1d-mine-b@example.com")
    _submit(client, h, shop_name="A 店")
    _submit(client, other, shop_name="B 店")
    mine = client.get("/api/v3/merchant-applications/mine", headers=h).json()
    assert all(a["shop_name"] == "A 店" for a in mine["applications"])
    assert mine["total"] == 1
    # owner can read own detail; another user cannot
    app_id = mine["applications"][0]["id"]
    assert client.get(f"/api/v3/merchant-applications/{app_id}",
                      headers=h).status_code == 200
    assert client.get(f"/api/v3/merchant-applications/{app_id}",
                      headers=other).status_code == 403


# ---------- rework 2026-11-21 (items 2-7) ----------

def test_sign_contract_no_cjk_font_returns_503(client, monkeypatch):
    """Item 2: no usable CJK font -> 503, no half-written state."""
    from app import contract_pdf
    monkeypatch.setattr(contract_pdf, "_register_fonts",
                        lambda: (None, None))
    h = _register_and_login(client, "p1d-nofont@example.com")
    app_id, ids = _submit_with_files(client, h, "nofont")
    r = _sign_current(client, h, app_id)
    assert r.status_code == 503, r.text
    assert "字體" in r.json()["detail"] or "字体" in r.json()["detail"]
    # No signature was recorded: the application still has no contract.
    ah = _admin_headers(client)
    detail = client.get(f"/api/v3/admin/merchant-applications/{app_id}",
                        headers=ah).json()["application"]
    assert detail["contract"] is None


def test_sign_contract_pdf_contains_chinese_text(client):
    """Item 2: with a real CJK font the PDF text holds Chinese clauses."""
    import re
    from pypdf import PdfReader
    h = _register_and_login(client, "p1d-pdftext@example.com")
    app_id, ids = _submit_with_files(client, h, "pdftext")
    # The current version may have moved on (earlier tests publish new
    # versions), so assert on the actual current body text, not v1's.
    body = client.get("/api/v3/merchant-contracts/current",
                      headers=h).json()["body_text"]
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", body))[:6]
    assert cjk, "current contract body has no Chinese text to check"
    assert _sign_current(client, h, app_id).status_code == 201
    pdf = client.get(f"/api/v3/merchant-applications/{app_id}/contract-pdf",
                     headers=h)
    assert pdf.status_code == 200, pdf.text
    dl = client.get(pdf.json()["download_url"])
    assert dl.status_code == 200, dl.text
    text = "\n".join((p.extract_text() or "")
                     for p in PdfReader(io.BytesIO(dl.content)).pages)
    assert "合約版本" in text  # meta line, always rendered
    assert cjk in text


def test_upload_unattached_count_limit_429(client, monkeypatch):
    """Item 3: > MERCHANT_UNATTACHED_MAX_FILES unattached files -> 429."""
    from app import config as _cfg
    monkeypatch.setattr(_cfg, "MERCHANT_UNATTACHED_MAX_FILES", 1)
    h = _register_and_login(client, "p1d-quota-count@example.com")
    assert _upload_unattached(client, h, "br").status_code == 201
    r = _upload_unattached(client, h, "br")
    assert r.status_code == 429, r.text
    assert "未掛接" in r.json()["detail"] or "未挂接" in r.json()["detail"]


def test_upload_unattached_bytes_limit_429(client, monkeypatch):
    """Item 3: unattached total bytes over the cap -> 429."""
    from app import config as _cfg
    monkeypatch.setattr(_cfg, "MERCHANT_UNATTACHED_MAX_BYTES", 10)
    h = _register_and_login(client, "p1d-quota-bytes@example.com")
    assert _upload_unattached(client, h, "br").status_code == 201
    r = _upload_unattached(client, h, "br")
    assert r.status_code == 429, r.text


def test_upload_rate_limit_429(client, monkeypatch):
    """Item 3: 11th upload inside one minute for one user -> 429."""
    from app import config as _cfg
    from app import merchant_upload_limits
    merchant_upload_limits.reset_for_tests()
    monkeypatch.setattr(_cfg, "MERCHANT_UPLOAD_RATE_LIMIT_PER_MINUTE", 10)
    h = _register_and_login(client, "p1d-rate@example.com")
    for i in range(10):
        r = _upload_unattached(client, h, "br")
        assert r.status_code == 201, r.text
    r = _upload_unattached(client, h, "br")
    assert r.status_code == 429, r.text
    merchant_upload_limits.reset_for_tests()


def test_cleanup_deletes_only_stale_unattached(client):
    """Item 3: cleanup removes stale unattached files only, disk too."""
    from datetime import datetime, timedelta
    from app.cleanup_merchant_files import cleanup_unattached_files
    from app.models_line_b import MerchantFile
    db = TestingSession()
    try:
        user = db.query(models.User).filter(
            models.User.email == "admin@coupon-go.example").first()
        old = datetime.now(timezone.utc).replace(tzinfo=None) \
            - timedelta(days=8)
        fresh = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = []
        for label, app_id, created in (
                ("stale-unattached", None, old),
                ("stale-attached", 1, old),
                ("fresh-unattached", None, fresh)):
            name = f"cleanup-{label}.pdf"
            path = os.path.join(config.MERCHANT_FILE_DIR, name)
            os.makedirs(config.MERCHANT_FILE_DIR, exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(b"%PDF-1.4 test")
            row = MerchantFile(application_id=app_id, uploader_id=user.id,
                               kind="br", stored_name=name,
                               original_filename=name,
                               content_type="application/pdf",
                               size_bytes=13, sha256="x",
                               created_at=created)
            db.add(row)
            rows.append((label, row, path))
        db.commit()
        summary = cleanup_unattached_files(db)
        assert summary["deleted_files"] == 1, summary
        assert summary["deleted_bytes"] == 13, summary
        assert summary["missing_on_disk"] == 0, summary
        for label, row, path in rows:
            alive = db.query(MerchantFile).filter(
                MerchantFile.id == row.id).first() is not None
            if label == "stale-unattached":
                assert not alive
                assert not os.path.exists(path)
            else:
                assert alive, label
                assert os.path.exists(path), label
                # tidy up the survivors so later runs stay clean
                os.remove(path)
                db.query(MerchantFile).filter(
                    MerchantFile.id == row.id).delete()
        db.commit()
    finally:
        db.close()


def test_upload_rejects_pdf_extension_with_non_pdf_content(client):
    """Item 4: .pdf whose bytes are not a PDF -> 400."""
    h = _register_and_login(client, "p1d-magic-pdf@example.com")
    r = _upload_unattached(client, h, "br", filename="fake.pdf",
                           content=b"hello, not a pdf at all",
                           content_type="application/pdf")
    assert r.status_code == 400, r.text


def test_upload_rejects_png_with_jpeg_content(client):
    """Item 4: .png whose bytes are JPEG -> 400."""
    h = _register_and_login(client, "p1d-magic-png@example.com")
    r = _upload_unattached(client, h, "shop_photo", filename="fake.png",
                           content=b"\xff\xd8\xff\xe0fake-jpeg",
                           content_type="image/png")
    assert r.status_code == 400, r.text


def test_download_content_type_from_server_detection(client):
    """Item 4: download Content-Type is server-detected, not the
    client-declared value stored at upload time."""
    h = _register_and_login(client, "p1d-dl-type@example.com")
    up = _upload_unattached(client, h, "shop_photo", filename="shop.jpg",
                            content=b"\xff\xd8\xff\xe0fake-jpeg",
                            content_type="image/png")
    assert up.status_code == 201, up.text
    fid = up.json()["file"]["id"]
    assert up.json()["file"]["content_type"] == "image/png"
    link = _download_link(client, h, fid).json()["download_url"]
    r = client.get(link)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/jpeg"), \
        r.headers["content-type"]


def test_trusted_proxies_rejects_catch_all():
    """Item 5: serve.trusted_proxies unit tests."""
    from app.serve import trusted_proxies
    with pytest.raises(ValueError):
        trusted_proxies("0.0.0.0/0")
    with pytest.raises(ValueError):
        trusted_proxies("203.0.113.5, ::/0")
    assert trusted_proxies("") == ""
    assert trusted_proxies("  ") == ""
    assert trusted_proxies("203.0.113.0/24") == "203.0.113.0/24"
    assert trusted_proxies("203.0.113.5, 198.51.100.0/24") == \
        "203.0.113.5,198.51.100.0/24"  # join strips the spaces


def test_access_log_filter_strips_query_string():
    """Item 6: the uvicorn access-log filter drops exp/token query
    strings while keeping method, path and status."""
    import logging
    from app.serve import StripQueryStringFilter
    filt = StripQueryStringFilter()
    record = logging.LogRecord(
        name="uvicorn.access", level=logging.INFO, pathname="x", lineno=1,
        msg='%s - "%s %s %s" %s',
        args=("203.0.113.10", "GET",
              "/api/v3/merchant-files/12/download?exp=1763634000"
              "&token=deadbeef", "HTTP/1.1", 200),
        exc_info=None)
    assert filt.filter(record) is True
    rendered = record.getMessage()
    assert "token=" not in rendered and "exp=" not in rendered
    assert "/api/v3/merchant-files/12/download" in rendered
    assert "GET" in rendered and "200" in rendered
