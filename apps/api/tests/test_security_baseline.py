"""Stage-0 security baseline tests (queue §I / QUALITY_SECURITY_BASELINE.md).

Covers: PyJWT token compat + rejection, OTP via secrets + 5-failure
void/lock, login/resend/forgot rate limits, password policy.

Run:  cd apps/api && ../../.venv/bin/python -m pytest tests/test_security_baseline.py -v
Uses its own throwaway SQLite file under tmp_path (queue global rule 4:
never share /tmp/coupon_go_test_*.db with other modules).
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

import jwt  # noqa: E402  (PyJWT; the token library under test)
from app import config, models, rate_limit  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.seed import seed  # noqa: E402

STRONG = "Demo123!"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("sec_baseline")
    db_file = tmp / "sec.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
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


@pytest.fixture(autouse=True)
def _reset_limits(monkeypatch):
    # Deterministic local path: no Redis server in the test env.
    monkeypatch.setattr(config, "REDIS_URL", "")
    rate_limit.reset_local_state()
    yield
    rate_limit.reset_local_state()


def _register(client, email, password=STRONG):
    return client.post("/api/v3/new-register", json={
        "email": email, "password": password, "name": "Test", "role": "customer"})


def _otp(client, email):
    return client.get("/api/v3/_dev/otp", params={"email": email}).json()["code"]


def _activate(client, email, password=STRONG):
    assert _register(client, email, password).status_code == 200
    code = _otp(client, email)
    r = client.post("/api/v3/new-verify-otp", json={
        "email": email, "code": code, "purpose": "register"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


# ---------- 1. PyJWT: compat + rejection ----------

def test_token_roundtrip(client):
    token = _activate(client, "sec-roundtrip@example.com")
    from app import security
    decoded = security.decode_token(token)
    assert decoded is not None
    assert decoded["role"] == "customer"
    assert decoded["sub"]


def test_token_expired_rejected():
    from app import security
    import datetime
    expired = jwt.encode(
        {"sub": "1", "role": "customer",
         "exp": datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)},
        config.SECRET_KEY, algorithm="HS256")
    assert security.decode_token(expired) is None


def test_token_tampered_rejected(client):
    from app import security
    token = _activate(client, "sec-tamper@example.com")
    head, payload, sig = token.split(".")
    tampered = head + "." + ("A" if payload[0] != "A" else "B") + payload[1:] + "." + sig
    assert security.decode_token(tampered) is None


def test_token_none_algorithm_rejected():
    from app import security
    import datetime
    header = _b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = _b64(json.dumps({
        "sub": "1", "role": "customer",
        "exp": int((datetime.datetime.now(datetime.timezone.utc)
                    + datetime.timedelta(hours=1)).timestamp())}).encode())
    assert security.decode_token(f"{header}.{payload}.") is None


def test_token_handbuilt_hs256_compat():
    """A token built by hand (HMAC-SHA256, as python-jose produced them)
    must still decode: existing tokens stay valid after the library swap."""
    from app import security
    import datetime
    exp = int((datetime.datetime.now(datetime.timezone.utc)
               + datetime.timedelta(hours=1)).timestamp())
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps({"sub": "999", "role": "customer", "exp": exp}).encode())
    signing_input = f"{header}.{payload}".encode()
    sig = _b64(hmac.new(config.SECRET_KEY.encode(), signing_input, hashlib.sha256).digest())
    decoded = security.decode_token(f"{header}.{payload}.{sig}")
    assert decoded is not None
    assert decoded["sub"] == "999"


# ---------- 2. OTP: secrets + 5-failure void/lock ----------

def test_otp_uses_secrets(client, monkeypatch):
    calls = []
    real_choice = secrets.choice

    def spy(seq):
        calls.append(1)
        return real_choice(seq)

    monkeypatch.setattr(auth_router.secrets, "choice", spy)
    email = "sec-secrets@example.com"
    assert client.post("/api/v3/new-resent-otp", json={
        "email": email, "purpose": "register"}).status_code == 200
    assert calls, "OTP must be generated with secrets.choice, not random"


def test_otp_five_failures_voids_and_locks(client):
    email = "sec-otp-lock@example.com"
    assert _register(client, email).status_code == 200
    code = _otp(client, email)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(4):
        r = client.post("/api/v3/new-verify-otp", json={
            "email": email, "code": wrong, "purpose": "register"})
        assert r.status_code == 400, r.text
    # 5th wrong attempt: code voided + 15-min lock.
    r = client.post("/api/v3/new-verify-otp", json={
        "email": email, "code": wrong, "purpose": "register"})
    assert r.status_code == 429, r.text
    # Correct code no longer works while locked; resend is refused too.
    r = client.post("/api/v3/new-verify-otp", json={
        "email": email, "code": code, "purpose": "register"})
    assert r.status_code == 429, r.text
    r = client.post("/api/v3/new-resent-otp", json={
        "email": email, "purpose": "register"})
    assert r.status_code == 429, r.text


def test_otp_lock_expires_allows_resend(client, monkeypatch):
    monkeypatch.setattr(rate_limit, "OTP_LOCK_SECONDS", 2)
    email = "sec-otp-expiry@example.com"
    assert _register(client, email).status_code == 200
    code = _otp(client, email)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        client.post("/api/v3/new-verify-otp", json={
            "email": email, "code": wrong, "purpose": "register"})
    r = client.post("/api/v3/new-resent-otp", json={
        "email": email, "purpose": "register"})
    assert r.status_code == 429, r.text
    time.sleep(2.2)
    r = client.post("/api/v3/new-resent-otp", json={
        "email": email, "purpose": "register"})
    assert r.status_code == 200, r.text
    new_code = _otp(client, email)
    r = client.post("/api/v3/new-verify-otp", json={
        "email": email, "code": new_code, "purpose": "register"})
    assert r.status_code == 200, r.text


# ---------- 3. Rate limits: resend / forgot / login ----------

def test_resend_rate_limited(client):
    email = "sec-resend@example.com"
    for _ in range(5):
        r = client.post("/api/v3/new-resent-otp", json={
            "email": email, "purpose": "register"})
        assert r.status_code == 200, r.text
    r = client.post("/api/v3/new-resent-otp", json={
        "email": email, "purpose": "register"})
    assert r.status_code == 429, r.text


def test_forgot_rate_limited(client):
    email = "sec-forgot@example.com"
    for _ in range(5):
        r = client.post("/api/v3/password/forgot", json={"email": email})
        assert r.status_code == 200, r.text
    r = client.post("/api/v3/password/forgot", json={"email": email})
    assert r.status_code == 429, r.text


def test_login_ten_failures_lock(client):
    email = "sec-login-lock@example.com"
    _activate(client, email)
    for _ in range(9):
        r = client.post("/api/v3/login", json={"email": email, "password": "Wrong123!"})
        assert r.status_code == 401, r.text
    r = client.post("/api/v3/login", json={"email": email, "password": "Wrong123!"})
    assert r.status_code == 429, r.text
    r = client.post("/api/v3/login", json={"email": email, "password": STRONG})
    assert r.status_code == 429, r.text


def test_login_success_resets_failures(client):
    email = "sec-login-reset@example.com"
    _activate(client, email)
    for _ in range(9):
        assert client.post("/api/v3/login", json={
            "email": email, "password": "Wrong123!"}).status_code == 401
    assert client.post("/api/v3/login", json={
        "email": email, "password": STRONG}).status_code == 200
    for _ in range(9):
        assert client.post("/api/v3/login", json={
            "email": email, "password": "Wrong123!"}).status_code == 401
    r = client.post("/api/v3/login", json={"email": email, "password": "Wrong123!"})
    assert r.status_code == 429, r.text


def test_login_lock_expires(client, monkeypatch):
    monkeypatch.setattr(rate_limit, "LOGIN_LOCK_SECONDS", 2)
    email = "sec-login-expiry@example.com"
    _activate(client, email)
    for _ in range(10):
        client.post("/api/v3/login", json={"email": email, "password": "Wrong123!"})
    r = client.post("/api/v3/login", json={"email": email, "password": STRONG})
    assert r.status_code == 429, r.text
    time.sleep(2.2)
    r = client.post("/api/v3/login", json={"email": email, "password": STRONG})
    assert r.status_code == 200, r.text


# ---------- 4. Password policy ----------

@pytest.mark.parametrize("bad", ["Abc123!", "password", "12345678", "qwerty123", "Password1"])
def test_password_weak_denied(client, bad):
    r = _register(client, f"sec-pw-{abs(hash(bad)) % 100000}@example.com", password=bad)
    assert r.status_code == 422, r.text


def test_password_reset_weak_denied(client):
    email = "sec-pw-reset@example.com"
    _activate(client, email)
    assert client.post("/api/v3/password/forgot", json={"email": email}).status_code == 200
    code = _otp(client, email)
    r = client.post("/api/v3/password/reset", json={
        "email": email, "code": code, "new_password": "password"})
    assert r.status_code == 422, r.text
    r = client.post("/api/v3/password/reset", json={
        "email": email, "code": code, "new_password": "NewPass123!"})
    assert r.status_code == 200, r.text


def test_password_verify_otp_weak_denied(client):
    email = "sec-pw-verify@example.com"
    assert _register(client, email).status_code == 200
    code = _otp(client, email)
    r = client.post("/api/v3/new-verify-otp", json={
        "email": email, "code": code, "purpose": "forgot", "new_password": "12345678"})
    assert r.status_code == 422, r.text


# ---------- 5. Redis backend (same logic, real deployment path) ----------

class _FakeRedis:
    """Minimal stand-in for redis.Redis: implements only what rate_limit
    uses (ping/get/set/delete/eval of the two INCR scripts)."""

    def __init__(self):
        self.store = {}
        self.expires = {}

    def ping(self):
        return True

    def _expired(self, key):
        exp = self.expires.get(key)
        if exp is not None and exp <= time.monotonic():
            self.store.pop(key, None)
            self.expires.pop(key, None)
            return True
        return False

    def eval(self, script, numkeys, key, ttl):
        self._expired(key)
        count = int(self.store.get(key, 0)) + 1
        self.store[key] = count
        sliding = "if c == 1" not in script
        if sliding or count == 1:
            self.expires[key] = time.monotonic() + int(ttl)
        return count

    def get(self, key):
        if self._expired(key):
            return None
        value = self.store.get(key)
        return None if value is None else str(value).encode()

    def set(self, key, value, ex=None):
        self.store[key] = value
        if ex is not None:
            self.expires[key] = time.monotonic() + int(ex)

    def delete(self, key):
        self.store.pop(key, None)
        self.expires.pop(key, None)


def test_otp_lockout_via_redis_backend(client, monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit, "_redis", lambda url: fake)
    monkeypatch.setattr(config, "REDIS_URL", "redis://fake:6379/0")
    email = "sec-redis@example.com"
    assert _register(client, email).status_code == 200
    code = _otp(client, email)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(4):
        assert client.post("/api/v3/new-verify-otp", json={
            "email": email, "code": wrong, "purpose": "register"}).status_code == 400
    r = client.post("/api/v3/new-verify-otp", json={
        "email": email, "code": wrong, "purpose": "register"})
    assert r.status_code == 429, r.text
    assert fake.store, "counters must go through Redis when it is configured"
