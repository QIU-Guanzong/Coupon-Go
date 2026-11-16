"""P1-2 tests: CORS allow-list from env + SECRET_KEY startup gate.

* CORS origins come from CORS_ORIGINS (comma-separated); dev defaults
  to ["*"], non-dev defaults to [] (fail closed).
* validate_payment_config() (the FIX-1 startup gate) also refuses to
  start outside dev when SECRET_KEY is still the shipped default.
"""
import os
import sys

import pytest
from fastapi.middleware.cors import CORSMiddleware

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from app import config  # noqa: E402
from app.main import app  # noqa: E402

DEFAULT_SECRET = "dev-only-insecure-secret-change-me"


def test_cors_origins_dev_defaults_to_star(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.setattr(config, "APP_ENV", "dev")
    assert config.cors_origins() == ["*"]


def test_cors_origins_env_list_is_parsed(monkeypatch):
    monkeypatch.setenv(
        "CORS_ORIGINS", "https://app.example.com, https://admin.example.com ")
    monkeypatch.setattr(config, "APP_ENV", "prod")
    assert config.cors_origins() == [
        "https://app.example.com", "https://admin.example.com"]


def test_cors_origins_non_dev_without_env_is_empty(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.setattr(config, "APP_ENV", "prod")
    assert config.cors_origins() == []


def test_secret_key_default_rejected_outside_dev(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "prod")
    monkeypatch.setattr(config, "SECRET_KEY", DEFAULT_SECRET)
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        config.validate_payment_config()


def test_secret_key_custom_passes_outside_dev(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "prod")
    monkeypatch.setattr(config, "SECRET_KEY", "a-real-secret-from-env")
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    config.validate_payment_config()  # must not raise


def test_secret_key_default_allowed_in_dev(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "dev")
    monkeypatch.setattr(config, "SECRET_KEY", DEFAULT_SECRET)
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "")
    config.validate_payment_config()  # dev: must not raise


def test_main_cors_middleware_uses_configured_origins():
    assert config.CORS_ORIGINS == ["*"]  # test env is dev
    cors = [mw for mw in app.user_middleware
            if mw.cls is CORSMiddleware]
    assert len(cors) == 1
    assert cors[0].kwargs["allow_origins"] == config.CORS_ORIGINS
