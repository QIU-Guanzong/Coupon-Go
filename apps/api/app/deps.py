"""Auth dependencies: current user / roles."""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import models, security
from .database import get_db

bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> models.User:
    if creds is None:
        raise HTTPException(status_code=401, detail="請登錄以繼續")
    payload = security.decode_token(creds.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="登錄已失效，請重新登錄")
    try:
        user = db.query(models.User).filter(
            models.User.id == int(payload["sub"])).first()
    except (KeyError, TypeError, ValueError):
        return None
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="帳戶不存在或已停用")
    return user


def get_optional_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> models.User | None:
    """Current user when a valid Bearer token is present, else None.

    For public endpoints that personalise one field (e.g. a review's
    helped_by_me) without requiring login. An invalid/expired token is
    treated as anonymous, never an error, on these endpoints.
    """
    if creds is None:
        return None
    payload = security.decode_token(creds.credentials)
    if payload is None:
        return None
    user = db.query(models.User).filter(models.User.id == int(payload["sub"])).first()
    if user is None or not user.is_active:
        return None
    return user


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


def add_points(db: Session, user_id: int, delta: int, reason: str, ref_id=None):
    db.add(models.PointTransaction(user_id=user_id, delta=delta, reason=reason, ref_id=ref_id))
    db.flush()
