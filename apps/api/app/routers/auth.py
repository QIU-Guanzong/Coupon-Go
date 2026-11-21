"""Auth routes (mirrors the original endpoint names under /api/v3).

Flow (from REVERSE.md §2.4): email + password, 6-digit OTP for registration
and password reset, remember-me + token refresh on the client, social login
(Google / Facebook / Apple), customer and vendor as separate accounts.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import config, legal, models, rate_limit, schemas, security
from ..database import get_db
from ..deps import get_current_user, get_identity_user, get_session_identity

router = APIRouter()


from ..timeutil import utcnow as _now


def _issue_otp(db: Session, email: str, purpose: str, role: str = "customer") -> str:
    # Stage-0: cryptographically secure randomness (was random.choices).
    code = "".join(secrets.choice("0123456789") for _ in range(config.OTP_LENGTH))
    db.add(models.OtpCode(
        email=email, code=code, purpose=purpose, role=role,
        expires_at=_now() + timedelta(seconds=config.OTP_TTL_SECONDS),
    ))
    db.commit()
    return code


def _check_otp(db: Session, email: str, code: str, purpose: str) -> models.OtpCode:
    """Stage-0: at most 5 wrong guesses per email+purpose; the 5th voids
    the code and locks verification for 15 minutes. An older unused code
    still verifies (resends do not invalidate earlier codes)."""
    if rate_limit.is_otp_locked(email, purpose):
        raise HTTPException(status_code=429,
                            detail="驗證碼錯誤次數過多，請 15 分鐘後再試",
                            headers={"Retry-After": "900"})
    otps = (
        db.query(models.OtpCode)
        .filter(models.OtpCode.email == email,
                models.OtpCode.purpose == purpose,
                models.OtpCode.used.is_(False))
        .order_by(models.OtpCode.id.desc())
        .all()
    )
    if not otps:
        raise HTTPException(status_code=400, detail="驗證碼錯誤，請重新輸入")
    matched = next((o for o in otps if hmac.compare_digest(o.code, code)), None)
    if matched is None:
        if rate_limit.record_otp_failure(email, purpose):
            latest = otps[0]
            latest.used = True  # void the code under attack
            db.commit()
            raise HTTPException(status_code=429,
                                detail="驗證碼錯誤次數過多，驗證碼已作廢，請 15 分鐘後重新發送",
                                headers={"Retry-After": "900"})
        raise HTTPException(status_code=400, detail="驗證碼錯誤，請重新輸入")
    if matched.expires_at < _now():
        raise HTTPException(status_code=400, detail="驗證碼已過期，請重新發送")
    rate_limit.clear_otp_attempts(email, purpose)
    return matched


def _token_for(user: models.User, db: Session, docs=None):
    if docs is None:
        docs = legal.documents()
    status = legal.status(db, user.id, docs)
    return {
        **status,
        "access_token": security.create_access_token(str(user.id), user.role,
                                                     consent_only=status["consent_required"]),
        "token_type": "Bearer",
        "user_id": user.id,
        "role": user.role,
        "email": user.email,
    }


@router.post("/new-register")
def register(body: schemas.RegisterIn, db: Session = Depends(get_db)):
    if body.role not in ("customer", "vendor"):
        raise HTTPException(status_code=400, detail="role 必須是 customer 或 vendor")
    docs = legal.documents()
    legal.require_versions(body, docs)
    if db.query(models.User).filter(models.User.email == body.email).first():
        raise HTTPException(status_code=400, detail="此電郵已註冊")
    user = models.User(
        email=body.email, password_hash=security.hash_password(body.password),
        name=body.name, phone=body.phone, role=body.role, is_active=False,
    )
    db.add(user)
    db.flush()
    legal.accept(db, user.id, body, docs)
    _issue_otp(db, body.email, "register", body.role)
    # 完成註冊後，經審核可使用。(vendor onboarding is approval-gated)
    return {"message": "請輸入 6 位數驗證碼。", "email": body.email}


@router.post("/new-register-vendor")
def register_vendor(body: schemas.RegisterIn, db: Session = Depends(get_db)):
    body.role = "vendor"
    return register(body, db)


@router.post("/new-verify-otp")
def verify_otp(body: schemas.OtpVerifyIn, db: Session = Depends(get_db)):
    otp = _check_otp(db, body.email, body.code, body.purpose)
    user = db.query(models.User).filter(models.User.email == body.email).first()
    if user is None:
        raise HTTPException(status_code=404, detail="帳戶不存在")
    docs = legal.documents()
    if body.purpose == "register":
        if not legal.has_accepted(db, user.id, docs):
            legal.accept(db, user.id, body, docs, mismatch_status=409)
        user.is_active = True
    otp.used = True
    if body.purpose == "forgot" and body.new_password:
        user.password_hash = security.hash_password(body.new_password)
    db.commit()
    return _token_for(user, db, docs)


@router.post("/password/verify-otp")
def verify_otp_password(body: schemas.OtpVerifyIn, db: Session = Depends(get_db)):
    return verify_otp(body, db)


@router.post("/new-resent-otp")
def resend_otp(body: schemas.OtpRequestIn, request: Request,
               db: Session = Depends(get_db)):
    # Stage-0: per email+IP cap, and a locked email+purpose cannot get a
    # fresh code to dodge the lock.
    rate_limit.check_fixed_window(
        "resend", f"{body.email}|{rate_limit.client_ip(request)}",
        rate_limit.RESEND_MAX_PER_WINDOW, rate_limit.RESEND_WINDOW_SECONDS)
    if rate_limit.is_otp_locked(body.email, body.purpose):
        raise HTTPException(status_code=429, detail="驗證碼錯誤次數過多，請 15 分鐘後再試",
                            headers={"Retry-After": "900"})
    _issue_otp(db, body.email, body.purpose)
    return {"message": "驗證碼已重新發送"}


@router.post("/resent-otp")
def resend_otp_forgot(body: schemas.OtpRequestIn, request: Request,
                      db: Session = Depends(get_db)):
    return resend_otp(body, request, db)


@router.post("/login")
def login(body: schemas.LoginIn, request: Request, db: Session = Depends(get_db)):
    # Stage-0: 10 consecutive failures per email+IP lock login 15 min.
    identity = f"{body.email}|{rate_limit.client_ip(request)}"
    if rate_limit.is_login_locked(identity):
        raise HTTPException(status_code=429, detail="登錄失敗次數過多，請 15 分鐘後再試",
                            headers={"Retry-After": "900"})
    user = db.query(models.User).filter(models.User.email == body.email).first()
    if user is None or not security.verify_password(body.password, user.password_hash):
        if rate_limit.record_login_failure(identity):
            raise HTTPException(status_code=429, detail="登錄失敗次數過多，請 15 分鐘後再試",
                                headers={"Retry-After": "900"})
        raise HTTPException(status_code=401, detail="電郵或密碼錯誤")
    rate_limit.reset_login_failures(identity)
    if not user.is_active:
        raise HTTPException(status_code=403, detail="請先完成驗證碼驗證")
    return _token_for(user, db)


@router.post("/logout")
def logout(user: models.User = Depends(get_identity_user)):
    return {"message": "已登出"}


@router.post("/social-login")
def social_login(body: schemas.SocialLoginIn, db: Session = Depends(get_db)):
    if config.APP_ENV != "dev":
        raise HTTPException(status_code=403, detail="不支援的登錄方式")
    legal.documents()
    # TODO(launch): verify `token` server-side with the provider
    # (Google tokeninfo / Facebook debug_token) using the OAuth client IDs
    # in config before trusting it. Dev stub: token is treated as an opaque
    # identifier and mapped to a stable pseudo-account.
    if body.provider not in ("google", "facebook", "apple"):
        raise HTTPException(status_code=400, detail="不支援的登錄方式")
    digest = hashlib.sha256(body.token.encode()).hexdigest()[:16]
    email = f"{body.provider}_{digest}@social.example"
    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None:
        user = models.User(
            email=email, password_hash=security.hash_password(secrets.token_hex(16)),
            name=f"{body.provider} 用戶", role=body.role, is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return _token_for(user, db)


@router.post("/password/forgot")
def forgot(body: schemas.ForgotIn, request: Request, db: Session = Depends(get_db)):
    # Stage-0: per email+IP cap (same reply whether the account exists).
    rate_limit.check_fixed_window(
        "forgot", f"{body.email}|{rate_limit.client_ip(request)}",
        rate_limit.FORGOT_MAX_PER_WINDOW, rate_limit.FORGOT_WINDOW_SECONDS)
    if db.query(models.User).filter(models.User.email == body.email).first():
        _issue_otp(db, body.email, "forgot")
    return {"message": "如帳戶存在，我們已發送重設密碼的驗證碼"}


@router.post("/password/reset")
def reset(body: schemas.ResetIn, db: Session = Depends(get_db)):
    otp = _check_otp(db, body.email, body.code, "forgot")
    user = db.query(models.User).filter(models.User.email == body.email).first()
    if user is None:
        raise HTTPException(status_code=404, detail="帳戶不存在")
    otp.used = True
    user.password_hash = security.hash_password(body.new_password)
    db.commit()
    return {"message": "密碼已重設，請重新登錄"}


@router.get("/get-profile", response_model=schemas.UserOut)
def get_profile(user: models.User = Depends(get_current_user)):
    return user


@router.get("/me")
def get_me(identity=Depends(get_session_identity), db: Session = Depends(get_db)):
    """Current user: role + the name given at registration.

    The registration name is already stored by /new-register into
    users.name (see register() above), so no new column or migration
    is needed: registered_name is that stored value, verbatim — never
    a made-up default. (If the user later edits their profile name via
    /update-customer-profile, this follows the current name; freezing
    the registration-time value would need a separate column.)
    """
    user, consent_only = identity
    status = legal.status(db, user.id, legal.documents())
    if consent_only:
        status["consent_required"] = True
    if status["consent_required"]:
        return {"id": user.id, "role": user.role, **status}
    return {
        **status,
        "id": user.id,
        "email": user.email,
        "name": user.name or "",
        "registered_name": user.name or "",
        "role": user.role,
        "phone": user.phone or "",
    }


@router.post("/update-customer-profile", response_model=schemas.UserOut)
def update_profile(body: schemas.ProfileUpdateIn, user: models.User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    if body.name is not None:
        user.name = body.name
    if body.phone is not None:
        user.phone = body.phone
    if body.locale is not None:
        user.locale = body.locale
    db.commit()
    return user


@router.post("/delete-account")
def delete_account(body: schemas.DeleteAccountIn, user: models.User = Depends(get_identity_user),
                   db: Session = Depends(get_db)):
    # Reason is recorded in the notification log for audit; account is deactivated.
    db.add(models.Notification(
        user_id=user.id, title="帳戶刪除",
        body=f"刪除原因：{body.reason or '未填寫'}", notification_type="account",
    ))
    user.is_active = False
    db.commit()
    return {"message": "帳戶已刪除"}


@router.get("/legal/current")
def legal_current():
    docs = legal.documents()
    return {"required": bool(docs), "documents": docs}


@router.post("/legal/accept")
def legal_accept(body: schemas.LegalVersionsIn, user: models.User = Depends(get_identity_user),
                 db: Session = Depends(get_db)):
    docs = legal.documents()
    if not docs:
        raise HTTPException(503, detail={"code": "legal_documents_unavailable"})
    legal.accept(db, user.id, body, docs)
    db.commit()
    return _token_for(user, db, docs)


@router.get("/_dev/otp")
def dev_otp(email: str, db: Session = Depends(get_db)):
    """Dev-only helper: read the latest unused OTP (for manual testing).

    Disabled outside dev. Never enable in production: OTPs must travel by
    email/SMS in a real deployment.
    """
    if config.ENV != "dev":
        raise HTTPException(status_code=404, detail="Not found")
    otp = (
        db.query(models.OtpCode)
        .filter(models.OtpCode.email == email, models.OtpCode.used.is_(False))
        .order_by(models.OtpCode.id.desc())
        .first()
    )
    if otp is None:
        raise HTTPException(status_code=404, detail="沒有未使用的驗證碼")
    return {"email": email, "code": otp.code, "purpose": otp.purpose,
            "expires_at": otp.expires_at}
