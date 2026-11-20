"""Anonymous catalogue-only ASGI gateway for the public product preview.

Deny by default. Never forward credentials. The only POST is the
side-effect-free search suggestion query.
"""
import re
from datetime import datetime, timezone

from sqlalchemy import event
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.database import engine, SessionLocal
from app.main import app as backend
from app.models_bargain import BargainCampaign
from app.routers.bargain import campaign_out

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
            await JSONResponse({"detail": "演示網站只供瀏覽"}, status_code=403)(scope, receive, send)
            return
        # No logged-in context is ever attached to public catalogue requests.
        scope = dict(scope)
        scope["headers"] = [(key, value) for key, value in scope["headers"]
                            if key.lower() not in (b"authorization", b"cookie")]
        await backend(scope, receive, send)


app = PreviewGateway()
