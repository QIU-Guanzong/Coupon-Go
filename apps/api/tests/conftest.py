"""Shared test setup.

M9-1 made an unset environment mean production. The test suite runs as
development unless a test overrides it, so set the default before any
test module imports app.config.

M10: the cashback master switch defaults OFF in production, but the
suite exercises cashback with the switch ON (every pre-M10 cashback
test redeems and expects a credit). The M10 tests toggle the switch
per-test with monkeypatch; all other tests rely on this default.
"""
import os

if not (os.environ.get("APP_ENV") or os.environ.get("COUPON_GO_ENV")):
    os.environ["COUPON_GO_ENV"] = "dev"

if not os.environ.get("CASHBACK_ENABLED"):
    os.environ["CASHBACK_ENABLED"] = "1"
