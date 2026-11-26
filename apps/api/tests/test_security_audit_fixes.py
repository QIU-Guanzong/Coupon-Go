"""Regression tests for 2026-11-26 security audit fixes (S-H1..S-H4, S-M1, S-M3)."""
import os
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import config, legal, models, rate_limit, security  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.routers.uploads import _has_valid_image_magic  # noqa: E402
from app.shared import csv_response, sanitize_csv_cell  # noqa: E402


@pytest.fixture()
def isolated_client(tmp_path):
    db_path = tmp_path / "sec_audit.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def _override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    prev = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as client:
            yield client, SessionLocal
    finally:
        if prev is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = prev
        engine.dispose()


def _create_user(SessionLocal, email: str, password: str = "SecurePass123!", role: str = "customer"):
    db = SessionLocal()
    try:
        user = models.User(
            email=email,
            password_hash=security.hash_password(password),
            name="Audit User",
            phone="91234567",
            role=role,
            is_active=True,
        )
        db.add(user)
        db.flush()
        docs = legal.documents()
        if docs:
            db.add(models.LegalAcceptance(
                user_id=user.id,
                terms_version=docs["terms"]["version"],
                privacy_version=docs["privacy"]["version"],
                terms_url=docs["terms"]["url"],
                privacy_url=docs["privacy"]["url"],
            ))
        db.commit()
        db.refresh(user)
        return user.id
    finally:
        db.close()


def test_csv_formula_injection_sanitized():
    assert sanitize_csv_cell("=CMD|' /C calc'!A0") == "'=CMD|' /C calc'!A0"
    assert sanitize_csv_cell("+1+1") == "'+1+1"
    assert sanitize_csv_cell("-2+3") == "'-2+3"
    assert sanitize_csv_cell("@SUM(A1:A2)") == "'@SUM(A1:A2)"
    # Numeric values and normal text remain untouched.
    assert sanitize_csv_cell(-10.5) == -10.5
    assert sanitize_csv_cell("Normal Shop") == "Normal Shop"

    resp = csv_response("test.csv", ["name", "amount"], [["=1+1", -50]])
    body = resp.body.decode("utf-8")
    assert "'=1+1,-50" in body


def test_public_upload_magic_bytes_validation():
    assert _has_valid_image_magic(b"\xff\xd8\xff\xe0\x00\x10JFIF", "jpg")
    assert _has_valid_image_magic(b"\x89PNG\r\n\x1a\n\x00\x00", "png")
    assert _has_valid_image_magic(b"RIFF\x24\x00\x00\x00WEBPVP8 ", "webp")
    assert _has_valid_image_magic(b"GIF89a\x01\x00", "gif")
    # Polyglot / HTML disguised as .jpg must be rejected.
    assert not _has_valid_image_magic(b"<html><script>alert(1)</script></html>", "jpg")
    assert not _has_valid_image_magic(b"%PDF-1.4\n...", "png")


def test_jwt_logout_and_relogin_same_second_db_backed(isolated_client):
    client, SessionLocal = isolated_client
    _create_user(SessionLocal, "logout-audit@example.com")

    r1 = client.post("/api/v3/login", json={
        "email": "logout-audit@example.com", "password": "SecurePass123!"
    })
    assert r1.status_code == 200
    old_token = r1.json()["access_token"]
    assert security.decode_token(old_token)["tv"] == 1
    assert client.get("/api/v3/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 200

    # Logout increments DB token_version -> old token immediately fails,
    # even after clearing in-memory rate_limit / Redis fallback state.
    assert client.post("/api/v3/logout", headers={"Authorization": f"Bearer {old_token}"}).status_code == 200
    rate_limit.reset_local_state()
    assert client.get("/api/v3/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401

    # Immediate re-login (same second) yields a valid tv=2 token.
    r2 = client.post("/api/v3/login", json={
        "email": "logout-audit@example.com", "password": "SecurePass123!"
    })
    assert r2.status_code == 200
    new_token = r2.json()["access_token"]
    assert security.decode_token(new_token)["tv"] == 2
    assert client.get("/api/v3/me", headers={"Authorization": f"Bearer {new_token}"}).status_code == 200
    assert client.get("/api/v3/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401


def test_jwt_legacy_token_without_tv_compatibility_and_revocation(isolated_client):
    client, SessionLocal = isolated_client
    uid = _create_user(SessionLocal, "legacy-tv@example.com")

    # Mint a legacy token without `tv` claim (pre-rollout format).
    legacy_token = jwt.encode(
        {
            "sub": str(uid),
            "role": "customer",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
        },
        config.SECRET_KEY,
        algorithm=config.ALGORITHM,
    )
    # Valid while user.token_version == 1.
    assert client.get("/api/v3/me", headers={"Authorization": f"Bearer {legacy_token}"}).status_code == 200
    # After logout (token_version -> 2), legacy token is immediately rejected.
    assert client.post("/api/v3/logout", headers={"Authorization": f"Bearer {legacy_token}"}).status_code == 200
    assert client.get("/api/v3/me", headers={"Authorization": f"Bearer {legacy_token}"}).status_code == 401


def test_jwt_revoked_on_password_reset_and_role_change(isolated_client):
    client, SessionLocal = isolated_client
    uid = _create_user(SessionLocal, "reset-audit@example.com", role="customer")
    _create_user(SessionLocal, "admin-audit@example.com", role="admin")

    user_token = client.post("/api/v3/login", json={
        "email": "reset-audit@example.com", "password": "SecurePass123!"
    }).json()["access_token"]
    admin_token = client.post("/api/v3/login", json={
        "email": "admin-audit@example.com", "password": "SecurePass123!"
    }).json()["access_token"]

    # 1. Password reset via OTP revokes prior token.
    assert client.post("/api/v3/password/forgot", json={"email": "reset-audit@example.com"}).status_code == 200
    db = SessionLocal()
    try:
        otp = db.query(models.OtpCode).filter_by(
            email="reset-audit@example.com", purpose="forgot", used=False
        ).order_by(models.OtpCode.id.desc()).first()
        code = otp.code
    finally:
        db.close()

    r_reset = client.post("/api/v3/password/reset", json={
        "email": "reset-audit@example.com", "code": code, "new_password": "NewSecure456!"
    })
    assert r_reset.status_code == 200
    assert client.get("/api/v3/me", headers={"Authorization": f"Bearer {user_token}"}).status_code == 401

    # 2. Re-login works, then admin role change revokes that token immediately.
    user_token_2 = client.post("/api/v3/login", json={
        "email": "reset-audit@example.com", "password": "NewSecure456!"
    }).json()["access_token"]
    assert client.get("/api/v3/me", headers={"Authorization": f"Bearer {user_token_2}"}).status_code == 200

    r_role = client.post(
        f"/api/v3/admin/users/{uid}/role",
        json={"role": "vendor"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r_role.status_code == 200
    assert client.get("/api/v3/me", headers={"Authorization": f"Bearer {user_token_2}"}).status_code == 401


def test_register_rate_limit_enforced():
    rate_limit.reset_local_state()
    for _ in range(rate_limit.REGISTER_MAX_PER_WINDOW):
        rate_limit.check_fixed_window(
            "register", "198.51.100.1",
            rate_limit.REGISTER_MAX_PER_WINDOW, rate_limit.REGISTER_WINDOW_SECONDS,
        )
    with pytest.raises(Exception) as exc_info:
        rate_limit.check_fixed_window(
            "register", "198.51.100.1",
            rate_limit.REGISTER_MAX_PER_WINDOW, rate_limit.REGISTER_WINDOW_SECONDS,
        )
    assert getattr(exc_info.value, "status_code", None) == 429
    rate_limit.reset_local_state()


def test_alembic_0032_user_token_version_upgrade(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config

    api_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_file = tmp_path / "mig_0032.db"
    db_url = f"sqlite:///{db_file}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setattr(config, "DATABASE_URL", db_url)
    cfg = Config(os.path.join(api_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(api_dir, "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(cfg, "head")
    eng = create_engine(db_url)
    try:
        cols = {c["name"]: c for c in inspect(eng).get_columns("users")}
        assert "token_version" in cols
        assert cols["token_version"]["nullable"] is False
    finally:
        eng.dispose()
