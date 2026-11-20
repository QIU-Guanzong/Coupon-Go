"""Password hashing (bcrypt directly) + JWT helpers.

Note: passlib is intentionally NOT used — it is incompatible with
bcrypt >= 4.1 (missing __about__ / 72-byte wrap bug detection).
"""
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from . import config


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(subject: str, role: str, *, consent_only: bool = False) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    claims = {"sub": subject, "role": role, "exp": expire}
    if consent_only:
        claims["purpose"] = "legal-consent"
        claims["exp"] = min(expire, datetime.now(timezone.utc) + timedelta(minutes=15))
    return jwt.encode(
        claims,
        config.SECRET_KEY,
        algorithm=config.ALGORITHM,
    )


def decode_token(token: str):
    try:
        return jwt.decode(token, config.SECRET_KEY, algorithms=[config.ALGORITHM])
    except JWTError:
        return None
