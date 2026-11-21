"""P1-B router: Stripe Connect (Accounts v2, express dashboard) merchant
onboarding, balance, ledger and payouts. Interface contract: docs/P1B_API.md.

Fail-closed throughout: when Connect is not configured the onboarding /
sync / payout / retry endpoints refuse with 503 and no money state
changes. The monthly Settlement is untouched here — it stays the
reconciliation record; Connect ledger entries are a separate book.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from decimal import Decimal
from sqlalchemy.orm import Session

from .. import config, connect_gateway, connect_service, models
from ..connect_service import (
    account_out, connect_configured, get_account, merchant_balance,
    request_payout, retry_entry, sync_account,
    reconcile_entry,
)
from ..database import get_db
from ..deps import require_role
from ..models_connect import ConnectAccount, ConnectLedgerEntry
from ..payments_gateway import GatewayError

router = APIRouter()


class PayoutRequest(BaseModel):
    shop_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    # Caller generates once and reuses for retries, including after timeouts.
    request_id: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ReconcileRequest(BaseModel):
    provider_id: str = Field(min_length=4, max_length=80, pattern=r"^[A-Za-z0-9_]+$")


def _money(value) -> float:
    return connect_service._money(value)


def _shop_or_404(db: Session, shop_id, vendor: models.User) -> models.Shop:
    shop = db.query(models.Shop).filter_by(id=shop_id).first()
    if shop is None:
        raise HTTPException(status_code=404, detail="找不到商店")
    if vendor.role != "admin" and shop.vendor_id != vendor.id:
        raise HTTPException(status_code=403, detail="沒有權限")
    return shop


def _entry_out(entry: ConnectLedgerEntry) -> dict:
    return {
        "id": entry.id,
        "operation_id": entry.operation_id,
        "kind": entry.kind,
        "status": entry.status,
        "redemption_id": entry.redemption_id,
        "order_id": entry.order_id,
        "gross_amount": _money(entry.gross_amount),
        "commission": _money(entry.commission),
        "net_amount": _money(entry.net_amount),
        "stripe_transfer_id": entry.stripe_transfer_id or None,
        "stripe_reversal_id": entry.stripe_reversal_id or None,
        "stripe_payout_id": entry.stripe_payout_id or None,
        "client_request_id": entry.client_request_id,
        "pending_reversal": entry.pending_reversal,
        "first_attempt_at": entry.first_attempt_at.isoformat() if entry.first_attempt_at else None,
        "last_attempt_at": entry.last_attempt_at.isoformat() if entry.last_attempt_at else None,
        "attempt_count": entry.attempt_count,
        "note": entry.note or "",
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


@router.post("/merchant/connect/onboarding")
def start_onboarding(body: dict,
                     vendor: models.User = Depends(require_role("vendor", "admin")),
                     db: Session = Depends(get_db)):
    shop = _shop_or_404(db, (body or {}).get("shop_id"), vendor)
    if not connect_configured():
        raise HTTPException(
            status_code=503,
            detail="Stripe Connect 未配置：请先在部署环境开启 "
                   "STRIPE_CONNECT_ENABLED")
    return_url = (body or {}).get("return_url") or config.STRIPE_CONNECT_RETURN_URL
    refresh_url = (body or {}).get("refresh_url") or config.STRIPE_CONNECT_REFRESH_URL
    if not return_url or not refresh_url:
        raise HTTPException(
            status_code=400,
            detail="缺少 return_url / refresh_url（请求或 STRIPE_CONNECT_* 环境变量）")
    account = get_account(db, shop.id)
    if account is None:
        try:
            created = connect_gateway.create_account(vendor.email)
        except GatewayError as exc:
            raise HTTPException(status_code=502, detail=f"Stripe 开户失败：{exc}")
        account = ConnectAccount(
            shop_id=shop.id, stripe_account_id=created["account_id"],
            charges_enabled=bool(created.get("charges_enabled")),
            payouts_enabled=bool(created.get("payouts_enabled")),
            details_submitted=bool(created.get("details_submitted")),
            onboarding_status="pending")
        db.add(account)
        db.commit()  # Commit before the link call so a link failure
        # leaves a reusable account row instead of an orphaned one.
        db.refresh(account)
    try:
        link = connect_gateway.create_account_link(
            account.stripe_account_id, refresh_url, return_url)
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=f"Stripe 生成开户链接失败：{exc}")
    out = account_out(db, account, shop.id)
    out.update({"onboarding_url": link.get("url"),
                "simulated": bool(link.get("simulated"))})
    return out


@router.post("/merchant/connect/sync")
def sync_onboarding(body: dict,
                    vendor: models.User = Depends(require_role("vendor", "admin")),
                    db: Session = Depends(get_db)):
    shop = _shop_or_404(db, (body or {}).get("shop_id"), vendor)
    account = get_account(db, shop.id)
    if account is None:
        raise HTTPException(status_code=404, detail="此商户尚未开户，请先调用 onboarding")
    sync_account(db, account)
    db.commit()
    db.refresh(account)
    return account_out(db, account, shop.id)


@router.get("/merchant/connect/status")
def connect_status(shop_id: int = Query(...),
                   vendor: models.User = Depends(require_role("vendor", "admin")),
                   db: Session = Depends(get_db)):
    shop = _shop_or_404(db, shop_id, vendor)
    return account_out(db, get_account(db, shop.id), shop.id)


@router.get("/merchant/connect/balance")
def connect_balance(shop_id: int = Query(...),
                    vendor: models.User = Depends(require_role("vendor", "admin")),
                    db: Session = Depends(get_db)):
    shop = _shop_or_404(db, shop_id, vendor)
    balance = merchant_balance(db, shop.id)
    account = get_account(db, shop.id)
    return {"shop_id": shop.id, "currency": connect_service.CURRENCY,
            "pending": _money(balance["pending"]),
            "available": _money(balance["available"]),
            "paid_out": _money(balance["paid_out"]),
            "connect_configured": connect_configured(),
            "account_ready": connect_service.account_ready(account)}


@router.get("/merchant/connect/ledger")
def connect_ledger(shop_id: int = Query(...),
                   page: int = Query(1, ge=1),
                   page_size: int = Query(20, ge=1, le=100),
                   vendor: models.User = Depends(require_role("vendor", "admin")),
                   db: Session = Depends(get_db)):
    shop = _shop_or_404(db, shop_id, vendor)
    q = (db.query(ConnectLedgerEntry)
         .filter_by(shop_id=shop.id).order_by(ConnectLedgerEntry.id.desc()))
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size,
            "entries": [_entry_out(e) for e in rows]}


@router.post("/merchant/connect/payouts")
def create_payout(body: PayoutRequest,
                  vendor: models.User = Depends(require_role("vendor", "admin")),
                  db: Session = Depends(get_db)):
    shop = _shop_or_404(db, body.shop_id, vendor)
    out = request_payout(db, shop, body.amount, body.request_id)
    db.commit()
    return out


@router.post("/merchant/connect/transfers/{entry_id}/retry")
def retry_transfer(entry_id: int,
                   vendor: models.User = Depends(require_role("vendor", "admin")),
                   db: Session = Depends(get_db)):
    entry = db.query(ConnectLedgerEntry).filter_by(id=entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="找不到此流水")
    shop = db.query(models.Shop).filter_by(id=entry.shop_id).first()
    if shop is None:
        raise HTTPException(status_code=404, detail="找不到商店")
    if vendor.role != "admin" and shop.vendor_id != vendor.id:
        raise HTTPException(status_code=403, detail="沒有權限")
    out = retry_entry(db, entry)
    db.commit()
    return out


@router.post("/admin/connect/ledger/{entry_id}/reconcile")
def reconcile_connect_entry(entry_id: int, body: ReconcileRequest,
                            admin: models.User = Depends(require_role("admin")),
                            db: Session = Depends(get_db)):
    # The client supplies an object ID only; status and amounts come from
    # Stripe and are checked against the persisted operation identity.
    return reconcile_entry(db, entry_id, body.provider_id, admin.id)
