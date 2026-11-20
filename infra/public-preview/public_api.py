"""Anonymous catalogue-only ASGI gateway for the public product preview.

Deny by default. Never forward credentials. The only POSTs are search
suggestions and the explicitly enabled, budgeted Q仔 assistant.
"""
import re
import os
import sqlite3
from datetime import datetime, timezone
from threading import BoundedSemaphore

from pydantic import ValidationError
from sqlalchemy import event
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.database import engine, SessionLocal
from app.main import app as backend
from app.models_bargain import BargainCampaign
from app.routers.bargain import campaign_out
from app.routers.home import app_config
from app.routers.qbot import Question

# This attaches to every newly opened SQLite connection. The public service
# receives a separately seeded read-only database and never changes it.
@event.listens_for(engine, "connect")
def _read_only(connection, _record):
    if engine.dialect.name == "sqlite":
        connection.execute("PRAGMA query_only=ON")


PUBLIC_GET = frozenset({
    "/api/v3/home",
    "/api/v3/advertisement-banners",
    "/api/v3/get-classification-coupons",
    "/api/v3/get-limited-time-coupons",
    "/api/v3/get-most-people-grab",
    "/api/v3/recomandation-coupons",
    "/api/v3/get-used-merchents-shop",
    "/api/v3/get-postion-shops",
    "/api/v3/get-area-list",
    "/api/v3/get-hong-kong-list",
    "/api/v3/get-kowloon-list",
    "/api/v3/get-new-territories-list",
    "/api/v3/get-category-list",
    "/api/v3/shop-details",
    "/api/v3/branch-details",
    "/api/v3/get-nearest-branchs",
    "/api/v3/coupon-details-new",
    "/api/v3/get-subscription-categories-list",
    "/api/v3/get-subscription-plan-list",
    "/api/v3/new-filter-details",
    "/api/v3/nearby-shops",
    "/api/v3/packages",
    "/api/v3/package-details",
})
PUBLIC_SHOP = re.compile(r"^/api/v3/shops/[1-9][0-9]{0,9}/(?:map|reviews|rating)$")
SEARCH = "/api/v3/auto-suggestion-new"
CAMPAIGNS = "/api/v3/bargain-campaigns"
APP_CONFIG = "/api/v3/app-config"
APP_CONFIG_FIELDS = (
    "points_claim", "points_redeem", "points_review", "qr_ttl_seconds",
    "cashback_unlock_days",
)
QBOT = "/api/v3/qbot/chat"
QBOT_MAX_BODY = 40000
_qbot_slots = BoundedSemaphore(3)


def _reserve_qbot_budget():
    """Atomically reserve a call before provider I/O; never persist chat text.

    The deployment uses one worker, so the semaphore above also bounds
    concurrency. SQLite budgets remain effective across process restarts.
    """
    path = os.environ.get("PUBLIC_QBOT_QUOTA_DB", "")
    if not path or not os.path.isabs(path):
        return False
    try:
        # Create a private file even if the surrounding directory is shared.
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        with sqlite3.connect(path, timeout=2) as db:
            db.execute("BEGIN IMMEDIATE")
            # Sample the period only after acquiring the write lock. A request
            # queued across midnight must count against the new day's budget.
            now = datetime.now(timezone.utc)
            day, minute = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M")
            db.execute("CREATE TABLE IF NOT EXISTS budgets (period TEXT PRIMARY KEY, used INTEGER NOT NULL)")
            periods = (("day:" + day, 200), ("minute:" + minute, 20))
            for period, limit in periods:
                row = db.execute("SELECT used FROM budgets WHERE period = ?", (period,)).fetchone()
                if row and row[0] >= limit:
                    db.rollback()
                    return False
            for period, _ in periods:
                db.execute("INSERT INTO budgets(period, used) VALUES (?, 1) "
                           "ON CONFLICT(period) DO UPDATE SET used = used + 1", (period,))
            # A backwards wall-clock adjustment must never erase a future
            # bucket, or the limit could be reset when that period returns.
            db.execute("DELETE FROM budgets WHERE (period LIKE 'day:%' AND period < ?) "
                       "OR (period LIKE 'minute:%' AND period < ?)",
                       (periods[0][0], periods[1][0]))
            db.commit()
        return True
    except (OSError, sqlite3.Error):
        return False


async def _qbot(scope, receive, send):
    async def reject(detail, status):
        await JSONResponse({"detail": detail}, status_code=status,
                           headers={"Cache-Control": "no-store"})(scope, receive, send)

    if os.environ.get("PUBLIC_QBOT_ENABLED") != "1":
        return await reject("Q仔暫時未能連線，請稍後再試", 503)
    if scope.get("query_string", b""):
        return await reject("請在請求內容中提交問題", 400)
    headers = dict(scope.get("headers", []))
    if not headers.get(b"content-type", b"").lower().startswith(b"application/json"):
        return await reject("請使用 JSON 提交問題", 415)
    body = bytearray()
    while True:
        event = await receive()
        if event["type"] != "http.request":
            return
        body.extend(event.get("body", b""))
        if len(body) > QBOT_MAX_BODY:
            return await reject("問題太長", 413)
        if not event.get("more_body", False):
            break
    try:
        Question.model_validate_json(bytes(body))
    except ValidationError:
        return await reject("請檢查問題內容", 422)
    if not _qbot_slots.acquire(blocking=False):
        return await reject("Q仔忙碌中，請稍後再試", 429)
    try:
        if not await run_in_threadpool(_reserve_qbot_budget):
            return await reject("Q仔今日或目前的使用額度已滿，請稍後再試", 429)
        # Provide the same body to the backend; mark only this request as a
        # demo so its model instructions cannot present seed data as real.
        forwarded = dict(scope)
        forwarded["headers"] = [(k, v) for k, v in scope["headers"]
                                if k.lower() not in (b"authorization", b"cookie")]
        forwarded["state"] = dict(scope.get("state", {}), qbot_preview=True)
        async def replay():
            return {"type": "http.request", "body": bytes(body), "more_body": False}
        await backend(forwarded, replay, send)
    finally:
        _qbot_slots.release()


def _campaigns(status):
    # The existing campaign GET commits expiry updates. Produce the same
    # public representation without modifying the isolated catalogue.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as db:
        q = db.query(BargainCampaign).order_by(BargainCampaign.id)
        if status:
            q = q.filter(BargainCampaign.status == status)
        rows = []
        for campaign in q.all():
            item = campaign_out(db, campaign)
            if campaign.status == "active" and campaign.expires_at and campaign.expires_at < now:
                item["status"] = "expired"
            rows.append(item)
        return {"campaigns": rows}


class PreviewGateway:
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await backend(scope, receive, send)
            return
        method, path = scope["method"], scope["path"]
        if len(scope.get("query_string", b"")) > 4096:
            await JSONResponse({"detail": "查詢過長"}, status_code=414)(scope, receive, send)
            return
        if method == "POST" and path == QBOT:
            await _qbot(scope, receive, send)
            return
        if method == "GET" and path == APP_CONFIG and not scope.get("query_string", b""):
            # Explicitly project the display contract so that future private
            # additions to the underlying endpoint cannot leak to the demo.
            values = await run_in_threadpool(app_config)
            await JSONResponse({key: values[key] for key in APP_CONFIG_FIELDS})(scope, receive, send)
            return
        request = Request(scope, receive)
        if method == "GET" and path == CAMPAIGNS:
            result = await run_in_threadpool(_campaigns, request.query_params.get("status"))
            await JSONResponse(result)(scope, receive, send)
            return
        allowed = (method == "GET" and
                   (path in PUBLIC_GET or PUBLIC_SHOP.fullmatch(path) is not None))
        if method == "POST" and path == SEARCH:
            keyword = request.query_params.get("keyword", "")
            allowed = len(keyword) <= 100
        if not allowed:
            await JSONResponse({"detail": "此網站只供瀏覽"}, status_code=403)(scope, receive, send)
            return
        # No logged-in context is ever attached to public catalogue requests.
        scope = dict(scope)
        scope["headers"] = [(key, value) for key, value in scope["headers"]
                            if key.lower() not in (b"authorization", b"cookie")]
        await backend(scope, receive, send)


app = PreviewGateway()
