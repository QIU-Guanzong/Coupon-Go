"""Shared test setup.

M9-1 made an unset environment mean production. The test suite runs as
development unless a test overrides it, so set the default before any
test module imports app.config.
"""
import os

if not (os.environ.get("APP_ENV") or os.environ.get("COUPON_GO_ENV")):
    os.environ["COUPON_GO_ENV"] = "dev"
