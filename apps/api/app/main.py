"""Coupon GO! API — FastAPI backend (development build).

API base mirrors the original app: /api/v3/<endpoint> with the endpoint
names recovered from the 1.2.6 APK (REVERSE.md §2 / REVERSE_SUPPLEMENT.md §4).
This is a clean-room re-implementation: no original source or backend is
used. Seed data are demo data, not real merchants.
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .database import init_db
from .errors import ApiError
from .routers import (admin, admin_v2, auth, bargain, cashback, connect, coupons,
                      home, merchant_onboarding, merchant_stats, nearby, notifications,
                      packages, qbot, redemption, reviews, search, uploads,
                      vendor, wallet)
from .scheduler import start_scheduler, validate_scheduler_config  # noqa: F401  (M1: also registers models_scheduler.scheduler_locks)

from . import models_cashback  # noqa: F401  (Line D: register cashback tables)

app = FastAPI(title="Coupon GO! API", version="0.1.0-dev")


@app.exception_handler(ApiError)
async def api_error_handler(request, exc: ApiError):
    """Stable error codes (dianping unify): the body is FastAPI's
    ``{"detail": message}`` plus one field, ``code`` — old clients
    matching on ``detail`` or status see no change."""
    return JSONResponse(status_code=exc.status_code,
                        content={"detail": exc.detail, "code": exc.code})

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (auth.router, home.router, coupons.router, wallet.router,
               search.router, notifications.router, vendor.router,
               redemption.router, nearby.router, reviews.router,
               admin.router, admin_v2.router, uploads.router,
               merchant_onboarding.router, merchant_stats.router,
               connect.router,
               cashback.router, bargain.router, packages.router, qbot.router):
    app.include_router(router, prefix="/api/v3")

# Line F: serve locally uploaded review images (placeholder storage;
# see app/routers/uploads.py). Files live under <UPLOAD_DIR>/.
os.makedirs(config.UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=config.UPLOAD_DIR), name="uploads")


@app.get("/health")
def health():
    return {"status": "ok", "env": config.ENV,
            "scheduler": config.scheduler_status()}


@app.on_event("startup")
def startup():
    # FIX-1 startup gate: non-dev without a webhook secret must not run.
    config.validate_payment_config()
    init_db()
    # M1: background scheduler (loud warning in non-dev when it is not
    # configured — never silently not running).
    validate_scheduler_config()
    app.state.scheduler = start_scheduler()
    if os.environ.get("SEED_ON_STARTUP", "1") == "1":
        from .database import SessionLocal
        from .seed import seed
        from .seed_showcase import seed_showcase
        from .seed_bargain import seed_bargain_campaigns
        db = SessionLocal()
        try:
            seed(db)
            seed_bargain_campaigns(db)
            seed_showcase(db)
        finally:
            db.close()


@app.on_event("shutdown")
def shutdown():
    # M1: stop the in-process scheduler (no-op when it never started).
    sched = getattr(app.state, "scheduler", None)
    if sched is not None:
        sched.shutdown(wait=False)
        app.state.scheduler = None

# Opt-in distribution of the compiled Flutter Web app from the API origin.
# Mount last so API, OpenAPI, uploads and health routes retain precedence.
if web_directory := os.getenv("COUPON_WEB_DIR"):
    app.mount("/", StaticFiles(directory=web_directory, html=True), name="coupon-web")
