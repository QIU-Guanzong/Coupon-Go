"""Admin v2 (F4 v2, report §13 third batch + §5.1).

Builds on admin v1 (line B) without changing any v1 path or field:
- Settlements: generate (per-redemption, per shop + YYYY-MM period) ->
  approve / dispute / resolve -> pay.
- Reconciliation: run a shop+period comparison of redemptions vs
  settlement lines; every difference is a discrepancy row that finance
  resolves with a note.
- Invoices: merchant/finance requests an invoice (optionally against a
  settlement, capped at its net_amount), finance approves/rejects, then
  marks issued.
- Content: banners / announcements CRUD (draft/active/archived + display
  window) and category management (reuses the v0.1 categories table).
- Roles: admin changes a user's role (admin / finance / auditor /
  support / merchant-admin / vendor / customer); shop staff membership
  links merchant-admin users to one shop. See app.permissions_line_f.
- Review disputes: list disputed reviews + resolve (uphold -> hidden,
  dismiss -> visible and back into the rating aggregate). v1 left
  disputed reviews out of the aggregate with no resolve endpoint; this
  is that endpoint.

Every write is recorded in admin_audit_logs (v1 table, reused).
Permissions are role-based (app.permissions_line_f); platform admin
passes every check, so the v1 single-admin setup upgrades unchanged.
"""
import json
import re
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models, schemas_line_f, schemas_packages
from ..database import get_db
from ..deps import get_current_user, require_role
from ..models_line_b import AdminAuditLog, Review
from ..models_line_f import (
    CmsAnnouncement, CmsBanner, Invoice, InvoiceCredit, MerchantCommissionRate,
    ReconciliationDiscrepancy, ReconciliationReport, Settlement,
    SettlementItem, ShopMember,
)
from ..models_packages import PAYMENT_STATUSES, Order, Package
from ..serializers_packages import order_out
from ..settlement_ledger import CENT, lock_shop, lock_statement, cash_totals
from ..models_line_f import SettlementReversal, SettlementTransfer
from ..settlement_basis import settlement_basis
from ..permissions_line_f import (
    ALL_ROLES, can_access_shop, can_request_invoice_for_shop,
    get_shop_or_404, has_perm, require_perm, role_permissions,
)
from .packages import _lock_order, _refund_order_checked
from .reviews import recompute_rating, review_out

router = APIRouter()
admin_only = require_role("admin")

# F5: platform default commission rate, used only when a shop has no
# configured rate in merchant_commission_rates.
DEFAULT_COMMISSION_RATE = Decimal("0.10")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _money(value) -> float:
    return float(value) if value is not None else 0.0


def _log(db: Session, admin_id: int, action: str, target_type: str,
         target_id, detail: str = ""):
    db.add(AdminAuditLog(admin_id=admin_id, action=action,
                         target_type=target_type, target_id=target_id,
                         detail=detail))
    db.flush()


def _page(items, page: int, per_page: int = 20):
    total = len(items)
    return {
        "current_page": page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "total": total,
        "items": items[(page - 1) * per_page: page * per_page],
    }


def _period_bounds(period: str):
    year, month = int(period[:4]), int(period[5:7])
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)
    return start, end


def _invoice_totals(db: Session, settlement: Settlement) -> dict:
    invoices = db.query(Invoice).filter_by(settlement_id=settlement.id).all()
    issued = sum((i.amount for i in invoices if i.status == "issued"), Decimal("0"))
    reserved = sum((i.amount for i in invoices if i.status in ("pending", "approved")), Decimal("0"))
    credited = db.query(func.coalesce(func.sum(InvoiceCredit.amount), 0)).join(
        Invoice, Invoice.id == InvoiceCredit.invoice_id).filter(
        Invoice.settlement_id == settlement.id, Invoice.status == "issued").scalar()
    credited = Decimal(str(credited))
    net_issued = issued - credited
    return {
        "invoice_issued_amount": _money(issued),
        "invoice_credited_amount": _money(credited),
        "invoice_net_issued_amount": _money(net_issued),
        "invoice_reserved_amount": _money(reserved),
        "invoice_available_amount": _money(max(Decimal("0"), settlement.net_amount - net_issued - reserved)),
        "invoice_excess_amount": _money(max(Decimal("0"), net_issued - settlement.net_amount)),
    }


def _settlement_out(db: Session, s: Settlement) -> dict:
    shop = db.query(models.Shop).filter(models.Shop.id == s.shop_id).first()
    paid, balance = cash_totals(db, s)
    return {
        **_invoice_totals(db, s),
        "id": s.id, "shop_id": s.shop_id,
        "shop_name": shop.name if shop else "",
        "transferred_net": _money(paid), "outstanding_net": _money(balance),
        "period": s.period, "redemption_count": s.redemption_count,
        "gross_amount": _money(s.gross_amount),
        "commission_rate": _money(s.commission_rate),
        "commission": _money(s.commission),
        "net_amount": _money(s.net_amount),
        "status": s.status, "dispute_reason": s.dispute_reason or "",
        "decided_by": s.decided_by, "decided_at": s.decided_at,
        "paid_at": s.paid_at, "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


def _invoice_out(db: Session, inv: Invoice) -> dict:
    shop = db.query(models.Shop).filter(models.Shop.id == inv.shop_id).first()
    user = db.query(models.User).filter(models.User.id == inv.user_id).first()
    credits = db.query(InvoiceCredit).filter_by(invoice_id=inv.id).order_by(InvoiceCredit.id).all()
    credited = sum((c.amount for c in credits), Decimal("0"))
    return {
        "credited_amount": _money(credited),
        "net_issued_amount": _money(inv.amount - credited) if inv.status == "issued" else 0,
        "credits": [{"id": c.id, "amount": _money(c.amount), "reference": c.reference,
                     "actor_id": c.actor_id, "created_at": c.created_at} for c in credits],
        "id": inv.id, "shop_id": inv.shop_id,
        "shop_name": shop.name if shop else "",
        "settlement_id": inv.settlement_id,
        "user_id": inv.user_id,
        "applicant_email": user.email if user else "",
        "amount": _money(inv.amount), "title": inv.title,
        "tax_id": inv.tax_id or "", "invoice_type": inv.invoice_type,
        "status": inv.status, "reject_reason": inv.reject_reason or "",
        "decided_by": inv.decided_by, "decided_at": inv.decided_at,
        "issued_at": inv.issued_at, "created_at": inv.created_at,
    }


def _banner_out(b: CmsBanner) -> dict:
    return {
        "id": b.id, "title": b.title, "image": b.image or "",
        "link_type": b.link_type, "link_target": b.link_target or "",
        "sort_order": b.sort_order, "start_at": b.start_at,
        "end_at": b.end_at, "status": b.status,
        "created_at": b.created_at, "updated_at": b.updated_at,
    }


def _announcement_out(a: CmsAnnouncement) -> dict:
    return {
        "id": a.id, "title": a.title, "body": a.body or "",
        "status": a.status, "sort_order": a.sort_order,
        "start_at": a.start_at, "end_at": a.end_at,
        "created_at": a.created_at, "updated_at": a.updated_at,
    }


def _check_window(start_at, end_at):
    if start_at and end_at and end_at <= start_at:
        raise HTTPException(status_code=400, detail="結束時間須晚於開始時間")


# ---------- overview (v2 dashboard for finance / auditor / support) ----------
@router.get("/admin/overview")
def overview(user: models.User = Depends(require_perm("dashboard.read")),
             db: Session = Depends(get_db)):
    by_status = {s: c for s, c in db.query(
        Settlement.status, func.count(Settlement.id))
        .group_by(Settlement.status).all()}
    return {
        "settlements_by_status": by_status,
        "settlements_pending_amount": _money(db.query(
            func.coalesce(func.sum(Settlement.net_amount), 0))
            .filter(Settlement.status == "pending").scalar()),
        "invoices_pending": db.query(Invoice).filter(
            Invoice.status == "pending").count(),
        "discrepancies_open": db.query(ReconciliationDiscrepancy).filter(
            ReconciliationDiscrepancy.status == "open").count(),
        "reviews_disputed": db.query(Review).filter(
            Review.status == "disputed").count(),
        "banners_active": db.query(CmsBanner).filter(
            CmsBanner.status == "active").count(),
    }


# ---------- orders (Gap 1: admin order list) ----------
def _admin_order_out(db: Session, o: Order, user: models.User) -> dict:
    """User-side order_out plus the admin extras: shop_id, the ordering
    user, and `status` as an alias of payment_status.

    Field names stay identical to user-side GET /orders (order_out);
    there is no separate order-number column — Order's id IS the order
    number (order_out already exposes it as both id and order_id).
    """
    out = order_out(db, o)
    pkg = None
    if o.package_id:
        pkg = db.query(Package).filter(Package.id == o.package_id).first()
    buyer = db.query(models.User).filter(models.User.id == o.user_id).first()
    out["shop_id"] = pkg.shop_id if pkg else None
    out["user_name"] = buyer.name if buyer else ""
    out["user_email"] = buyer.email if buyer else ""
    out["admin_refundable"] = user.role == "admin" and out["refundable"]
    out["status"] = o.payment_status
    return out


def _merchant_admin_shop_ids(db: Session, user: models.User) -> set:
    """Shops a merchant-admin may see: ACTIVE shop_members rows plus any
    shop they own directly (same scope rule as can_access_shop)."""
    ids = {m.shop_id for m in db.query(ShopMember).filter(
        ShopMember.user_id == user.id,
        ShopMember.status == "active").all()}
    ids |= {s.id for s in db.query(models.Shop).filter(
        models.Shop.vendor_id == user.id).all()}
    return ids


@router.get("/admin/orders")
def list_admin_orders(status: str = Query(None),
                      page: int = Query(1, ge=1),
                      page_size: int = Query(20, ge=1, le=100),
                      user: models.User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """Admin order list. finance / auditor / admin see all orders;
    merchant-admin sees only orders of their own shop(s) (matched via
    the order's package -> shop); every other role gets 403.

    Pagination follows the admin-v2 style (page + _page helper, 20 per
    page by default); status filters payment_status and unknown values
    are ignored, like the other admin list endpoints.
    """
    if user.role in ("finance", "auditor", "admin"):
        shop_ids = None
    elif user.role == "merchant-admin":
        shop_ids = _merchant_admin_shop_ids(db, user)
    else:
        raise HTTPException(status_code=403, detail="沒有權限")
    q = db.query(Order).order_by(Order.id.desc())
    if status in PAYMENT_STATUSES:
        q = q.filter(Order.payment_status == status)
    if shop_ids is not None:
        pkg_ids = [p.id for p in db.query(Package.id).filter(
            Package.shop_id.in_(shop_ids)).all()] if shop_ids else []
        q = q.filter(Order.package_id.in_(pkg_ids)) if pkg_ids else \
            q.filter(False)
    out = _page([_admin_order_out(db, o, user) for o in q.all()], page, page_size)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "orders": out["items"]}


@router.get("/admin/orders/{order_id}")
def admin_order_detail(order_id: int, user: models.User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(404, "找不到此訂單")
    if user.role == "merchant-admin":
        pkg = db.get(Package, order.package_id) if order.package_id else None
        if not pkg or pkg.shop_id not in _merchant_admin_shop_ids(db, user):
            raise HTTPException(403, "沒有權限")
    elif user.role not in ("finance", "auditor", "admin"):
        raise HTTPException(403, "沒有權限")
    return {"order": _admin_order_out(db, order, user)}


@router.post("/admin/orders/{order_id}/refund")
def admin_refund_order(order_id: int, body: schemas_packages.RefundIn,
                       user: models.User = Depends(admin_only), db: Session = Depends(get_db)):
    reason = body.reason.strip()
    if not 1 <= len(reason) <= 220:
        raise HTTPException(400, "請填寫退款原因（最多 220 字）")
    order = _lock_order(db, order_id)
    if order is None:
        raise HTTPException(404, "找不到此訂單")
    _log(db, user.id, "request_refund", "order", order_id, reason)
    return _refund_order_checked(db, order, reason)


# ---------- commission rates (F5) ----------
def _commission_rate_out(db: Session, row: MerchantCommissionRate) -> dict:
    shop = db.query(models.Shop).filter(models.Shop.id == row.shop_id).first()
    return {
        "id": row.id, "shop_id": row.shop_id,
        "shop_name": shop.name if shop else "",
        "rate": _money(row.rate), "set_by": row.set_by,
        "created_at": row.created_at, "updated_at": row.updated_at,
    }


@router.post("/admin/commission-rates", status_code=201)
def set_commission_rate(body: schemas_line_f.CommissionRateSetIn,
                        user: models.User = Depends(
                            require_perm("commission_rates.write")),
                        db: Session = Depends(get_db)):
    """Set a shop's commission rate (finance / admin only).

    One row per shop: re-setting updates the existing row. The rate is
    the single source of truth generate_settlement reads from.
    """
    shop = get_shop_or_404(db, body.shop_id)
    rate = Decimal(str(body.rate))
    row = (db.query(MerchantCommissionRate)
           .filter(MerchantCommissionRate.shop_id == shop.id).first())
    created = row is None
    if created:
        row = MerchantCommissionRate(shop_id=shop.id, rate=rate,
                                     set_by=user.id)
        db.add(row)
    else:
        row.rate = rate
        row.set_by = user.id
    db.flush()
    _log(db, user.id, "commission_rate.set", "merchant_commission_rate",
         row.id, f"shop_id={shop.id}; rate={rate}; set_by={user.id}")
    db.commit()
    db.refresh(row)
    return {"message": "商户佣金率已设置" if created else "商户佣金率已更新",
            "commission_rate": _commission_rate_out(db, row)}


@router.get("/admin/commission-rates")
def list_commission_rates(shop_id: int = Query(None),
                          page: int = Query(1, ge=1),
                          user: models.User = Depends(
                              require_perm("commission_rates.read")),
                          db: Session = Depends(get_db)):
    """List configured commission rates (finance / auditor / admin)."""
    q = db.query(MerchantCommissionRate).order_by(
        MerchantCommissionRate.shop_id)
    if shop_id:
        q = q.filter(MerchantCommissionRate.shop_id == shop_id)
    out = _page([_commission_rate_out(db, r) for r in q.all()], page)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "commission_rates": out["items"]}


# ---------- settlements ----------
@router.post("/admin/settlements/generate", status_code=201)
def generate_settlement(body: schemas_line_f.SettlementGenerateIn,
                        user: models.User = Depends(
                            require_perm("settlements.write")),
                        db: Session = Depends(get_db)):
    shop = get_shop_or_404(db, body.shop_id)
    lock_shop(db, shop.id)
    # F5: the effective rate comes from the shop's configured rate,
    # else the platform default 10%. An explicitly passed
    # commission_rate must equal the effective rate, otherwise the
    # request is rejected — the request body can no longer set an
    # arbitrary rate (e.g. finance passing 0%).
    configured = (db.query(MerchantCommissionRate)
                  .filter(MerchantCommissionRate.shop_id == shop.id)
                  .first())
    if configured is not None:
        rate = Decimal(str(configured.rate))
        rate_from_default = False
    else:
        rate = DEFAULT_COMMISSION_RATE
        rate_from_default = True
    if (body.commission_rate is not None
            and Decimal(str(body.commission_rate)) != rate):
        raise HTTPException(
            status_code=400,
            detail="传入的佣金率与生效费率不一致（生效费率 %s）" % rate)
    existing = (db.query(Settlement)
                .filter(Settlement.shop_id == shop.id,
                        Settlement.period == body.period).first())
    if existing is not None:
        raise HTTPException(status_code=409, detail="此商户此結算週期已有結算單")
    start, end = _period_bounds(body.period)
    settled_ids = {i.redemption_id for i in
                   db.query(SettlementItem.redemption_id).all()}
    redemptions = (db.query(models.Redemption)
                   .filter(models.Redemption.shop_id == shop.id,
                           models.Redemption.scanned_at >= start,
                           models.Redemption.scanned_at < end,
                           # M6: revoked redemptions never settle.
                           models.Redemption.revoked_at.is_(None))
                   .order_by(models.Redemption.id).all())
    fresh = [r for r in redemptions if r.id not in settled_ids]
    gross = Decimal("0")
    items = []
    for r in fresh:
        coupon = db.query(models.Coupon).filter(
            models.Coupon.id == r.coupon_id).first()
        # M6: one shared basis (see app.settlement_basis). None means
        # the redemption must not settle (its package order was
        # refunded) — skip it entirely, do not count it.
        amount = settlement_basis(db, coupon, redemption=r)
        if amount is None:
            continue
        gross += amount
        items.append((r, amount))
    commission = sum(((amount * rate).quantize(CENT) for _, amount in items), Decimal("0"))
    net = gross - commission
    settlement = Settlement(
        shop_id=shop.id, period=body.period, period_start=start,
        period_end=end, redemption_count=len(items), gross_amount=gross,
        commission_rate=rate, commission=commission, net_amount=net,
        status="pending", generated_by=user.id)
    db.add(settlement)
    try:
        db.flush()
    except IntegrityError:
        # F4: a concurrent generate for the same (shop, period) passed
        # the pre-check above and committed first; the unique
        # constraint uq_settlement_shop_period rejects this second
        # insert. That is a duplicate-generation conflict, not a server
        # error: roll back and report 409 so the caller can query the
        # settlement that already exists.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="此商户此結算週期已有結算單（重複生成衝突），請查詢已有結算單")
    for r, amount in items:
        db.add(SettlementItem(
            settlement_id=settlement.id, redemption_id=r.id,
            coupon_id=r.coupon_id, amount=amount,
            commission=(amount * rate).quantize(Decimal("0.01"))))
    _log(db, user.id, "settlement.generate", "settlement", settlement.id,
         f"shop_id={shop.id}; period={body.period}; "
         f"redemptions={len(items)}; gross={gross}; rate={rate}"
         + ("; 无商户费率配置，使用默认 10%" if rate_from_default
            else "; 使用商户配置费率"))
    db.commit()
    db.refresh(settlement)
    out = _settlement_out(db, settlement)
    out["items"] = [{"redemption_id": r.id, "coupon_id": r.coupon_id,
                     "amount": _money(a)} for r, a in items]
    return {"message": "結算單已生成", "settlement": out}


@router.get("/admin/settlements")
def list_settlements(shop_id: int = Query(None),
                     status: str = Query(None),
                     page: int = Query(1, ge=1),
                     user: models.User = Depends(
                         require_perm("settlements.read")),
                     db: Session = Depends(get_db)):
    q = db.query(Settlement).order_by(Settlement.period.desc(),
                                      Settlement.id.desc())
    if shop_id:
        q = q.filter(Settlement.shop_id == shop_id)
    if status in ("pending", "approved", "disputed", "paid"):
        q = q.filter(Settlement.status == status)
    out = _page([_settlement_out(db, s) for s in q.all()], page)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "settlements": out["items"], "can_write": has_perm(user, "settlements.write")}


@router.get("/admin/settlements/{settlement_id}")
def get_settlement(settlement_id: int,
                   user: models.User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    s = db.query(Settlement).filter(Settlement.id == settlement_id).first()
    if s is not None and not can_access_shop(db, user, db.get(models.Shop, s.shop_id)):
        raise HTTPException(403, "沒有權限查看此結算單")
    if s is None:
        raise HTTPException(status_code=404, detail="找不到此結算單")
    out = _settlement_out(db, s)
    out["items"] = []
    for item, reversal in db.query(SettlementItem, SettlementReversal).outerjoin(
            SettlementReversal, SettlementReversal.item_id == SettlementItem.id).filter(
            SettlementItem.settlement_id == s.id).order_by(SettlementItem.id):
        out["items"].append({"id": item.id, "redemption_id": item.redemption_id,
                             "coupon_id": item.coupon_id, "amount": _money(item.amount),
                             "commission": _money(item.commission), "created_at": item.created_at,
                             "reversal": {"amount": _money(reversal.amount), "commission": _money(reversal.commission),
                                          "reason": reversal.reason, "created_at": reversal.created_at} if reversal else None})
    out["invoices"] = [_invoice_out(db, invoice) for invoice in
                       db.query(Invoice).filter_by(settlement_id=s.id).order_by(Invoice.id)]
    out["transfers"] = [{"kind": t.kind, "reference": t.reference, "amount": _money(t.amount),
                         "created_at": t.created_at} for t in db.query(SettlementTransfer).filter_by(
                             settlement_id=s.id).order_by(SettlementTransfer.id)]
    return {"settlement": out}


@router.post("/admin/settlements/{settlement_id}/approve")
def approve_settlement(settlement_id: int,
                       user: models.User = Depends(
                           require_perm("settlements.write")),
                       db: Session = Depends(get_db)):
    # F3: lock the settlement row before the status check (same as pay
    # since FIX-3) so a concurrent transition blocks here and sees the
    # fresh status instead of overwriting it.
    s = lock_statement(db, settlement_id)
    if s is None:
        raise HTTPException(status_code=404, detail="找不到此結算單")
    if s.status != "pending":
        raise HTTPException(status_code=400, detail="只有待審核的結算單可以確認")
    s.status = "approved"
    s.decided_by = user.id
    s.decided_at = _now()
    _log(db, user.id, "settlement.approve", "settlement", s.id,
         f"period={s.period}; net={s.net_amount}")
    db.commit()
    return {"message": "結算單已確認", "settlement": _settlement_out(db, s)}


@router.post("/admin/settlements/{settlement_id}/dispute")
def dispute_settlement(settlement_id: int,
                       body: schemas_line_f.SettlementDisputeIn,
                       user: models.User = Depends(
                           require_perm("settlements.write")),
                       db: Session = Depends(get_db)):
    # F3: lock the settlement row before the status check (same as pay
    # since FIX-3) so a concurrent pay cannot commit under this read
    # and later be reverted to disputed.
    s = lock_statement(db, settlement_id)
    if s is None:
        raise HTTPException(status_code=404, detail="找不到此結算單")
    if s.status not in ("pending", "approved"):
        raise HTTPException(status_code=400, detail="此狀態的結算單不能提出爭議")
    s.status = "disputed"
    s.dispute_reason = body.reason.strip()
    s.decided_by = user.id
    s.decided_at = _now()
    _log(db, user.id, "settlement.dispute", "settlement", s.id,
         f"reason={s.dispute_reason}")
    db.commit()
    return {"message": "結算單已標記為爭議", "settlement": _settlement_out(db, s)}


@router.post("/admin/settlements/{settlement_id}/resolve")
def resolve_settlement(settlement_id: int,
                       body: schemas_line_f.SettlementResolveIn,
                       user: models.User = Depends(
                           require_perm("settlements.write")),
                       db: Session = Depends(get_db)):
    # F3: lock the settlement row before the status check (same as pay
    # since FIX-3) so a concurrent transition blocks here and sees the
    # fresh status instead of overwriting it.
    s = lock_statement(db, settlement_id)
    if s is None:
        raise HTTPException(status_code=404, detail="找不到此結算單")
    if s.status != "disputed":
        raise HTTPException(status_code=400, detail="只有爭議中的結算單可以處理")
    s.status = "approved" if body.decision == "approve" else "pending"
    s.decided_by = user.id
    s.decided_at = _now()
    _log(db, user.id, "settlement.resolve", "settlement", s.id,
         f"decision={body.decision}; note={body.note}")
    db.commit()
    return {"message": "爭議已處理，結算單回到" + ("已確認" if body.decision == "approve" else "待審核"),
            "settlement": _settlement_out(db, s)}


def _hold_legacy_cash_voucher_payment(db: Session, settlement_id: int) -> None:
    """Require finance reconciliation before paying an old cash-discount line."""
    line = (db.query(SettlementItem.id)
            .join(models.Redemption, models.Redemption.id == SettlementItem.redemption_id)
            .join(models.Coupon, models.Coupon.id == models.Redemption.coupon_id)
            .outerjoin(models.WalletItem, models.WalletItem.id == models.Redemption.wallet_item_id)
            .outerjoin(Order, Order.id == models.WalletItem.order_id)
            .outerjoin(SettlementReversal, SettlementReversal.item_id == SettlementItem.id)
            .filter(SettlementItem.settlement_id == settlement_id,
                    SettlementItem.amount > 0,
                    func.lower(func.trim(models.Coupon.coupon_type)) == "cash",
                    Order.id.is_(None),
                    SettlementReversal.item_id.is_(None))
            .first())
    if line is not None:
        raise HTTPException(409, "舊結算單含現金券折扣，請先完成對賬及調整")


@router.post("/admin/settlements/{settlement_id}/pay")
def pay_settlement(settlement_id: int,
                   body: schemas_line_f.SettlementTransferIn | None = None,
                   user: models.User = Depends(
                       require_perm("settlements.write")),
                   db: Session = Depends(get_db)):
    # FIX-3: lock the settlement row before the status check so a
    # second concurrent pay blocks here and sees status == "paid".
    s = lock_statement(db, settlement_id)
    if s is None:
        raise HTTPException(status_code=404, detail="找不到此結算單")
    reference = f"settlement-{s.id}" if body is None else body.reference.strip()
    if not reference:
        raise HTTPException(400, "請填寫付款憑證編號")
    if body is not None:
        prior = db.query(SettlementTransfer).filter_by(settlement_id=s.id, reference=reference).first()
        if prior and prior.kind == "payment" and prior.amount == body.amount:
            return {"message": "已登記", "settlement": _settlement_out(db, s)}
        if prior or body.amount != s.net_amount:
            raise HTTPException(409, "付款金額或憑證已變更，請重新核對")
    if s.status != "approved":
        raise HTTPException(status_code=400, detail="只有已確認的結算單可以付款")
    _hold_legacy_cash_voucher_payment(db, s.id)
    s.status = "paid"
    db.add(SettlementTransfer(settlement_id=s.id, amount=s.net_amount, kind="payment",
                              reference=reference, actor_id=user.id))
    s.paid_at = _now()
    s.decided_by = user.id
    s.decided_at = _now()
    _log(db, user.id, "settlement.pay", "settlement", s.id,
         f"period={s.period}; net={s.net_amount}")
    db.commit()
    return {"message": "結算單已付款", "settlement": _settlement_out(db, s)}


@router.post("/admin/settlements/{settlement_id}/transfers")
def record_balance_transfer(settlement_id: int, body: schemas_line_f.SettlementTransferIn,
                            user: models.User = Depends(require_perm("settlements.write")),
                            db: Session = Depends(get_db)):
    s = lock_statement(db, settlement_id)
    if s is None:
        raise HTTPException(404, "找不到此結算單")
    reference = body.reference.strip()
    if not reference:
        raise HTTPException(400, "請填寫付款或收回憑證編號")
    amount = body.amount
    previous = db.query(SettlementTransfer).filter_by(settlement_id=s.id, reference=reference).first()
    if previous:
        if previous.amount != amount or previous.kind == "payment":
            raise HTTPException(409, "憑證編號已用於其他流水")
        return {"settlement": _settlement_out(db, s), "message": "已登記"}
    _, outstanding = cash_totals(db, s)
    if s.status != "paid" or not amount or amount != outstanding:
        raise HTTPException(409, "待處理金額已變更，請重新核對")
    kind = "recovery" if amount < 0 else "supplement"
    db.add(SettlementTransfer(settlement_id=s.id, amount=amount, kind=kind, reference=reference, actor_id=user.id))
    _log(db, user.id, "settlement." + kind, "settlement", s.id, f"amount={amount}; reference={reference}")
    db.commit()
    return {"settlement": _settlement_out(db, s), "message": "流水已登記"}


@router.get("/merchant/settlements")
def own_statements(page: int = Query(1, ge=1), user: models.User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    shops = _merchant_admin_shop_ids(db, user)
    rows = db.query(Settlement).filter(Settlement.shop_id.in_(shops)).order_by(Settlement.id.desc())
    return {"settlements": [_settlement_out(db, s) for s in rows.offset((page-1)*20).limit(20)],
            "total": rows.count()}


@router.get("/merchant/shops/{shop_id}/settlements")
def merchant_settlements(shop_id: int, page: int = Query(1, ge=1),
                         user: models.User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    shop = get_shop_or_404(db, shop_id)
    if not can_access_shop(db, user, shop):
        raise HTTPException(status_code=403, detail="沒有權限查看此商户")
    q = (db.query(Settlement).filter(Settlement.shop_id == shop.id)
         .order_by(Settlement.period.desc(), Settlement.id.desc()))
    out = _page([_settlement_out(db, s) for s in q.all()], page)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "settlements": out["items"]}


# ---------- reconciliation ----------
def _discrepancy_out(d: ReconciliationDiscrepancy) -> dict:
    return {
        "id": d.id, "report_id": d.report_id,
        "discrepancy_type": d.discrepancy_type,
        "redemption_id": d.redemption_id,
        "settlement_item_id": d.settlement_item_id,
        "expected_amount": _money(d.expected_amount),
        "actual_amount": _money(d.actual_amount),
        "status": d.status, "resolution": d.resolution or "",
        "resolved_by": d.resolved_by, "resolved_at": d.resolved_at,
        "created_at": d.created_at,
    }


def _report_out(db: Session, rep: ReconciliationReport,
                with_items: bool = False) -> dict:
    shop = db.query(models.Shop).filter(models.Shop.id == rep.shop_id).first()
    out = {
        "id": rep.id, "shop_id": rep.shop_id,
        "shop_name": shop.name if shop else "",
        "period": rep.period, "settlement_id": rep.settlement_id,
        "redemption_count": rep.redemption_count,
        "settlement_line_count": rep.settlement_line_count,
        "financials": json.loads(rep.financial_snapshot) if rep.financial_snapshot else None,
        "discrepancy_count": rep.discrepancy_count,
        "status": rep.status, "created_at": rep.created_at,
        "updated_at": rep.updated_at,
    }
    if with_items:
        out["discrepancies"] = [
            _discrepancy_out(d) for d in
            db.query(ReconciliationDiscrepancy)
            .filter(ReconciliationDiscrepancy.report_id == rep.id)
            .order_by(ReconciliationDiscrepancy.id).all()]
    return out


@router.post("/admin/reconciliation/run", status_code=201)
def run_reconciliation(body: schemas_line_f.ReconciliationRunIn,
                       user: models.User = Depends(
                           require_perm("reconciliation.write")),
                       db: Session = Depends(get_db)):
    shop = get_shop_or_404(db, body.shop_id)
    lock_shop(db, shop.id)
    start, end = _period_bounds(body.period)
    settlement = (db.query(Settlement)
                  .filter(Settlement.shop_id == shop.id,
                          Settlement.period == body.period).first())
    redemptions = (db.query(models.Redemption)
                   .filter(models.Redemption.shop_id == shop.id,
                           models.Redemption.scanned_at >= start,
                           models.Redemption.scanned_at < end)
                   .order_by(models.Redemption.id).all())
    items = []
    if settlement is not None:
        settlement = lock_statement(db, settlement.id)
        items = (db.query(SettlementItem)
                 .filter(SettlementItem.settlement_id == settlement.id)
                 .order_by(SettlementItem.id).all())
    item_by_redemption = {i.redemption_id: i for i in items}
    redemption_by_id = {r.id: r for r in redemptions}
    # M6: this query deliberately does NOT filter revoked_at out —
    # a revoked redemption WITH a settlement line is the
    # revoked_but_settled discrepancy below. Revoked redemptions
    # without a line are correctly excluded (no missing-line row).
    found = []
    for r in redemptions:
        if r.id not in item_by_redemption:
            if r.revoked_at is not None:
                continue
            coupon = db.query(models.Coupon).filter(
                models.Coupon.id == r.coupon_id).first()
            expected = settlement_basis(db, coupon, redemption=r)
            if expected is None:
                continue  # refunded order: correctly excluded
            found.append(("missing_settlement_line", r.id, None,
                          expected, None))
    reversals = {rev.item_id: rev for rev in db.query(SettlementReversal).join(SettlementItem).filter(
        SettlementItem.settlement_id == settlement.id)} if settlement else {}
    for i in items:
        correct_fee = (i.amount * settlement.commission_rate).quantize(CENT) + i.rounding_adjustment
        if correct_fee != i.commission:
            found.append(("commission_mismatch", i.redemption_id, i.id, correct_fee, i.commission))
        if i.redemption_id not in redemption_by_id:
            found.append(("missing_redemption", i.redemption_id, i.id,
                          None, i.amount))
        else:
            r = redemption_by_id[i.redemption_id]
            reversal = reversals.get(i.id)
            if reversal:
                if r.revoked_at is None and settlement_basis(db, redemption=r) is not None:
                    found.append(("unexpected_reversal", r.id, i.id, 0, reversal.amount))
                if reversal.commission != -i.commission:
                    found.append(("reversed_commission_mismatch", r.id, i.id, -i.commission, reversal.commission))
                if reversal.amount != -i.amount:
                    found.append(("incorrect_reversal", r.id, i.id, -i.amount, reversal.amount))
                continue
            if r.revoked_at is not None:
                # M6: settled, then revoked — the line should not exist.
                found.append(("revoked_but_settled", r.id, i.id,
                              Decimal("0"), i.amount))
                continue
            coupon = db.query(models.Coupon).filter(
                models.Coupon.id == r.coupon_id).first()
            expected = settlement_basis(db, coupon, redemption=r)
            if expected is None:
                # M6: order refunded after settlement — the settled
                # amount should not have been settled; flag for manual
                # handling instead of silently rewriting the line.
                found.append(("amount_mismatch", r.id, i.id,
                              Decimal("0"), i.amount))
            elif Decimal(str(i.amount)) != expected:
                found.append(("amount_mismatch", r.id, i.id,
                              expected, i.amount))
    if settlement:
        gross = sum(i.amount for i in items) + sum(r.amount for r in reversals.values())
        fee = sum(i.commission for i in items) + sum(r.commission for r in reversals.values())
        payments = db.query(SettlementTransfer).filter_by(settlement_id=settlement.id, kind="payment").count()
        expected_payments = int(settlement.status == "paid")
        if payments != expected_payments:
            found.append(("payment_entry_count", None, None, expected_payments, payments))
        invoice_totals = _invoice_totals(db, settlement)
        issued_net = Decimal(str(invoice_totals["invoice_net_issued_amount"]))
        if issued_net > settlement.net_amount:
            found.append(("invoice_over_settlement", None, None, settlement.net_amount, issued_net))
        for label, expected, actual in (("count", len(items) - len(reversals), settlement.redemption_count),
                                        ("gross", gross, settlement.gross_amount),
                                        ("commission", fee, settlement.commission),
                                        ("net", gross - fee, settlement.net_amount)):
            if expected != actual:
                found.append((label + "_total_mismatch", None, None, expected, actual))
    report = (db.query(ReconciliationReport)
              .filter(ReconciliationReport.shop_id == shop.id,
                      ReconciliationReport.period == body.period).first())
    if report is None:
        report = ReconciliationReport(shop_id=shop.id, period=body.period)
        db.add(report)
        db.flush()
        resolved_rows = []
    else:
        # Keep the resolution trail. A changed amount is a new discrepancy.
        resolved_rows = (db.query(ReconciliationDiscrepancy).filter(
            ReconciliationDiscrepancy.report_id == report.id,
            ReconciliationDiscrepancy.status == "resolved").all())
        db.query(ReconciliationDiscrepancy).filter(
            ReconciliationDiscrepancy.report_id == report.id,
            ReconciliationDiscrepancy.status == "open").delete()
        db.flush()
    resolved_keys = {(d.discrepancy_type, d.redemption_id,
                      d.settlement_item_id, d.expected_amount, d.actual_amount) for d in resolved_rows}
    new_found = [f for f in found
                 if f[0] == "invoice_over_settlement" or f not in resolved_keys]
    total_discrepancies = len(resolved_rows) + len(new_found)
    report.settlement_id = settlement.id if settlement else None
    report.redemption_count = sum(r.revoked_at is None and settlement_basis(db, redemption=r) is not None for r in redemptions)
    report.settlement_line_count = len(items) - len(reversals)
    if settlement:
        transferred, balance = cash_totals(db, settlement)
        report.financial_snapshot = json.dumps({"gross_amount": float(gross), "commission": float(fee),
            "net_amount": float(gross - fee), "transferred_net": float(transferred),
            "outstanding_net": float(balance), "reversal_count": len(reversals), **invoice_totals})
    report.discrepancy_count = total_discrepancies
    report.status = ("no_settlement" if settlement is None else
                     "resolved" if not new_found else "open")
    for dtype, rid, iid, expected, actual in new_found:
        db.add(ReconciliationDiscrepancy(
            report_id=report.id, discrepancy_type=dtype,
            redemption_id=rid, settlement_item_id=iid,
            expected_amount=expected, actual_amount=actual, status="open"))
    _log(db, user.id, "reconciliation.run", "reconciliation_report",
         report.id, f"shop_id={shop.id}; period={body.period}; "
         f"discrepancies={total_discrepancies}")
    db.commit()
    db.refresh(report)
    return {"message": "對賬完成，共 %d 項差異" % total_discrepancies,
            "report": _report_out(db, report, with_items=True)}


@router.get("/admin/reconciliation/reports")
def list_reconciliation_reports(
        shop_id: int = Query(None), period: str = Query(None),
        page: int = Query(1, ge=1),
        user: models.User = Depends(require_perm("reconciliation.read")),
        db: Session = Depends(get_db)):
    q = db.query(ReconciliationReport).order_by(
        ReconciliationReport.period.desc(), ReconciliationReport.id.desc())
    if shop_id:
        q = q.filter(ReconciliationReport.shop_id == shop_id)
    if period:
        q = q.filter(ReconciliationReport.period == period)
    out = _page([_report_out(db, r) for r in q.all()], page)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "reports": out["items"]}


@router.get("/admin/reconciliation/reports/{report_id}")
def get_reconciliation_report(
        report_id: int,
        user: models.User = Depends(require_perm("reconciliation.read")),
        db: Session = Depends(get_db)):
    rep = db.query(ReconciliationReport).filter(
        ReconciliationReport.id == report_id).first()
    if rep is None:
        raise HTTPException(status_code=404, detail="找不到此對賬報告")
    return {"report": _report_out(db, rep, with_items=True)}


@router.post("/admin/reconciliation/discrepancies/{discrepancy_id}/resolve")
def resolve_discrepancy(
        discrepancy_id: int,
        body: schemas_line_f.DiscrepancyResolveIn,
        user: models.User = Depends(require_perm("reconciliation.write")),
        db: Session = Depends(get_db)):
    d = db.query(ReconciliationDiscrepancy).filter(
        ReconciliationDiscrepancy.id == discrepancy_id).first()
    if d is None:
        raise HTTPException(status_code=404, detail="找不到此差異記錄")
    if d.status != "open":
        raise HTTPException(status_code=400, detail="此差異已處理")
    d.status = "resolved"
    d.resolution = body.resolution.strip()
    d.resolved_by = user.id
    d.resolved_at = _now()
    _log(db, user.id, "reconciliation.resolve", "reconciliation_discrepancy",
         d.id, f"type={d.discrepancy_type}; resolution={d.resolution}")
    rep = db.query(ReconciliationReport).filter(
        ReconciliationReport.id == d.report_id).first()
    if rep is not None:
        open_left = db.query(ReconciliationDiscrepancy).filter(
            ReconciliationDiscrepancy.report_id == rep.id,
            ReconciliationDiscrepancy.status == "open").count()
        if open_left == 0 and rep.status in ("open", "no_settlement"):
            # F7: a no_settlement report whose discrepancies are all
            # resolved is done too — it must not stay no_settlement.
            rep.status = "resolved"
    db.commit()
    return {"message": "差異已處理", "discrepancy": _discrepancy_out(d)}


@router.get("/merchant/shops/{shop_id}/reconciliation")
def merchant_reconciliation(shop_id: int, page: int = Query(1, ge=1),
                            user: models.User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    shop = get_shop_or_404(db, shop_id)
    if not can_access_shop(db, user, shop):
        raise HTTPException(status_code=403, detail="沒有權限查看此商户")
    q = (db.query(ReconciliationReport)
         .filter(ReconciliationReport.shop_id == shop.id)
         .order_by(ReconciliationReport.period.desc(),
                   ReconciliationReport.id.desc()))
    out = _page([_report_out(db, r) for r in q.all()], page)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "reports": out["items"]}


# ---------- invoices ----------
@router.post("/admin/invoices", status_code=201)
def create_invoice(body: schemas_line_f.InvoiceCreateIn,
                   user: models.User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    shop = get_shop_or_404(db, body.shop_id)
    if not has_perm(user, "invoices.write"):
        # F1: do NOT fall back to can_access_shop here — it is
        # unconditionally True for auditor/finance, which let an auditor
        # create invoices for any shop. Only the shop owner or a
        # shop_members member may request without invoices.write.
        if not can_request_invoice_for_shop(db, user, shop):
            raise HTTPException(status_code=403, detail="沒有權限為此商户申請發票")
    settlement = None
    if body.settlement_id is not None:
        # FIX-3: lock the settlement row before summing already-invoiced
        # amounts, so a concurrent invoice for the same settlement
        # blocks here and the cap is re-checked on fresh totals.
        settlement = lock_statement(db, body.settlement_id)
        if settlement is None:
            raise HTTPException(status_code=404, detail="找不到此結算單")
        if settlement.shop_id != shop.id:
            raise HTTPException(status_code=400, detail="結算單不屬於此商户")
        totals = _invoice_totals(db, settlement)
        if body.amount > Decimal(str(totals["invoice_available_amount"])):
            raise HTTPException(status_code=400, detail="開票金額超過結算單可開票餘額")
    inv = Invoice(
        shop_id=shop.id, settlement_id=body.settlement_id,
        user_id=user.id, amount=Decimal(str(body.amount)),
        title=body.title.strip(), tax_id=body.tax_id,
        invoice_type=body.invoice_type, status="pending")
    db.add(inv)
    db.flush()
    _log(db, user.id, "invoice.create", "invoice", inv.id,
         f"shop_id={shop.id}; amount={body.amount}")
    db.commit()
    db.refresh(inv)
    return {"message": "開票申請已提交", "invoice": _invoice_out(db, inv)}


@router.get("/admin/invoices")
def list_invoices(shop_id: int = Query(None), status: str = Query(None),
                  page: int = Query(1, ge=1),
                  user: models.User = Depends(require_perm("invoices.read")),
                  db: Session = Depends(get_db)):
    q = db.query(Invoice).order_by(Invoice.created_at.desc(),
                                   Invoice.id.desc())
    if shop_id:
        q = q.filter(Invoice.shop_id == shop_id)
    if status in ("pending", "approved", "rejected", "issued"):
        q = q.filter(Invoice.status == status)
    out = _page([_invoice_out(db, i) for i in q.all()], page)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "invoices": out["items"], "can_write": has_perm(user, "invoices.write")}


@router.get("/admin/invoices/{invoice_id}")
def get_invoice(invoice_id: int,
                user: models.User = Depends(require_perm("invoices.read")),
                db: Session = Depends(get_db)):
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if inv is None:
        raise HTTPException(status_code=404, detail="找不到此發票申請")
    return {"invoice": _invoice_out(db, inv)}


def _check_invoice_limit(db, inv):
    if inv.settlement_id is None:
        return
    statement = lock_statement(db, inv.settlement_id)
    totals = _invoice_totals(db, statement)
    # Pending applications reserve capacity for creation, while approval
    # compares only other approved requests and issued net invoices.
    approved = db.query(func.coalesce(func.sum(Invoice.amount), 0)).filter(
        Invoice.settlement_id == statement.id, Invoice.id != inv.id,
        Invoice.status == "approved").scalar()
    if Decimal(str(totals["invoice_net_issued_amount"])) + approved + inv.amount > statement.net_amount:
        raise HTTPException(409, "結算金額已調整，請核對發票金額")



def _lock_invoice(db, invoice_id):
    # SQLite ignores FOR UPDATE. A no-op write must precede the status read.
    if db.get_bind().dialect.name == "sqlite":
        db.query(Invoice).filter_by(id=invoice_id).update(
            {"status": Invoice.status, "updated_at": Invoice.updated_at},
            synchronize_session=False)
    return db.query(Invoice).filter_by(id=invoice_id).with_for_update().populate_existing().first()


@router.post("/admin/invoices/{invoice_id}/approve")
def approve_invoice(invoice_id: int,
                    user: models.User = Depends(
                        require_perm("invoices.write")),
                    db: Session = Depends(get_db)):
    inv = _lock_invoice(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="找不到此發票申請")
    if inv.status != "pending":
        raise HTTPException(status_code=400, detail="只有待審核的發票可以批准")
    _check_invoice_limit(db, inv)
    inv.status = "approved"
    inv.decided_by = user.id
    inv.decided_at = _now()
    _log(db, user.id, "invoice.approve", "invoice", inv.id,
         f"amount={inv.amount}")
    db.commit()
    return {"message": "發票申請已批准", "invoice": _invoice_out(db, inv)}


@router.post("/admin/invoices/{invoice_id}/reject")
def reject_invoice(invoice_id: int,
                   body: schemas_line_f.InvoiceRejectIn,
                   user: models.User = Depends(
                       require_perm("invoices.write")),
                   db: Session = Depends(get_db)):
    inv = _lock_invoice(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="找不到此發票申請")
    if inv.status not in ("pending", "approved"):
        raise HTTPException(status_code=400, detail="只有待審核或已批准的發票可以拒絕")
    inv.status = "rejected"
    inv.reject_reason = body.reason.strip()
    inv.decided_by = user.id
    inv.decided_at = _now()
    _log(db, user.id, "invoice.reject", "invoice", inv.id,
         f"reason={inv.reject_reason}")
    db.commit()
    return {"message": "發票申請已拒絕", "invoice": _invoice_out(db, inv)}


@router.post("/admin/invoices/{invoice_id}/issue")
def issue_invoice(invoice_id: int,
                  user: models.User = Depends(
                      require_perm("invoices.write")),
                  db: Session = Depends(get_db)):
    inv = _lock_invoice(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="找不到此發票申請")
    if inv.status != "approved":
        raise HTTPException(status_code=400, detail="只有已批准的發票可以開具")
    _check_invoice_limit(db, inv)
    inv.status = "issued"
    inv.issued_at = _now()
    _log(db, user.id, "invoice.issue", "invoice", inv.id,
         f"amount={inv.amount}")
    db.commit()
    return {"message": "發票已開具", "invoice": _invoice_out(db, inv)}


@router.post("/admin/invoices/{invoice_id}/credits")
def record_invoice_credit(invoice_id: int, body: schemas_line_f.InvoiceCreditIn,
                          user: models.User = Depends(require_perm("invoices.write")),
                          db: Session = Depends(get_db)):
    inv = _lock_invoice(db, invoice_id)
    if inv is None:
        raise HTTPException(404, "找不到此發票")
    if inv.settlement_id is not None:
        lock_statement(db, inv.settlement_id)
    reference = body.reference.strip()
    if not reference:
        raise HTTPException(400, "請填寫沖紅憑證編號")
    previous = db.query(InvoiceCredit).filter_by(invoice_id=inv.id, reference=reference).first()
    if previous:
        if previous.amount != body.amount:
            raise HTTPException(409, "憑證編號已用於其他金額")
        return {"message": "已登記", "invoice": _invoice_out(db, inv)}
    if inv.status != "issued":
        raise HTTPException(400, "只有已開具的發票可以登記沖紅")
    credited = db.query(func.coalesce(func.sum(InvoiceCredit.amount), 0)).filter_by(invoice_id=inv.id).scalar()
    if credited + body.amount > inv.amount:
        raise HTTPException(409, "沖紅金額超過原發票未沖紅金額")
    db.add(InvoiceCredit(invoice_id=inv.id, amount=body.amount, reference=reference, actor_id=user.id))
    _log(db, user.id, "invoice.credit", "invoice", inv.id, f"amount={body.amount}; reference={reference}")
    db.commit()
    return {"message": "沖紅已登記", "invoice": _invoice_out(db, inv)}


@router.get("/merchant/shops/{shop_id}/invoices")
def merchant_invoices(shop_id: int, page: int = Query(1, ge=1),
                      user: models.User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    shop = get_shop_or_404(db, shop_id)
    if not can_access_shop(db, user, shop):
        raise HTTPException(status_code=403, detail="沒有權限查看此商户")
    q = (db.query(Invoice).filter(Invoice.shop_id == shop.id)
         .order_by(Invoice.created_at.desc(), Invoice.id.desc()))
    out = _page([_invoice_out(db, i) for i in q.all()], page)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "invoices": out["items"]}


# ---------- CMS: banners ----------
@router.get("/admin/banners")
def list_banners(status: str = Query(None), page: int = Query(1, ge=1),
                 user: models.User = Depends(require_perm("content.read")),
                 db: Session = Depends(get_db)):
    q = db.query(CmsBanner).order_by(CmsBanner.sort_order, CmsBanner.id)
    if status in ("draft", "active", "archived"):
        q = q.filter(CmsBanner.status == status)
    out = _page([_banner_out(b) for b in q.all()], page)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "banners": out["items"]}


@router.post("/admin/banners", status_code=201)
def create_banner(body: schemas_line_f.BannerIn,
                  user: models.User = Depends(require_perm("content.write")),
                  db: Session = Depends(get_db)):
    _check_window(body.start_at, body.end_at)
    b = CmsBanner(title=body.title.strip(), image=body.image,
                  link_type=body.link_type, link_target=body.link_target,
                  sort_order=body.sort_order, start_at=body.start_at,
                  end_at=body.end_at, status=body.status,
                  created_by=user.id)
    db.add(b)
    db.flush()
    _log(db, user.id, "banner.create", "cms_banner", b.id,
         f"title={b.title}; status={b.status}")
    db.commit()
    db.refresh(b)
    return {"message": "橫幅已建立", "banner": _banner_out(b)}


@router.put("/admin/banners/{banner_id}")
def update_banner(banner_id: int, body: schemas_line_f.BannerIn,
                  user: models.User = Depends(require_perm("content.write")),
                  db: Session = Depends(get_db)):
    b = db.query(CmsBanner).filter(CmsBanner.id == banner_id).first()
    if b is None:
        raise HTTPException(status_code=404, detail="找不到此橫幅")
    _check_window(body.start_at, body.end_at)
    b.title = body.title.strip()
    b.image = body.image
    b.link_type = body.link_type
    b.link_target = body.link_target
    b.sort_order = body.sort_order
    b.start_at = body.start_at
    b.end_at = body.end_at
    b.status = body.status
    _log(db, user.id, "banner.update", "cms_banner", b.id,
         f"title={b.title}; status={b.status}")
    db.commit()
    return {"message": "橫幅已更新", "banner": _banner_out(b)}


@router.delete("/admin/banners/{banner_id}")
def delete_banner(banner_id: int,
                  user: models.User = Depends(require_perm("content.write")),
                  db: Session = Depends(get_db)):
    b = db.query(CmsBanner).filter(CmsBanner.id == banner_id).first()
    if b is None:
        raise HTTPException(status_code=404, detail="找不到此橫幅")
    # Soft delete: archive instead of removing the row (keeps history).
    b.status = "archived"
    _log(db, user.id, "banner.archive", "cms_banner", b.id,
         f"title={b.title}")
    db.commit()
    return {"message": "橫幅已下架", "banner": _banner_out(b)}


@router.get("/banners")
def public_banners(db: Session = Depends(get_db)):
    """Public CMS banners: active and inside the display window.

    The v0.1 GET /advertisement-banners (hardcoded demo banners) is left
    untouched; the app home switches to this endpoint when CMS content
    is live.
    """
    now = _now()
    rows = (db.query(CmsBanner)
            .filter(CmsBanner.status == "active")
            .order_by(CmsBanner.sort_order, CmsBanner.id).all())
    live = [b for b in rows
            if (b.start_at is None or b.start_at <= now) and
            (b.end_at is None or b.end_at >= now)]
    return {"banners": [_banner_out(b) for b in live]}


# ---------- CMS: announcements ----------
@router.get("/admin/announcements")
def list_announcements(status: str = Query(None),
                       page: int = Query(1, ge=1),
                       user: models.User = Depends(
                           require_perm("content.read")),
                       db: Session = Depends(get_db)):
    q = db.query(CmsAnnouncement).order_by(CmsAnnouncement.sort_order,
                                           CmsAnnouncement.id)
    if status in ("draft", "active", "archived"):
        q = q.filter(CmsAnnouncement.status == status)
    out = _page([_announcement_out(a) for a in q.all()], page)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "announcements": out["items"]}


@router.post("/admin/announcements", status_code=201)
def create_announcement(body: schemas_line_f.AnnouncementIn,
                        user: models.User = Depends(
                            require_perm("content.write")),
                        db: Session = Depends(get_db)):
    _check_window(body.start_at, body.end_at)
    a = CmsAnnouncement(title=body.title.strip(), body=body.body,
                        status=body.status, sort_order=body.sort_order,
                        start_at=body.start_at, end_at=body.end_at,
                        created_by=user.id)
    db.add(a)
    db.flush()
    _log(db, user.id, "announcement.create", "cms_announcement", a.id,
         f"title={a.title}")
    db.commit()
    db.refresh(a)
    return {"message": "公告已建立", "announcement": _announcement_out(a)}


@router.put("/admin/announcements/{announcement_id}")
def update_announcement(announcement_id: int,
                        body: schemas_line_f.AnnouncementIn,
                        user: models.User = Depends(
                            require_perm("content.write")),
                        db: Session = Depends(get_db)):
    a = db.query(CmsAnnouncement).filter(
        CmsAnnouncement.id == announcement_id).first()
    if a is None:
        raise HTTPException(status_code=404, detail="找不到此公告")
    _check_window(body.start_at, body.end_at)
    a.title = body.title.strip()
    a.body = body.body
    a.status = body.status
    a.sort_order = body.sort_order
    a.start_at = body.start_at
    a.end_at = body.end_at
    _log(db, user.id, "announcement.update", "cms_announcement", a.id,
         f"title={a.title}; status={a.status}")
    db.commit()
    return {"message": "公告已更新", "announcement": _announcement_out(a)}


@router.delete("/admin/announcements/{announcement_id}")
def delete_announcement(announcement_id: int,
                        user: models.User = Depends(
                            require_perm("content.write")),
                        db: Session = Depends(get_db)):
    a = db.query(CmsAnnouncement).filter(
        CmsAnnouncement.id == announcement_id).first()
    if a is None:
        raise HTTPException(status_code=404, detail="找不到此公告")
    a.status = "archived"
    _log(db, user.id, "announcement.archive", "cms_announcement", a.id,
         f"title={a.title}")
    db.commit()
    return {"message": "公告已下架", "announcement": _announcement_out(a)}


@router.get("/announcements")
def public_announcements(db: Session = Depends(get_db)):
    now = _now()
    rows = (db.query(CmsAnnouncement)
            .filter(CmsAnnouncement.status == "active")
            .order_by(CmsAnnouncement.sort_order, CmsAnnouncement.id).all())
    live = [a for a in rows
            if (a.start_at is None or a.start_at <= now) and
            (a.end_at is None or a.end_at >= now)]
    return {"announcements": [_announcement_out(a) for a in live]}


# ---------- CMS: categories (reuses the v0.1 categories table) ----------
@router.get("/admin/categories")
def list_categories_admin(
        user: models.User = Depends(require_perm("content.read")),
        db: Session = Depends(get_db)):
    rows = db.query(models.Category).order_by(models.Category.id).all()
    return {"categories": [{
        "id": c.id, "name": c.name, "name_en": c.name_en or "",
        "icon": c.icon or "", "is_active": bool(c.is_active)} for c in rows]}


@router.post("/admin/categories", status_code=201)
def create_category(body: schemas_line_f.CategoryIn,
                    user: models.User = Depends(
                        require_perm("content.write")),
                    db: Session = Depends(get_db)):
    if db.query(models.Category).filter(
            models.Category.name == body.name.strip()).first() is not None:
        raise HTTPException(status_code=400, detail="已有同名分類")
    c = models.Category(name=body.name.strip(), name_en=body.name_en,
                        icon=body.icon, is_active=True)
    db.add(c)
    db.flush()
    _log(db, user.id, "category.create", "category", c.id,
         f"name={c.name}")
    db.commit()
    db.refresh(c)
    return {"message": "分類已建立", "category": {
        "id": c.id, "name": c.name, "name_en": c.name_en or "",
        "icon": c.icon or "", "is_active": bool(c.is_active)}}


@router.put("/admin/categories/{category_id}")
def update_category(category_id: int, body: schemas_line_f.CategoryIn,
                    user: models.User = Depends(
                        require_perm("content.write")),
                    db: Session = Depends(get_db)):
    c = db.query(models.Category).filter(
        models.Category.id == category_id).first()
    if c is None:
        raise HTTPException(status_code=404, detail="找不到此分類")
    c.name = body.name.strip()
    c.name_en = body.name_en
    c.icon = body.icon
    _log(db, user.id, "category.update", "category", c.id,
         f"name={c.name}")
    db.commit()
    return {"message": "分類已更新", "category": {
        "id": c.id, "name": c.name, "name_en": c.name_en or "",
        "icon": c.icon or "", "is_active": bool(c.is_active)}}


@router.post("/admin/categories/{category_id}/deactivate")
def deactivate_category(category_id: int,
                        user: models.User = Depends(
                            require_perm("content.write")),
                        db: Session = Depends(get_db)):
    c = db.query(models.Category).filter(
        models.Category.id == category_id).first()
    if c is None:
        raise HTTPException(status_code=404, detail="找不到此分類")
    c.is_active = False
    _log(db, user.id, "category.deactivate", "category", c.id,
         f"name={c.name}")
    db.commit()
    return {"message": "分類已停用", "category": {
        "id": c.id, "name": c.name, "name_en": c.name_en or "",
        "icon": c.icon or "", "is_active": bool(c.is_active)}}


@router.post("/admin/categories/{category_id}/activate")
def activate_category(category_id: int,
                      user: models.User = Depends(
                          require_perm("content.write")),
                      db: Session = Depends(get_db)):
    c = db.query(models.Category).filter(
        models.Category.id == category_id).first()
    if c is None:
        raise HTTPException(status_code=404, detail="找不到此分類")
    c.is_active = True
    _log(db, user.id, "category.activate", "category", c.id,
         f"name={c.name}")
    db.commit()
    return {"message": "分類已啟用", "category": {
        "id": c.id, "name": c.name, "name_en": c.name_en or "",
        "icon": c.icon or "", "is_active": bool(c.is_active)}}


# ---------- roles ----------
@router.get("/admin/roles")
def list_roles(user: models.User = Depends(admin_only)):
    return {"roles": ALL_ROLES, "permissions": role_permissions()}


@router.post("/admin/users/{user_id}/role")
def change_user_role(user_id: int, body: schemas_line_f.RoleChangeIn,
                     user: models.User = Depends(admin_only),
                     db: Session = Depends(get_db)):
    if body.role not in ALL_ROLES:
        raise HTTPException(status_code=400,
                            detail="未知角色：%s" % body.role)
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="找不到此用戶")
    if target.id == user.id:
        raise HTTPException(status_code=400, detail="不能修改自己的角色")
    if target.role == "admin" and body.role != "admin":
        admin_count = db.query(models.User).filter(
            models.User.role == "admin").count()
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="不能停用最後一位管理員")
    old_role = target.role
    target.role = body.role
    _log(db, user.id, "user.role.change", "user", target.id,
         f"{old_role} -> {body.role}")
    db.commit()
    return {"message": "角色已更新",
            "user": {"id": target.id, "email": target.email,
                     "role": target.role}}


# ---------- shop members ----------
@router.get("/admin/shops/{shop_id}/members")
def list_shop_members(shop_id: int,
                      user: models.User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    shop = get_shop_or_404(db, shop_id)
    # F9: members.read holders (finance/auditor/admin) plus the shop's
    # owner / active members (can_access_shop) may list THIS shop's
    # members; anyone else gets 403.
    if not has_perm(user, "members.read") and not can_access_shop(
            db, user, shop):
        raise HTTPException(status_code=403, detail="沒有權限")
    rows = (db.query(ShopMember)
            .filter(ShopMember.shop_id == shop.id)
            .order_by(ShopMember.id).all())
    out = []
    for m in rows:
        u = db.query(models.User).filter(models.User.id == m.user_id).first()
        out.append({"id": m.id, "shop_id": m.shop_id, "user_id": m.user_id,
                    "email": u.email if u else "",
                    "name": u.name if u else "",
                    "role": m.role, "status": m.status,
                    "invited_by": m.invited_by,
                    "invited_at": m.invited_at,
                    "created_at": m.created_at})
    return {"shop_id": shop.id, "shop_name": shop.name, "members": out}


@router.post("/admin/shops/{shop_id}/members", status_code=201)
def add_shop_member(shop_id: int, body: schemas_line_f.ShopMemberAddIn,
                    user: models.User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    shop = get_shop_or_404(db, shop_id)
    if user.role != "admin" and shop.vendor_id != user.id:
        raise HTTPException(status_code=403, detail="只有管理員或店主可以加店員")
    target = db.query(models.User).filter(
        models.User.id == body.user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="找不到此用戶")
    # F8: only a customer or an existing merchant-admin may be invited.
    # Platform roles (admin/finance/auditor/support/vendor) are never
    # pulled into shop staff.
    if target.role not in ("customer", "merchant-admin"):
        raise HTTPException(
            status_code=400,
            detail="只有 customer 或 merchant-admin 可以被邀請為店員")
    existing = (db.query(ShopMember)
                .filter(ShopMember.shop_id == shop.id,
                        ShopMember.user_id == target.id).first())
    if existing is not None:
        raise HTTPException(status_code=400, detail="此用戶已是本店店員")
    # F8: invite only — status=pending, NO immediate promotion. The
    # invitee confirms via POST .../members/{member_id}/confirm, and
    # only then (if they are a customer) become merchant-admin.
    member = ShopMember(shop_id=shop.id, user_id=target.id,
                        role="merchant-admin", added_by=user.id,
                        status="pending", invited_by=user.id,
                        invited_at=_now())
    db.add(member)
    db.flush()
    _log(db, user.id, "shop.member.invite", "shop_member", member.id,
         f"shop_id={shop.id}; user_id={target.id}")
    db.commit()
    db.refresh(member)
    return {"message": "邀請已發送，待對方確認", "member": {
        "id": member.id, "shop_id": member.shop_id,
        "user_id": member.user_id, "email": target.email,
        "name": target.name or "", "role": member.role,
        "status": member.status, "invited_by": member.invited_by,
        "invited_at": member.invited_at,
        "created_at": member.created_at}}


@router.post("/admin/shops/{shop_id}/members/{member_id}/confirm")
def confirm_shop_member(shop_id: int, member_id: int,
                        user: models.User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """F8: the invitee confirms their own invite. Only the invitee
    (member.user_id == caller) may call this; confirming flips the
    invite to active and promotes a customer to merchant-admin."""
    shop = get_shop_or_404(db, shop_id)
    member = (db.query(ShopMember)
              .filter(ShopMember.id == member_id,
                      ShopMember.shop_id == shop.id).first())
    if member is None:
        raise HTTPException(status_code=404, detail="找不到此邀請")
    if member.user_id != user.id:
        raise HTTPException(status_code=403, detail="只有被邀請人本人可以確認")
    if member.status != "pending":
        raise HTTPException(status_code=400, detail="此邀請已確認")
    target = db.query(models.User).filter(
        models.User.id == member.user_id).first()
    member.status = "active"
    if target is not None and target.role == "customer":
        target.role = "merchant-admin"
        _log(db, user.id, "user.role.change", "user", target.id,
             "customer -> merchant-admin (confirmed shop member invite)")
    _log(db, user.id, "shop.member.confirm", "shop_member", member.id,
         f"shop_id={shop.id}; user_id={member.user_id}")
    db.commit()
    db.refresh(member)
    return {"message": "已確認加入", "member": {
        "id": member.id, "shop_id": member.shop_id,
        "user_id": member.user_id,
        "email": target.email if target else "",
        "name": target.name if target else "", "role": member.role,
        "status": member.status, "invited_by": member.invited_by,
        "invited_at": member.invited_at,
        "created_at": member.created_at}}


@router.delete("/admin/shops/{shop_id}/members/{member_user_id}")
def remove_shop_member(shop_id: int, member_user_id: int,
                       user: models.User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    shop = get_shop_or_404(db, shop_id)
    if user.role != "admin" and shop.vendor_id != user.id:
        raise HTTPException(status_code=403, detail="只有管理員或店主可以移除店員")
    member = (db.query(ShopMember)
              .filter(ShopMember.shop_id == shop.id,
                      ShopMember.user_id == member_user_id).first())
    if member is None:
        raise HTTPException(status_code=404, detail="找不到此店員")
    target = db.query(models.User).filter(
        models.User.id == member_user_id).first()
    db.delete(member)
    db.flush()
    # F8: removing the last ACTIVE membership demotes a merchant-admin
    # back to customer; members of other shops keep the role.
    if target is not None and target.role == "merchant-admin":
        remaining = (db.query(ShopMember)
                     .filter(ShopMember.user_id == member_user_id,
                             ShopMember.status == "active").count())
        if remaining == 0:
            target.role = "customer"
            _log(db, user.id, "user.role.change", "user", target.id,
                 "merchant-admin -> customer "
                 "(last shop membership removed)")
    _log(db, user.id, "shop.member.remove", "shop_member", member.id,
         f"shop_id={shop.id}; user_id={member_user_id}")
    db.commit()
    return {"message": "店員已移除"}


# ---------- review dispute resolution (v1 left disputes unresolved) ----------
@router.get("/admin/reviews/disputes")
def list_review_disputes(page: int = Query(1, ge=1),
                         user: models.User = Depends(
                             require_perm("reviews.disputes.read")),
                         db: Session = Depends(get_db)):
    rows = (db.query(Review).filter(Review.status == "disputed")
            .order_by(Review.created_at.desc(), Review.id.desc()).all())
    items = []
    for r in rows:
        out = review_out(db, r)
        out["dispute_reason"] = r.dispute_reason or ""
        items.append(out)
    out = _page(items, page)
    return {"current_page": out["current_page"],
            "total_pages": out["total_pages"], "total": out["total"],
            "reviews": out["items"]}


@router.post("/reviews/{review_id}/resolve")
def resolve_review_dispute(review_id: int,
                           body: schemas_line_f.ReviewResolveIn,
                           user: models.User = Depends(
                               require_perm("reviews.dispute.resolve")),
                           db: Session = Depends(get_db)):
    review = db.query(Review).filter(Review.id == review_id).first()
    if review is None:
        raise HTTPException(status_code=404, detail="找不到此評價")
    if review.status != "disputed":
        raise HTTPException(status_code=400, detail="只有申訴中的評價可以處理")
    if body.decision == "uphold":
        # 申訴成立：評價不真實，隱藏，永久退出聚合。
        review.status = "hidden"
        msg = "申訴成立，評價已隱藏，不計入星級"
    else:
        # 申訴不成立：評價真實，恢復可見，重新計入聚合。
        review.status = "visible"
        msg = "申訴不成立，評價已恢復可見，重新計入星級"
    db.flush()
    recompute_rating(db, review.shop_id)
    _log(db, user.id, "review.dispute.resolve", "review", review.id,
         f"decision={body.decision}; note={body.note}")
    db.commit()
    db.refresh(review)
    return {"message": msg, "review": review_out(db, review)}
