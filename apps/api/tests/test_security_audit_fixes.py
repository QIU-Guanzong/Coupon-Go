"""Regression tests for 2026-11-26 security audit fixes (S-H1..S-H4, S-M1)."""
import io
import os
import sys
import tempfile

import pytest

os.environ["COUPON_GO_ENV"] = "dev"
os.environ["SEED_ON_STARTUP"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app import config, merchant_upload_limits, rate_limit, security  # noqa: E402
from app.main import app  # noqa: E402
from app.routers.uploads import _has_valid_image_magic  # noqa: E402
from app.shared import csv_response, sanitize_csv_cell  # noqa: E402


def test_csv_formula_injection_sanitized():
    assert sanitize_csv_cell("=CMD|' /C calc'!A0") == "'=CMD|' /C calc'!A0"
    assert sanitize_csv_cell("+1+1") == "'+1+1"
    assert sanitize_csv_cell("-2+3") == "'-2+3"
    assert sanitize_csv_cell("@SUM(A1:A2)") == "'@SUM(A1:A2)"
    # Numeric values and normal text remain untouched.
    assert sanitize_csv_cell(-10.5) == -10.5
    assert sanitize_csv_cell("Normal Shop") == "Normal Shop"

    resp = csv_response("test.csv", ["name", "amount"], [["=1+1", -50]])
    body = resp.body.decode("utf-8")
    assert "'=1+1,-50" in body


def test_public_upload_magic_bytes_validation():
    assert _has_valid_image_magic(b"\xff\xd8\xff\xe0\x00\x10JFIF", "jpg")
    assert _has_valid_image_magic(b"\x89PNG\r\n\x1a\n\x00\x00", "png")
    assert _has_valid_image_magic(b"RIFF\x24\x00\x00\x00WEBPVP8 ", "webp")
    assert _has_valid_image_magic(b"GIF89a\x01\x00", "gif")
    # Polyglot / HTML disguised as .jpg must be rejected.
    assert not _has_valid_image_magic(b"<html><script>alert(1)</script></html>", "jpg")
    assert not _has_valid_image_magic(b"%PDF-1.4\n...", "png")


def test_jwt_revocation_helpers():
    rate_limit.reset_local_state()
    token = security.create_access_token("42", "customer")
    payload = security.decode_token(token)
    assert payload is not None and isinstance(payload.get("iat"), int)
    issued_us = payload["issued_us"]
    assert not rate_limit.is_token_revoked(42, issued_us)
    cutoff = rate_limit.revoke_user_tokens(42)
    assert cutoff >= issued_us
    assert rate_limit.is_token_revoked(42, issued_us)
    assert rate_limit.is_token_revoked(42, None)
    assert not rate_limit.is_token_revoked(42, cutoff + 1)
    assert not rate_limit.is_token_revoked(99, issued_us)
    rate_limit.reset_local_state()


def test_register_rate_limit_enforced():
    rate_limit.reset_local_state()
    for _ in range(rate_limit.REGISTER_MAX_PER_WINDOW):
        rate_limit.check_fixed_window(
            "register", "198.51.100.1",
            rate_limit.REGISTER_MAX_PER_WINDOW, rate_limit.REGISTER_WINDOW_SECONDS,
        )
    with pytest.raises(Exception) as exc_info:
        rate_limit.check_fixed_window(
            "register", "198.51.100.1",
            rate_limit.REGISTER_MAX_PER_WINDOW, rate_limit.REGISTER_WINDOW_SECONDS,
        )
    assert getattr(exc_info.value, "status_code", None) == 429
    rate_limit.reset_local_state()
