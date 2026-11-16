"""Coupon GO! API — FastAPI backend (development build).

API base mirrors the original app: /api/v3/<endpoint> with the endpoint
names recovered from the 1.2.6 APK (REVERSE.md §2 / REVERSE_SUPPLEMENT.md §4).
This is a clean-room re-implementation: no original source or backend is
used. Seed data are demo data, not real merchants.
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import config
from .database import init_db
from .routers import (admin, admin_v2, auth, bargain, cashback, coupons,
                      home, nearby, notifications, packages, redemption,
                      reviews, search, uploads, vendor, wallet)

from . import models_cashback  # noqa: F401  (Line D: register cashback tables)

app = FastAPI(title="Coupon GO! API", version="0.1.0-dev")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (auth.router, home.router, coupons.router, wallet.router,
               search.router, notifications.router, vendor.router,
               redemption.router, nearby.router, reviews.router,
               admin.router, admin_v2.router, uploads.router,
               cashback.router, bargain.router, packages.router):
    app.include_router(router, prefix="/api/v3")

# Line F: serve locally uploaded review images (placeholder storage;
# see app/routers/uploads.py). Files live under <UPLOAD_DIR>/.
os.makedirs(config.UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=config.UPLOAD_DIR), name="uploads")


@app.get("/health")
def health():
    return {"status": "ok", "env": config.ENV}


@app.on_event("startup")
def startup():
    init_db()
    if os.environ.get("SEED_ON_STARTUP", "1") == "1":
        from .database import SessionLocal
        from .seed import seed
        from .seed_bargain import seed_bargain_campaigns
        db = SessionLocal()
        try:
            seed(db)
            seed_bargain_campaigns(db)
        finally:
            db.close()
