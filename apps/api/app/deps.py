"""Auth dependencies: current user / roles."""
from calendar import monthrange
from datetime import datetime, timezone
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import config, legal, models, security
from .database import get_db

bearer = HTTPBearer(auto_error=False)


def _identity(creds, db, *, lock_for_write=False):
    payload = security.decode_token(creds.credentials) if creds is not None else None
    if payload is not None and payload.get("purpose") in (None, "legal-consent"):
        try:
            user_id = int(payload["sub"])
            if lock_for_write:
                # Serialize authenticated writes with account deletion.
                # SQLite ignores SELECT FOR UPDATE, so acquire its write lock
                # explicitly; PostgreSQL holds the row lock until commit.
                if db.get_bind().dialect.name == "sqlite":
                    db.query(models.User).filter_by(id=user_id).update(
                        {"id": user_id}, synchronize_session=False)
                user = (db.query(models.User).filter_by(id=user_id)
                        .with_for_update(key_share=True).populate_existing().first())
            else:
                user = db.get(models.User, user_id)
        except (KeyError, TypeError, ValueError):
            user = None
        if user is not None and user.is_active and user.deleted_at is None:
            return user, payload
    return None, {}


def get_session_identity(
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> tuple[models.User, bool]:
    user, payload = _identity(creds, db, lock_for_write=request.method not in ("GET", "HEAD", "OPTIONS"))
    if user is None:
        raise HTTPException(status_code=401, detail="請登錄以繼續")
    return user, payload.get("purpose") == "legal-consent"


def get_identity_user(identity=Depends(get_session_identity)) -> models.User:
    """Identity-only routes: consent, session status, logout and deletion."""
    return identity[0]


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> models.User:
    user, payload = _identity(creds, db, lock_for_write=request.method not in ("GET", "HEAD", "OPTIONS"))
    if user is None:
        raise HTTPException(status_code=401, detail="請登錄以繼續")
    docs = legal.documents()
    if not legal.has_accepted(db, user.id, docs):
        raise HTTPException(428, detail={"code": "legal_consent_required", "documents": docs})
    if payload.get("purpose") == "legal-consent":
        raise HTTPException(403, detail={"code": "legal_consent_token_scope"})
    return user


def get_optional_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> models.User | None:
    """Unaccepted or consent-only tokens never personalise public routes."""
    user, payload = _identity(creds, db)
    if user is None or payload.get("purpose") == "legal-consent":
        return None
    try:
        docs = legal.documents()
    except HTTPException:
        return None
    return user if legal.has_accepted(db, user.id, docs) else None


def require_role(*roles: str):
    def _check(user: models.User = Depends(get_current_user)):
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="沒有權限")
        return user
    return _check


def points_balance(db: Session, user_id: int) -> int:
    from sqlalchemy import func
    total = (
        db.query(func.coalesce(func.sum(models.PointTransaction.delta), 0))
        .filter(models.PointTransaction.user_id == user_id)
        .scalar()
    )
    return int(total or 0)


def add_months(dt: datetime, months: int) -> datetime:
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    return dt.replace(year=year, month=month, day=min(dt.day, monthrange(year, month)[1]))


def add_points(db: Session, user_id: int, delta: int, reason: str, ref_id=None,
               expires_at=None):
    from .cashback_service import lock_account
    lock_account(db, user_id)
    if expires_at is None and delta > 0:
        expires_at = add_months(datetime.now(timezone.utc).replace(tzinfo=None),
                                config.POINTS_EXPIRE_MONTHS)
    db.add(models.PointTransaction(user_id=user_id, delta=delta, reason=reason,
                                   ref_id=ref_id, expires_at=expires_at))
    db.flush()
