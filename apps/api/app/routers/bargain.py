"""Bargain 2C (line E): share-to-help price cutting — F3 in the research
report (FEATURE_RESEARCH_20261116.md §10 F3, §11.1/§11.3, §12 砍价接口).

Endpoints (all under /api/v3):
  GET  /bargain-campaigns                  list campaigns (guest OK)
  POST /bargain-campaigns                  create campaign (vendor/admin)
  POST /bargain-campaigns/{id}/start       start a session (customer)
  GET  /bargain-sessions/{id}              progress + helps + refunds (guest OK)
  GET  /bargain-my-sessions                my sessions (customer)
  POST /bargain-sessions/{id}/help         help cut the price (customer)
  POST /bargain-sessions/{id}/purchase     buy at cut price -> package coupon
  POST /bargain/expire-sweep               settle timed-out sessions (admin)

Rules (defaults are assumptions — report §14 leaves them open; see
README/docs for the list):
  * Prepay-at-start, simulated: starting a session records paid_amount =
    original_price (no payment gateway in this dev build — no real
    charge is taken). Failure / timeout refunds it in full; a
    successful purchase refunds the price-cut difference. Refunds are
    attempted through the gateway (payments_gateway.refund_payment,
    same calibre as package refunds) and recorded as BargainRefund
    rows with status pending/succeeded/failed + gateway_ref; a failed
    refund is retryable on the same row (POST /bargain/refunds/{id}/retry).
    Because the prepay is simulated, the gateway call uses a synthetic
    "bargain-session-{id}" payment reference (see _attempt_gateway_refund):
    the dev simulator returns a re_test_… ref, while a real Stripe key
    has no real payment_intent to refund against, so that call fails
    and stays retryable until a real prepay flow lands. The dev-only
    explanation lives here and in the server log, never in user copy.
  * Cut curve (public, no "永远差一刀"): remaining * rate, rate decays
    30% -> 25% -> 20% -> 15% -> 10% -> 5% by help index; a new user
    (registered within 7 days) adds +10pts. Min cut HK$1, never below floor.
  * Success = price reaches floor_price OR helper_count reaches
    helper_limit (成团). Default helper_limit 5, session life 24h,
    campaign life 7 days, package coupon valid 30 days.
  * Stock: starting locks 1 unit (stock_left - 1); failure/timeout
    releases it; purchase consumes it. lock_stock=False campaigns
    instead deduct at purchase time.
  * Anti-fraud: one help per user per session (409); initiator cannot
    help (400); same device_id under a different account on one session
    is blocked (409); max 10 helps per user per day (400); one active
    session per user per campaign (409).
  * Refund policy shown to the user (never the DT-life pitfalls):
    unformed / timed-out = full refund; package coupon unredeemed =
    refundable, expired = auto-refund; redeemed = not refundable.
    No 30% fee, no "一经售出不退不换".
"""
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models
from ..demo_covers import BARGAIN_COVERS, cover_path
from ..database import get_db
from ..package_templates import current_package_coupon
from ..deps import get_current_user, get_optional_user, require_role
from ..models_bargain import (
    BargainCampaign, BargainHelp, BargainRefund, BargainSession,
)
from ..payments_gateway import GatewayError, refund_payment
from ..shared import issue_wallet_item

router = APIRouter()

logger = logging.getLogger(__name__)

# ---- defaults (assumptions; see module docstring) ----
DEFAULT_HELPER_LIMIT = 5
DEFAULT_DURATION_MINUTES = 1440          # 24h per session
DEFAULT_CAMPAIGN_DAYS = 7
PACKAGE_COUPON_VALID_DAYS = 30
MAX_HELPS_PER_USER_PER_DAY = 10
NEW_USER_DAYS = 7
CUT_RATES = [Decimal("0.30"), Decimal("0.25"), Decimal("0.20"),
             Decimal("0.15"), Decimal("0.10")]  # then 0.05
NEW_USER_BONUS = Decimal("0.10")
MIN_CUT = Decimal("1.00")
REFUND_NOTE = "退款将在 1–3 个工作日原路退回"
PACKAGE_TERMS = ("砍价成团套餐券；未核销可退款，过期自动退款；已核销不可退。"
                 "退款原路返回，1–3 个工作日。")


from ..timeutil import utcnow as _now


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def cut_amount(remaining: Decimal, help_index: int, is_new_user: bool) -> Decimal:
    """Deterministic decaying cut. Public curve — no endless last-cent cut."""
    if remaining <= 0:
        return Decimal("0.00")
    rate = CUT_RATES[min(help_index, len(CUT_RATES) - 1)]
    if help_index >= len(CUT_RATES):
        rate = Decimal("0.05")
    if is_new_user:
        rate += NEW_USER_BONUS
    cut = (remaining * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if cut < MIN_CUT:
        cut = min(MIN_CUT, remaining)
    return min(cut, remaining)


def _is_new_user(user: models.User) -> bool:
    if not user.created_at:
        return False
    return (_now() - user.created_at) <= timedelta(days=NEW_USER_DAYS)


def _require_customer(user: models.User) -> models.User:
    if user.role != "customer":
        raise HTTPException(status_code=403, detail="只有消費者帳戶可以砍價")
    return user


# ---- serializers ----
def campaign_out(db: Session, c: BargainCampaign) -> dict:
    shop = db.query(models.Shop).filter(models.Shop.id == c.shop_id).first()
    # Gap 10 — image: the linked package's coupon template image when
    # the campaign is tied to a package, else the shop image. There is
    # no campaign image column; "" when neither exists.
    image = ""
    if c.package_id:
        coupon = current_package_coupon(db, c.package_id)
        if coupon is not None and coupon.image:
            image = coupon.image
    if not image and shop is not None:
        image = shop.image or ""
        if image.startswith("assets/images/mvp-shop-"):
            shop_slug = image.removeprefix("assets/images/mvp-").removesuffix(".jpg")
            demo_cover = BARGAIN_COVERS.get((shop_slug, c.name))
            if demo_cover:
                image = cover_path(demo_cover)
    # Gap 10 — helper_count: distinct people who have helped any
    # session of this campaign (BargainHelp rows, not sessions).
    helper_count = (db.query(BargainHelp.helper_user_id)
                    .join(BargainSession,
                          BargainSession.id == BargainHelp.session_id)
                    .filter(BargainSession.campaign_id == c.id)
                    .distinct().count())
    # Gap 10 — seconds_left: campaign expiry countdown, 0 once ended.
    seconds_left = 0
    if c.expires_at:
        seconds_left = max(0, int((c.expires_at - _now()).total_seconds()))
    return {
        "id": c.id, "campaign_id": c.id,
        "package_id": c.package_id,
        "shop_id": c.shop_id,
        "shop_name": shop.name if shop else "",
        "name": c.name, "items": c.items or "",
        "person_count": c.person_count,
        "original_price": float(c.original_price),
        "floor_price": float(c.floor_price),
        "stock_total": c.stock_total, "stock_left": c.stock_left,
        "helper_limit": c.helper_limit,
        "duration_minutes": c.duration_minutes,
        "lock_stock": c.lock_stock,
        "status": c.status,
        "image": image,
        "helper_count": helper_count,
        "seconds_left": seconds_left,
        "expires_at": c.expires_at.isoformat() if c.expires_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _help_out(db: Session, h: BargainHelp) -> dict:
    helper = db.query(models.User).filter(models.User.id == h.helper_user_id).first()
    return {
        "id": h.id, "helper_user_id": h.helper_user_id,
        "helper_name": helper.name if helper and helper.name else "好友",
        "cut_amount": float(h.cut_amount),
        "is_new_user": h.is_new_user,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }


def _refund_out(r: BargainRefund) -> dict:
    return {
        "id": r.id, "amount": float(r.amount), "reason": r.reason,
        "status": r.status, "gateway_ref": r.gateway_ref or "",
        "note": r.note or "",
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def session_out(db: Session, s: BargainSession,
                user_id: int | None = None) -> dict:
    campaign = db.query(BargainCampaign).filter(
        BargainCampaign.id == s.campaign_id).first()
    seconds_left = 0
    if s.expires_at:
        seconds_left = max(0, int((s.expires_at - _now()).total_seconds()))
    # Gap 10 — cut_amount_total: original price minus current price,
    # both server-side values (the sum of the BargainHelp cuts).
    original = (float(campaign.original_price) if campaign
                else float(s.paid_amount))
    current = float(s.current_price)
    cut_amount_total = round(max(0.0, original - current), 2)
    # Gap 10 — remaining_amount: current price minus the floor price,
    # floored at 0 (how much more must be cut to reach the floor).
    floor = (float(campaign.floor_price) if campaign
             else float(s.target_price))
    remaining_amount = round(max(0.0, current - floor), 2)
    # Gap 10 — is_owner / already_helped need the caller: None /
    # False for guests (no identity to compare against).
    is_owner = None if user_id is None else (user_id == s.user_id)
    already_helped = False
    if user_id is not None:
        already_helped = (db.query(BargainHelp)
                          .filter(BargainHelp.session_id == s.id,
                                  BargainHelp.helper_user_id == user_id)
                          .first() is not None)
    return {
        "id": s.id, "session_id": s.id,
        "campaign_id": s.campaign_id,
        "campaign": campaign_out(db, campaign) if campaign else None,
        "user_id": s.user_id,
        "is_owner": is_owner,
        "already_helped": already_helped,
        "cut_amount_total": cut_amount_total,
        "remaining_amount": remaining_amount,
        "current_price": float(s.current_price),
        "target_price": float(s.target_price),
        "floor_price": float(campaign.floor_price) if campaign else float(s.target_price),
        "original_price": float(campaign.original_price) if campaign else float(s.paid_amount),
        "paid_amount": float(s.paid_amount),
        "status": s.status,
        "helper_count": s.helper_count,
        "helper_limit": campaign.helper_limit if campaign else DEFAULT_HELPER_LIMIT,
        "stock_locked": s.stock_locked,
        "wallet_item_id": s.wallet_item_id,
        "expires_at": s.expires_at.isoformat() if s.expires_at else None,
        "seconds_left": seconds_left,
        "can_purchase": s.status == "success" and s.wallet_item_id is None,
        "share_link": f"coupon-go://bargain?session={s.id}",
        "helps": [_help_out(db, h) for h in
                  db.query(BargainHelp).filter(
                      BargainHelp.session_id == s.id)
                  .order_by(BargainHelp.id).all()],
        "refunds": [_refund_out(r) for r in
                    db.query(BargainRefund).filter(
                        BargainRefund.session_id == s.id)
                    .order_by(BargainRefund.id).all()],
    }


# ---- settlement (timeout / unpurchased-success) ----
def _release_stock(db: Session, s: BargainSession):
    if not s.stock_locked:
        return
    # FIX-3: lock the campaign row so a release cannot lose an update
    # against a concurrent start/purchase decrement.
    campaign = (db.query(BargainCampaign)
                .filter(BargainCampaign.id == s.campaign_id)
                .with_for_update().populate_existing().first())
    if campaign and campaign.lock_stock:
        campaign.stock_left += 1
        if campaign.status == "sold_out":
            campaign.status = "active"
    s.stock_locked = False


def _attempt_gateway_refund(db: Session, refund: BargainRefund) -> BargainRefund:
    """Attempt the gateway refund for one refund row (P1-3, same
    calibre as package refunds).

    The bargain prepay is simulated — no real charge was ever taken —
    so there is no real payment_intent to refund against. The gateway
    is called with the synthetic reference "bargain-session-{id}":
    the dev simulator accepts any reference and returns a re_test_…
    refund id; a real Stripe key rejects it, and the refund then
    stays failed (retryable) until a real prepay flow lands. That
    dev-only explanation is logged here, never shown to the user:
    on failure the user sees only the generic "processing" note.
    """
    try:
        result = refund_payment(f"bargain-session-{refund.session_id}",
                                float(refund.amount))
    except GatewayError as exc:
        logger.exception(
            "bargain refund gateway failed session_id=%s refund_id=%s "
            "amount=%s (simulated prepay — no real payment_intent; "
            "see routers/bargain.py): %s",
            refund.session_id, refund.id, refund.amount, exc)
        refund.status = "failed"
        refund.note = "退款處理中，請稍後查看或聯繫客服"
        db.flush()
        return refund
    refund.status = "succeeded"
    refund.gateway_ref = result.get("gateway_ref", "")
    refund.note = REFUND_NOTE
    db.flush()
    return refund


def _add_refund(db: Session, s: BargainSession, amount: Decimal,
                reason: str) -> BargainRefund | None:
    if amount <= 0:
        return None
    existing = db.query(BargainRefund).filter(
        BargainRefund.session_id == s.id,
        BargainRefund.reason == reason).first()
    if existing:
        # One refund row per (session, reason). A failed row is NOT
        # re-attempted implicitly on every read — retry is explicit
        # via POST /bargain/refunds/{id}/retry.
        return existing
    refund = BargainRefund(session_id=s.id, user_id=s.user_id,
                           amount=_money(amount), reason=reason,
                           status="pending", note=REFUND_NOTE)
    db.add(refund)
    db.flush()
    return _attempt_gateway_refund(db, refund)


def settle_session(db: Session, s: BargainSession) -> BargainSession:
    """Lazy timeout settlement. Called on every read/help/purchase so an
    expired session never stays 'active' past its deadline."""
    if s.expires_at and _now() > s.expires_at:
        if s.status == "active":
            s.status = "failed"
            _release_stock(db, s)
            _add_refund(db, s, s.paid_amount, "bargain_failed")
            db.commit()
        elif s.status == "success" and s.wallet_item_id is None:
            s.status = "expired"
            _release_stock(db, s)
            _add_refund(db, s, s.paid_amount, "expired")
            db.commit()
    return s


def _get_session_or_404(db: Session, session_id: int) -> BargainSession:
    s = db.query(BargainSession).filter(BargainSession.id == session_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="找不到此砍價")
    return settle_session(db, s)


# ---- request bodies ----
class BargainCampaignIn(BaseModel):
    shop_id: int
    name: str
    items: str = ""
    package_id: int | None = None
    person_count: int = 2
    original_price: float = Field(gt=0)
    floor_price: float | None = None  # default: 50% of original
    stock_total: int = Field(ge=1)
    helper_limit: int = Field(default=DEFAULT_HELPER_LIMIT, ge=1)
    duration_minutes: int = Field(default=DEFAULT_DURATION_MINUTES, ge=0)
    campaign_days: int = Field(default=DEFAULT_CAMPAIGN_DAYS, ge=1)
    lock_stock: bool = True


class HelpIn(BaseModel):
    device_id: str = ""


# ---- campaigns ----
@router.get("/bargain-campaigns")
def list_campaigns(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(BargainCampaign).order_by(BargainCampaign.id)
    if status:
        q = q.filter(BargainCampaign.status == status)
    campaigns = q.all()
    for c in campaigns:
        if c.status == "active" and c.expires_at and _now() > c.expires_at:
            c.status = "expired"
    db.commit()
    return {"campaigns": [campaign_out(db, c) for c in campaigns]}


@router.post("/bargain-campaigns")
def create_campaign(body: BargainCampaignIn,
                    user: models.User = Depends(
                        require_role("vendor", "admin")),
                    db: Session = Depends(get_db)):
    shop = db.query(models.Shop).filter(models.Shop.id == body.shop_id).first()
    if shop is None:
        raise HTTPException(status_code=404, detail="找不到此商店")
    if user.role == "vendor" and shop.vendor_id != user.id:
        raise HTTPException(status_code=403, detail="只能為自己的商店開砍價")
    original = _money(body.original_price)
    floor = _money(body.floor_price) if body.floor_price is not None \
        else _money(original * Decimal("0.5"))
    if floor <= 0 or floor >= original:
        raise HTTPException(status_code=400, detail="底價須大於 0 且低於原價")
    campaign = BargainCampaign(
        package_id=body.package_id, shop_id=shop.id, name=body.name,
        items=body.items, person_count=body.person_count,
        original_price=original, floor_price=floor,
        stock_total=body.stock_total, stock_left=body.stock_total,
        helper_limit=body.helper_limit,
        duration_minutes=body.duration_minutes,
        lock_stock=body.lock_stock, status="active",
        expires_at=_now() + timedelta(days=body.campaign_days))
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return {"message": "砍價活動已建立", "campaign": campaign_out(db, campaign)}


# ---- sessions ----
@router.post("/bargain-campaigns/{campaign_id}/start")
def start_bargain(campaign_id: int,
                  user: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    _require_customer(user)
    # FIX-3: lock the campaign row; the stock check below then reads
    # the locked row, and a concurrent start blocks until commit.
    campaign = (db.query(BargainCampaign).filter(
        BargainCampaign.id == campaign_id)
        .with_for_update().populate_existing().first())
    if campaign is None:
        raise HTTPException(status_code=404, detail="找不到此砍價活動")
    if campaign.status == "expired" or (
            campaign.expires_at and _now() > campaign.expires_at):
        campaign.status = "expired"
        db.commit()
        raise HTTPException(status_code=400, detail="此砍價活動已結束")
    if campaign.stock_left <= 0 or campaign.status == "sold_out":
        raise HTTPException(status_code=400, detail="已售罄，先到先得")
    existing = db.query(BargainSession).filter(
        BargainSession.campaign_id == campaign.id,
        BargainSession.user_id == user.id,
        BargainSession.status == "active").first()
    if existing:
        raise HTTPException(status_code=409, detail="您已在砍此套餐，請先完成它")
    session = BargainSession(
        campaign_id=campaign.id, user_id=user.id,
        current_price=campaign.original_price,
        target_price=campaign.floor_price,
        paid_amount=campaign.original_price,  # simulated prepay (see docstring)
        status="active", helper_count=0,
        stock_locked=bool(campaign.lock_stock),
        expires_at=_now() + timedelta(minutes=campaign.duration_minutes))
    db.add(session)
    if campaign.lock_stock:
        campaign.stock_left -= 1
        if campaign.stock_left <= 0:
            campaign.status = "sold_out"
    try:
        db.commit()
    except IntegrityError:
        # FIX-3 backstop: the CHECK (stock_left >= 0) fired — the row
        # lock did not serialise (SQLite ignores FOR UPDATE).
        db.rollback()
        raise HTTPException(status_code=400, detail="已售罄，先到先得")
    db.refresh(session)
    return {"message": "砍價已發起，分享給好友一起砍到底價",
            "session": session_out(db, session, user_id=user.id)}


@router.get("/bargain-sessions/{session_id}")
def get_session(session_id: int, db: Session = Depends(get_db),
                user: models.User | None = Depends(get_optional_user)):
    s = _get_session_or_404(db, session_id)
    return {"session": session_out(db, s, user_id=user.id if user else None)}


@router.get("/bargain-my-sessions")
def my_sessions(user: models.User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    sessions = (db.query(BargainSession)
                .filter(BargainSession.user_id == user.id)
                .order_by(BargainSession.id.desc()).all())
    for s in sessions:
        settle_session(db, s)
    return {"sessions": [session_out(db, s, user_id=user.id)
                         for s in sessions]}


@router.post("/bargain-sessions/{session_id}/help")
def help_bargain(session_id: int, body: HelpIn | None = None,
                 user: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    _require_customer(user)
    s = _get_session_or_404(db, session_id)
    # M2: lock the session row before the duplicate/floor re-checks
    # (same FIX-3 pattern as purchase_bargain below) so two concurrent
    # helps serialise: the second reads the updated current_price /
    # helper_count, its cut is computed from the new remaining amount,
    # and the total cut can never push the price below floor_price.
    s = (db.query(BargainSession).filter(BargainSession.id == s.id)
         .with_for_update().populate_existing().first())
    if s.status != "active":
        raise HTTPException(status_code=400, detail="此砍價已結束，不能再助力")
    if s.user_id == user.id:
        raise HTTPException(status_code=400, detail="不能為自己的砍價助力")
    duplicate = db.query(BargainHelp).filter(
        BargainHelp.session_id == s.id,
        BargainHelp.helper_user_id == user.id).first()
    if duplicate:
        raise HTTPException(status_code=409, detail="您已助力過一次，每人每單限助一次")
    device_id = (body.device_id if body else "") or ""
    if device_id:
        clash = db.query(BargainHelp).filter(
            BargainHelp.session_id == s.id,
            BargainHelp.device_id == device_id,
            BargainHelp.helper_user_id != user.id).first()
        if clash:
            raise HTTPException(status_code=409, detail="同一設備已助力過此單")
    today_start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    helps_today = db.query(BargainHelp).filter(
        BargainHelp.helper_user_id == user.id,
        BargainHelp.created_at >= today_start).count()
    if helps_today >= MAX_HELPS_PER_USER_PER_DAY:
        raise HTTPException(
            status_code=400,
            detail=f"今日助力已達上限（{MAX_HELPS_PER_USER_PER_DAY} 次）")
    campaign = db.query(BargainCampaign).filter(
        BargainCampaign.id == s.campaign_id).first()
    is_new = _is_new_user(user)
    remaining = _money(s.current_price) - _money(campaign.floor_price)
    cut = cut_amount(remaining, s.helper_count, is_new)
    new_price = _money(s.current_price) - cut
    help_row = BargainHelp(session_id=s.id, helper_user_id=user.id,
                           cut_amount=cut, device_id=device_id,
                           is_new_user=is_new)
    db.add(help_row)
    s.current_price = new_price
    s.helper_count += 1
    if new_price <= _money(campaign.floor_price) or \
            s.helper_count >= campaign.helper_limit:
        s.status = "success"
    db.commit()
    db.refresh(s)
    return {"message": "助力成功" if s.status == "active" else "砍價成功！可以底價拿套餐了",
            "cut_amount": float(cut),
            "is_new_user_bonus": is_new,
            "session": session_out(db, s, user_id=user.id)}


@router.post("/bargain-sessions/{session_id}/purchase")
def purchase_bargain(session_id: int,
                     user: models.User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    _require_customer(user)
    s = _get_session_or_404(db, session_id)
    # FIX-3: lock the session row (one purchase per session) and the
    # campaign row (stock decrement below) before re-checking.
    s = (db.query(BargainSession).filter(BargainSession.id == s.id)
         .with_for_update().populate_existing().first())
    if s.user_id != user.id:
        raise HTTPException(status_code=403, detail="只有發起人可以下單")
    if s.status != "success":
        raise HTTPException(status_code=400, detail="還未砍到目標價，不能下單")
    if s.wallet_item_id is not None:
        raise HTTPException(status_code=409, detail="已下單生成套餐券")
    campaign = (db.query(BargainCampaign).filter(
        BargainCampaign.id == s.campaign_id)
        .with_for_update().populate_existing().first())
    shop = db.query(models.Shop).filter(models.Shop.id == campaign.shop_id).first()
    if not campaign.lock_stock:
        if campaign.stock_left <= 0:
            raise HTTPException(status_code=400, detail="已售罄，先到先得")
        campaign.stock_left -= 1
        if campaign.stock_left <= 0:
            campaign.status = "sold_out"
    current = _money(s.current_price)
    coupon = models.Coupon(
        shop_id=campaign.shop_id, vendor_id=shop.vendor_id,
        coupon_type="Price",
        coupon_name=f"{campaign.name}（砍價套餐券）",
        category_id=shop.category_id,
        discount_price=current, regular_price=_money(campaign.original_price),
        valid_from=_now(), valid_to=_now() + timedelta(days=PACKAGE_COUPON_VALID_DAYS),
        total=1, remaining=1, downloads=1, used_count=0, status="active",
        additional_terms=PACKAGE_TERMS,
        kind="package", package_id=campaign.package_id)
    db.add(coupon)
    db.flush()
    item = issue_wallet_item(db, user.id, coupon.id, "bargain")
    s.wallet_item_id = item.id
    s.stock_locked = False  # locked unit is now sold, no longer held
    refund = _add_refund(db, s, _money(s.paid_amount) - current, "price_cut")
    try:
        db.commit()
    except IntegrityError:
        # FIX-3 backstop: the CHECK (stock_left >= 0) fired — the row
        # lock did not serialise (SQLite ignores FOR UPDATE).
        db.rollback()
        raise HTTPException(status_code=400, detail="已售罄，先到先得")
    db.refresh(item)
    return {"message": "成團！套餐券已存入 Q Wallet",
            "wallet_item_id": item.id,
            "coupon_id": coupon.id,
            "final_price": float(current),
            "refund": _refund_out(refund) if refund else None,
            "session": session_out(db, s, user_id=user.id)}


@router.post("/bargain/refunds/{refund_id}/retry")
def retry_refund(refund_id: int,
                 user: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """Retry a failed bargain refund on the SAME row (P1-3).

    Only the session owner or an admin may retry, and only a failed
    refund can be retried. A retry re-calls the gateway and updates
    the row in place (status + gateway_ref); it never creates a
    second refund row for the same (session, reason)."""
    refund = db.query(BargainRefund).filter(
        BargainRefund.id == refund_id).first()
    if refund is None:
        raise HTTPException(status_code=404, detail="找不到此退款記錄")
    session = db.query(BargainSession).filter(
        BargainSession.id == refund.session_id).first()
    if user.role != "admin" and (session is None or session.user_id != user.id):
        raise HTTPException(status_code=403, detail="沒有權限")
    if refund.status == "succeeded":
        raise HTTPException(status_code=409, detail="退款已成功，無需重試")
    if refund.status == "pending":
        raise HTTPException(status_code=409, detail="退款處理中，請稍後再試")
    refund = _attempt_gateway_refund(db, refund)
    db.commit()
    db.refresh(refund)
    return {"message": "退款成功，款項原路返回，預計 1–3 個工作日到賬"
            if refund.status == "succeeded" else "退款仍未成功，請稍後再試",
            "refund": _refund_out(refund)}


def sweep_expired_bargains(db: Session) -> dict:
    """M1 shared sweep: settle timed-out bargain sessions (active →
    failed + full refund; success-but-unpurchased → expired + refund).
    Used by BOTH the admin manual endpoint and the background
    scheduler — one logic, two triggers.

    Idempotent: settle_session only transitions active/success
    sessions, and _add_refund keeps one refund row per
    (session, reason); a settled session is never selected again.
    One bad session never blocks the rest."""
    now = _now()
    due = (db.query(BargainSession)
           .filter(BargainSession.expires_at <= now,
                   BargainSession.status.in_(["active", "success"])).all())
    settled, failed = 0, 0
    for s in due:
        before = s.status
        try:
            settle_session(db, s)
            if s.status in ("failed", "expired") and before != s.status:
                settled += 1
        except Exception:
            db.rollback()
            failed += 1
            logger.exception("bargain expire-sweep failed session_id=%s",
                             s.id)
    return {"settled": settled, "checked": len(due), "failed": failed}


@router.post("/bargain/expire-sweep")
def expire_sweep(admin: models.User = Depends(require_role("admin")),
                 db: Session = Depends(get_db)):
    """M1: this manual endpoint now calls the same
    sweep_expired_bargains() the background scheduler runs — it stays
    as the admin backfill path."""
    result = sweep_expired_bargains(db)
    return {"message": "已結算過期砍價", **result}
