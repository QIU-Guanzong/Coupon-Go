"""Password hashing (bcrypt directly) + JWT helpers.

Note: passlib is intentionally NOT used — it is incompatible with
bcrypt >= 4.1 (missing __about__ / 72-byte wrap bug detection).

Token library is PyJWT (stage-0 security baseline, queue §I: python-jose
3.5.0 / ecdsa 0.19.2 had known CVEs). Tokens are standard HS256 JWTs,
so tokens issued by the old library still decode.
"""
from datetime import datetime, timedelta, timezone
from time import time_ns

import bcrypt
import jwt

from . import config


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("utf-8"))
    except ValueError:
        return False


# Stage-0 password policy (queue §I): at least 8 chars, and the
# well-known weakest passwords are refused outright.
MIN_PASSWORD_LENGTH = 8
WEAK_PASSWORDS = frozenset({
    "password", "password1", "password123", "passw0rd", "p@ssw0rd",
    "123456", "1234567", "12345678", "123456789", "1234567890",
    "123456789a", "123123123", "123321", "654321", "11223344",
    "111111", "11111111", "000000", "00000000", "88888888",
    "66666666", "99999999", "12121212", "aaaaaaaa",
    "qwerty", "qwerty123", "qwertyuiop", "1q2w3e4r", "1qaz2wsx",
    "abc123", "abc12345", "abcdefgh",
    "letmein", "welcome", "welcome1", "monkey", "dragon",
    "football", "iloveyou", "admin123", "admin1234",
    "root1234", "changeme", "secret123",
})


def validate_password(password: str) -> None:
    """Raise ValueError when a password breaks the stage-0 policy."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError("密碼最少 8 位")
    if password.lower() in WEAK_PASSWORDS:
        raise ValueError("密碼過於常見，請換一個更難猜的密碼")


def create_access_token(subject: str, role: str, *, consent_only: bool = False) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(
        minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    claims = {"sub": subject, "role": role, "iat": int(now.timestamp()), "issued_us": time_ns() // 1000, "exp": expire}
    if consent_only:
        claims["purpose"] = "legal-consent"
        claims["exp"] = min(expire, now + timedelta(minutes=15))
    return jwt.encode(
        claims,
        config.SECRET_KEY,
        algorithm=config.ALGORITHM,
    )


def decode_token(token: str):
    try:
        return jwt.decode(token, config.SECRET_KEY, algorithms=[config.ALGORITHM])
    except jwt.InvalidTokenError:
        return None
