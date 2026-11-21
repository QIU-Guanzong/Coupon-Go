"""Auth routes (mirrors the original endpoint names under /api/v3).

Flow (from REVERSE.md §2.4): email + password, 6-digit OTP for registration
and password reset, remember-me + token refresh on the client, social login
(Google / Facebook / Apple), customer and vendor as separate accounts.
"""
import hashlib
import random
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from .. import cashback_service as cashback_svc, config, legal, models, schemas, security
from ..database import get_db
from ..models_cashback import CashbackWithdrawal
from ..models_line_b import MerchantApplication
from ..models_line_f import Settlement, SettlementItem
from ..models_packages import Order, Package
from ..settlement_basis import settlement_basis
from ..deps import (
    add_points, get_current_user, get_identity_user, get_session_identity,
    points_balance,
)

router = APIRouter()


def _lock_email_user(db: Session, email: str):
    """Serialize identity transitions for an existing account.

    The same user-row lock is taken by deletion and cashback operations.
    Never read an OTP before acquiring this lock: a stale OTP must not
    cross a deletion and be applied to a new account with the same email.
    """
    return (db.query(models.User).filter(models.User.email == email)
            .with_for_update().populate_existing().first())


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _issue_otp(db: Session, email: str, purpose: str, role: str = "customer") -> str:
    code = "".join(random.choices("0123456789", k=config.OTP_LENGTH))
    db.add(models.OtpCode(
        email=email, code=code, purpose=purpose, role=role,
        expires_at=_now() + timedelta(seconds=config.OTP_TTL_SECONDS),
    ))
    db.commit()
    return code


def _check_otp(db: Session, email: str, code: str, purpose: str) -> models.OtpCode:
    otp = (
        db.query(models.OtpCode)
        .filter(models.OtpCode.email == email,
                models.OtpCode.code == code,
                models.OtpCode.purpose == purpose,
                models.OtpCode.used.is_(False))
        .order_by(models.OtpCode.id.desc())
        .first()
    )
    if otp is None:
        raise HTTPException(status_code=400, detail="驗證碼錯誤，請重新輸入")
    if otp.expires_at < _now():
        raise HTTPException(status_code=400, detail="驗證碼已過期，請重新發送")
    return otp


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
    user = _lock_email_user(db, body.email)
    if (user is None or user.deleted_at is not None or
            body.purpose not in ("register", "forgot") or
            (body.purpose == "register" and user.is_active) or
            (body.purpose == "forgot" and not user.is_active)):
        raise HTTPException(status_code=400, detail="驗證碼錯誤，請重新輸入")
    otp = _check_otp(db, body.email, body.code, body.purpose)
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
def resend_otp(body: schemas.OtpRequestIn, db: Session = Depends(get_db)):
    user = _lock_email_user(db, body.email)
    if (user is not None and user.deleted_at is None and
            ((body.purpose == "register" and not user.is_active) or
             (body.purpose == "forgot" and user.is_active))):
        _issue_otp(db, body.email, body.purpose)
    return {"message": "驗證碼已重新發送"}


@router.post("/resent-otp")
def resend_otp_forgot(body: schemas.OtpRequestIn, db: Session = Depends(get_db)):
    return resend_otp(body, db)


@router.post("/login")
def login(body: schemas.LoginIn, db: Session = Depends(get_db)):
    user = _lock_email_user(db, body.email)
    if user is None or not security.verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="電郵或密碼錯誤")
    if user.deleted_at is not None:
        raise HTTPException(status_code=401, detail="電郵或密碼錯誤")
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
    user = _lock_email_user(db, email)
    if user is None:
        user = models.User(
            email=email, password_hash=security.hash_password(secrets.token_hex(16)),
            name=f"{body.provider} 用戶", role=body.role, is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    if user.deleted_at is not None or not user.is_active:
        raise HTTPException(status_code=401, detail="無法登入")
    return _token_for(user, db)


@router.post("/password/forgot")
def forgot(body: schemas.ForgotIn, db: Session = Depends(get_db)):
    user = _lock_email_user(db, body.email)
    if user is not None and user.is_active and user.deleted_at is None:
        _issue_otp(db, body.email, "forgot")
    return {"message": "如帳戶存在，我們已發送重設密碼的驗證碼"}


@router.post("/password/reset")
def reset(body: schemas.ResetIn, db: Session = Depends(get_db)):
    user = _lock_email_user(db, body.email)
    if user is None or not user.is_active or user.deleted_at is not None:
        raise HTTPException(status_code=400, detail="驗證碼錯誤，請重新輸入")
    otp = _check_otp(db, body.email, body.code, "forgot")
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
    """Deactivate and anonymize the identity, while retaining financial ledgers.

    - Fail closed on money: sweep first, then refuse deletion while
      cashback is withdrawable, pending, tracked, or otherwise unresolved.
    - Voids unused wallet coupons (status 1 -> 3, expired) and zeroes
      points with a negative PointTransaction (reason account_delete);
      the ledger is kept for audit. Already-used coupons are untouched.
    - Clears identity fields and verification codes; preserves the immutable
      user id and audit/settlement rows. Free-form reasons are not retained
      because they can themselves contain personal information.
    """
    cashback_svc.sweep_user(db, user.id)
    # get_identity_user resolved the token before this transaction acquired
    # its account lock; refresh to reject a concurrent completed deletion.
    db.refresh(user)
    if user.deleted_at is not None or not user.is_active:
        raise HTTPException(status_code=401, detail="請登錄以繼續")
    # The role-management flow protects the final administrator; require
    # administrator privileges to be transferred/revoked there first.
    # This is safe even when multiple administrators delete concurrently.
    if user.role == "admin":
        raise HTTPException(status_code=409, detail="請先轉移並解除管理員權限")
    if (db.query(CashbackWithdrawal.id).filter(
            CashbackWithdrawal.user_id == user.id,
            CashbackWithdrawal.status.in_(("pending", "approved"))).first()):
        raise HTTPException(status_code=409, detail="仍有處理中的返現提現，請完成後再刪除帳戶")
    if (db.query(MerchantApplication.id).filter(
            MerchantApplication.user_id == user.id,
            MerchantApplication.status == "pending").first()):
        raise HTTPException(status_code=409, detail="仍有處理中的商戶申請，請先聯絡平台處理")
    pending_customer_order = (db.query(Order.id).filter(
        Order.user_id == user.id,
        or_(Order.payment_status == "pending", Order.refund_status == "requested",
            and_(Order.payment_status == "paid", Order.refund_status != "refunded",
                 db.query(models.WalletItem.id).filter(
                     models.WalletItem.order_id == Order.id,
                     models.WalletItem.status == 1).exists()))).first())
    if pending_customer_order:
        raise HTTPException(status_code=409, detail="仍有未完成訂單或未使用的已購買優惠，請先處理")
    # Ownership is authoritative: an administrator may have changed a
    # former vendor's role while leaving Shop.vendor_id unchanged.
    if db.query(models.Shop.id).filter(models.Shop.vendor_id == user.id).first():
        shop_ids = db.query(models.Shop.id).filter(models.Shop.vendor_id == user.id)
        if db.query(models.Shop.id).filter(
                models.Shop.vendor_id == user.id, models.Shop.is_approved.is_(True)).first():
            raise HTTPException(status_code=409, detail="仍有營運中的商戶店舖，請先聯絡平台處理")
        if db.query(Settlement.id).filter(
                Settlement.shop_id.in_(shop_ids), Settlement.status != "paid",
                Settlement.net_amount != 0).first():
            raise HTTPException(status_code=409, detail="仍有未結算商戶款項，請先聯絡平台處理")
        # Sales not yet included in any statement can still be owed even
        # when the shop is no longer approved. Preserve those receivables.
        unposted = (db.query(models.Redemption)
                    .outerjoin(SettlementItem, SettlementItem.redemption_id == models.Redemption.id)
                    .filter(models.Redemption.shop_id.in_(shop_ids),
                            models.Redemption.revoked_at.is_(None),
                            SettlementItem.id.is_(None)).yield_per(200))
        for redemption in unposted:
            amount = settlement_basis(db, redemption=redemption)
            if amount is not None and amount > 0:
                raise HTTPException(status_code=409, detail="仍有未結算商戶款項，請先聯絡平台處理")
        if (db.query(Order.id).join(Package, Order.package_id == Package.id)
                .filter(Package.shop_id.in_(shop_ids),
                        or_(Order.payment_status == "pending",
                            Order.refund_status == "requested",
                            and_(Order.payment_status == "paid",
                                 Order.refund_status != "refunded",
                                 Order.settlement_status != "void",
                                 or_(Order.settlement_status == "pending",
                                     db.query(models.WalletItem.id).filter(
                                         models.WalletItem.order_id == Order.id,
                                         models.WalletItem.status == 1).exists())))).first()):
            raise HTTPException(status_code=409, detail="仍有進行中的商戶訂單，請先聯絡平台處理")
    cashback = cashback_svc.balances(db, user.id)
    withdrawable = float(cashback["withdrawable_balance"])
    if withdrawable > 0:
        return JSONResponse(
            status_code=409,
            content={"detail": f"仍有可提現返現 HK${withdrawable:g}，請先提現",
                     "withdrawable_balance": withdrawable})
    if (withdrawable < 0 or cashback["pending_balance"] > 0 or
            cashback["tracked_balance"] > 0 or cashback["withdrawal_unreconciled"] > 0):
        raise HTTPException(status_code=409, detail="仍有待確認或未結清的返現，請先聯絡平台處理")
    db.query(models.WalletItem).filter(
        models.WalletItem.user_id == user.id,
        models.WalletItem.status == 1).update({"status": 3, "code": "", "qr_expires_at": None})
    balance = points_balance(db, user.id)
    if balance > 0:
        add_points(db, user.id, -balance, "account_delete")
    # Do not retain user-supplied free text: it may contain identifying data.
    db.add(models.Notification(
        user_id=user.id, title="帳戶刪除",
        body="帳戶已刪除", notification_type="account",
    ))
    original_email = user.email
    db.query(models.OtpCode).filter(models.OtpCode.email == original_email).delete(synchronize_session=False)
    db.query(models.SearchHistory).filter_by(user_id=user.id).delete(synchronize_session=False)
    db.query(models.Favorite).filter_by(user_id=user.id).delete(synchronize_session=False)
    # Randomness prevents a deleted id from claiming a predictable tombstone.
    user.email = f"deleted+{user.id}-{secrets.token_hex(16)}@deleted.invalid"
    user.name = ""
    user.phone = ""
    # This deliberately cannot be verified by bcrypt; no old credential works.
    user.password_hash = "!deleted!" + secrets.token_hex(32)
    user.is_active = False
    user.deleted_at = _now()
    user.delete_reason = None
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
