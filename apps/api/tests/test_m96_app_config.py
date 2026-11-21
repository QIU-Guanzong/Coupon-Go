from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.routers import home


def test_app_config_is_public_and_only_exposes_display_rules(monkeypatch):
    monkeypatch.setattr(config, "POINTS_CLAIM", 12)
    monkeypatch.setattr(config, "POINTS_REDEEM", 34)
    monkeypatch.setattr(config, "POINTS_REVIEW", 56)
    monkeypatch.setattr(config, "QR_TTL_SECONDS", 120)
    monkeypatch.setattr(config, "CASHBACK_DINEIN_UNLOCK_DAYS", 45)
    # M10: the three new fields follow the switch / config values.
    monkeypatch.setattr(config, "CASHBACK_ENABLED", True)
    monkeypatch.setattr(config, "POINTS_EXCHANGE_COST", 7)
    monkeypatch.setattr(config, "OPEN_LOGIN", True)
    app = FastAPI()
    app.include_router(home.router, prefix="/api/v3")
    with TestClient(app) as c:
        result = c.get("/api/v3/app-config")
        assert result.status_code == 200
        assert result.json() == {
            "points_claim": 12, "points_redeem": 34, "points_review": 56,
            "qr_ttl_seconds": 120, "cashback_unlock_days": 45,
            "cashback_enabled": True, "points_exchange_cost": 7,
            "open_login": True,
        }
