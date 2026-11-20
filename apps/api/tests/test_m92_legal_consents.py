"""Explicit version acceptance and existing-session enforcement (fixture only)."""
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SEED_ON_STARTUP", "0")

from app import models
from app.database import Base, get_db
from app.main import app
from app.routers import auth

TERMS = "accepted_terms_version"
PRIVACY = "accepted_privacy_version"
URL = "https://legal.example.test"
PREFIX = "/api/v3"
ACCOUNT = {"email": "consent@example.com", "password": "demo-test-1234"}


@pytest.fixture
def service(monkeypatch):
    for kind in ("TERMS", "PRIVACY"):
        monkeypatch.setenv(f"LEGAL_{kind}_VERSION", "v1")
        monkeypatch.setenv(f"LEGAL_{kind}_URL", f"{URL}/{kind.lower()}/v1")
    socket_dir = os.getenv("COUPONGO_M92_TEST_PG_SOCKET")
    if socket_dir:
        from pathlib import Path
        from sqlalchemy import URL as SqlAlchemyURL
        socket = Path(socket_dir).resolve()
        assert socket.name == "s" and socket.parent.name.startswith("coupon-dot-test-pg.")
        assert socket.parent.stat().st_uid == os.getuid()
        assert socket.parent.stat().st_mode & 0o077 == 0
        assert (socket.parent / "data" / "PG_VERSION").is_file()
        assert (socket / ".s.PGSQL.54961").is_socket()
        engine = create_engine(SqlAlchemyURL.create(
            "postgresql+psycopg2", username="coupon_test", database="m92_legal_tests",
            query={"host": str(socket), "port": "54961"},
        ))
    else:
        engine = create_engine("sqlite://", poolclass=StaticPool,
                               connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)

    def db():
        with session() as s:
            yield s

    app.dependency_overrides[get_db] = db
    try:
        yield TestClient(app), session, monkeypatch
    finally:
        app.dependency_overrides.pop(get_db)
        Base.metadata.drop_all(engine)
        engine.dispose()


def api(client, method, path, **kwargs):
    return client.request(method, PREFIX + path, **kwargs)


def otp(session):
    with session() as s:
        row = s.scalar(select(models.OtpCode).order_by(models.OtpCode.id.desc()))
        return row.code


def bearer(data):
    return {"Authorization": f"Bearer {data['access_token']}"}


def rotate(monkeypatch):
    monkeypatch.setenv("LEGAL_TERMS_VERSION", "v2")
    monkeypatch.setenv("LEGAL_TERMS_URL", f"{URL}/terms/v2")


def test_missing_and_stale_registration_are_rejected_before_writing(service):
    client, session, _ = service
    d = api(client, "GET", "/legal/current").json()
    assert d["documents"]["terms"] == {"version": "v1", "url": f"{URL}/terms/v1"}
    for route in ("/new-register", "/new-register-vendor"):
        for payload in (ACCOUNT, {**ACCOUNT, TERMS: "v0", PRIVACY: "v1"}):
            assert api(client, "POST", route, json=payload).status_code == 400
    with session() as s:
        assert s.scalar(select(models.User)) is None
        assert s.scalar(select(models.OtpCode)) is None


def test_otp_rotation_requires_explicit_new_versions_without_burning_code(service):
    client, session, mp = service
    assert api(client, "POST", "/new-register", json={**ACCOUNT, TERMS:"v1", PRIVACY:"v1"}).status_code == 200
    rotate(mp)
    body = {"email": ACCOUNT["email"], "code": otp(session)}
    stale = api(client, "POST", "/new-verify-otp", json=body)
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "legal_versions_changed"
    success = api(client, "POST", "/new-verify-otp", json={**body, TERMS:"v2", PRIVACY:"v1"})
    assert success.status_code == 200
    assert success.json()["consent_required"] is False
    assert api(client, "GET", "/get-profile", headers=bearer(success.json())).status_code == 200


def test_old_session_gated_and_reconsent_idempotent(service):
    client, session, mp = service
    body = {**ACCOUNT, TERMS:"v1", PRIVACY:"v1"}
    assert api(client, "POST", "/new-register", json=body).status_code == 200
    v = api(client, "POST", "/new-verify-otp", json={"email": ACCOUNT["email"], "code": otp(session)})
    assert v.status_code == 200
    old = bearer(v.json())
    rotate(mp)
    assert api(client, "GET", "/me", headers=old).json()["consent_required"] is True
    assert api(client, "GET", "/get-profile", headers=old).status_code == 428
    login = api(client, "POST", "/login", json=ACCOUNT).json()
    assert login["consent_required"] is True
    restricted = bearer(login)
    assert api(client, "POST", "/legal/accept", json={TERMS:"v2", PRIVACY:"v1"}).status_code == 401
    assert api(client, "POST", "/legal/accept", headers=restricted, json={TERMS:"v1", PRIVACY:"v1"}).status_code == 400
    for _ in range(2):
        accept = api(client, "POST", "/legal/accept", headers=restricted, json={TERMS:"v2", PRIVACY:"v1"})
        assert accept.status_code == 200
        assert accept.json()["consent_required"] is False
    stale_session = api(client, "GET", "/me", headers=restricted).json()
    assert stale_session["consent_required"] is True
    assert "email" not in stale_session
    assert api(client, "GET", "/get-profile", headers=restricted).status_code == 403
    assert api(client, "GET", "/get-profile", headers=bearer(accept.json())).status_code == 200
    with session() as s:
        records = s.scalars(select(models.LegalAcceptance).order_by(models.LegalAcceptance.id)).all()
        assert len(records) == 2
        assert records[-1].terms_url == f"{URL}/terms/v2"
        assert records[-1].accepted_at is not None


def test_missing_published_config_fails_closed_outside_dev(service, monkeypatch):
    client, _, _ = service
    monkeypatch.delenv("LEGAL_PRIVACY_URL")
    monkeypatch.setattr(auth.config, "APP_ENV", "production")
    assert api(client, "GET", "/legal/current").status_code == 503
    assert api(client, "POST", "/new-register", json={**ACCOUNT, TERMS:"v1", PRIVACY:"v1"}).status_code == 503


@pytest.mark.skipif(not os.getenv("COUPONGO_M92_TEST_PG_SOCKET"), reason="requires isolated Postgres")
def test_concurrent_postgres_reconsent_is_idempotent(service):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    client, session, mp = service
    body = {**ACCOUNT, TERMS: "v1", PRIVACY: "v1"}
    assert api(client, "POST", "/new-register", json=body).status_code == 200
    assert api(client, "POST", "/new-verify-otp", json={
        "email": ACCOUNT["email"], "code": otp(session),
    }).status_code == 200
    rotate(mp)
    headers = bearer(api(client, "POST", "/login", json=ACCOUNT).json())
    start = Barrier(2)

    def accept():
        start.wait(timeout=10)
        return api(client, "POST", "/legal/accept", headers=headers, json={TERMS: "v2", PRIVACY: "v1"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: accept(), range(2)))
    assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
    with session() as s:
        assert len(s.scalars(select(models.LegalAcceptance)).all()) == 2


def test_changed_url_requires_new_version_and_never_overwrites_audit(service):
    client, session, mp = service
    body = {**ACCOUNT, TERMS: "v1", PRIVACY: "v1"}
    assert api(client, "POST", "/new-register", json=body).status_code == 200
    first = api(client, "POST", "/new-verify-otp", json={
        "email": ACCOUNT["email"], "code": otp(session),
    })
    assert first.status_code == 200
    mp.setenv("LEGAL_TERMS_URL", f"{URL}/terms/not-v1")
    headers = bearer(first.json())
    assert api(client, "GET", "/get-profile", headers=headers).status_code == 428
    conflict = api(client, "POST", "/legal/accept", headers=headers, json={TERMS:"v1", PRIVACY:"v1"})
    assert conflict.status_code == 503
    assert conflict.json()["detail"]["code"] == "legal_document_version_conflict"
    with session() as s:
        records = s.scalars(select(models.LegalAcceptance)).all()
        assert len(records) == 1 and records[0].terms_url == f"{URL}/terms/v1"
    assert api(client, "POST", "/logout", headers=headers).status_code == 200


def test_production_rejects_fixture_or_insecure_document_urls(service, monkeypatch):
    client, _, _ = service
    monkeypatch.setattr(auth.config, "APP_ENV", "production")
    assert api(client, "GET", "/legal/current").status_code == 503
    monkeypatch.setenv("LEGAL_TERMS_URL", "http://customer.example.net/terms")
    monkeypatch.setenv("LEGAL_PRIVACY_URL", "https://customer.example.net/privacy")
    reply = api(client, "GET", "/legal/current")
    assert reply.status_code == 503
    assert reply.json()["detail"]["code"] == "legal_documents_unavailable"
