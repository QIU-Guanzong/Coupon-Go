"""Coupon GO! API — FastAPI backend (development build).

API base mirrors the original app: /api/v3/<endpoint> with the endpoint
names recovered from the 1.2.6 APK (REVERSE.md §2 / REVERSE_SUPPLEMENT.md §4).
This is a clean-room re-implementation: no original source or backend is
used. Seed data are demo data, not real merchants.
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .database import init_db
from .routers import auth, coupons, home, notifications, search, vendor, wallet

app = FastAPI(title="Coupon GO! API", version="0.1.0-dev")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (auth.router, home.router, coupons.router, wallet.router,
               search.router, notifications.router, vendor.router):
    app.include_router(router, prefix="/api/v3")


@app.get("/health")
def health():
    return {"status": "ok", "env": config.ENV}


@app.on_event("startup")
def startup():
    init_db()
    if os.environ.get("SEED_ON_STARTUP", "1") == "1":
        from .database import SessionLocal
        from .seed import seed
        db = SessionLocal()
        try:
            seed(db)
        finally:
            db.close()
