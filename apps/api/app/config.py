"""Settings for the Coupon GO! API (development build).

No production secrets live here. SECRET_KEY / DATABASE_URL / REDIS_URL come
from the environment (see infra/.env.example). The dev defaults below are
deliberately insecure and must be overridden before any real deployment.
"""
import os

# M9-1: fail closed. An unset environment is production, so a deploy that
# forgets the variable still enforces the payment / secret startup gates,
# refuses demo payments and unsigned webhooks, and does not open CORS.
# Local development must set APP_ENV=dev (or COUPON_GO_ENV=dev) explicitly.
ENV = (os.environ.get("APP_ENV") or os.environ.get("COUPON_GO_ENV")
       or "production").strip().lower()
# APP_ENV is the canonical name (FIX-1); ENV is the legacy alias.
APP_ENV = ENV
# The shipped dev default. It must never run outside dev (see
# validate_payment_config, P1-2): a non-dev process that still has
# this value refuses to start.
DEFAULT_SECRET_KEY = "dev-only-insecure-secret-change-me"
SECRET_KEY = os.environ.get("SECRET_KEY", DEFAULT_SECRET_KEY)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))  # 7 days

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "sqlite:///./coupon_go.db"
)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# Points rules (demo rules from the MVP; configurable, not hard-coded business law)
POINTS_CLAIM = int(os.environ.get("POINTS_CLAIM", "10"))       # 領券 +10
POINTS_REDEEM = int(os.environ.get("POINTS_REDEEM", "20"))     # 核銷 +20
POINTS_REVIEW = int(os.environ.get("POINTS_REVIEW", "15"))     # 評價 +15

# Google services (Places API + Google sign-in).
# 【待更换】凭据在本地 infra/.env 配置（不进仓库、不推送），
# 上线前必须换成自有新 Key / 新 Client ID，并给 API Key 加 API 限制。
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_OAUTH_CLIENT_ID_1 = os.environ.get("GOOGLE_OAUTH_CLIENT_ID_1", "")
GOOGLE_OAUTH_CLIENT_ID_2 = os.environ.get("GOOGLE_OAUTH_CLIENT_ID_2", "")

QR_TTL_SECONDS = int(os.environ.get("QR_TTL_SECONDS", "900"))  # 15:00
SCAN_DAILY_LIMIT = int(os.environ.get("SCAN_DAILY_LIMIT", "200"))

# Review-image uploads (line F, local placeholder storage — NOT object
# storage; see app/routers/uploads.py). Served at /uploads by the
# StaticFiles mount in app.main.
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads"))
UPLOAD_MAX_BYTES = int(os.environ.get("UPLOAD_MAX_BYTES", str(5 * 1024 * 1024)))
OTP_TTL_SECONDS = int(os.environ.get("OTP_TTL_SECONDS", "600"))
OTP_LENGTH = 6

# Line A (report §5.4): revoke window after redemption; prepare-token TTL.
REVOKE_WINDOW_MINUTES = int(os.environ.get("REVOKE_WINDOW_MINUTES", "10"))
PREPARE_TOKEN_TTL_SECONDS = int(os.environ.get("PREPARE_TOKEN_TTL_SECONDS", "300"))
# --- Line D: cashback defaults (返现 2B; see README 返现一节) ---
# Defaults are assumptions, not negotiated merchant terms — see README.
CASHBACK_DEFAULT_RATE = float(os.environ.get("CASHBACK_DEFAULT_RATE", "0.05"))
CASHBACK_DINEIN_RATE = float(os.environ.get("CASHBACK_DINEIN_RATE", "0.05"))
CASHBACK_DINEIN_UNLOCK_DAYS = int(os.environ.get("CASHBACK_DINEIN_UNLOCK_DAYS", "90"))
CASHBACK_ONLINE_CONFIRM_DAYS = int(os.environ.get("CASHBACK_ONLINE_CONFIRM_DAYS", "30"))
CASHBACK_MIN_WITHDRAW_HKD = float(os.environ.get("CASHBACK_MIN_WITHDRAW_HKD", "50"))
CASHBACK_DAILY_WITHDRAW_LIMIT_HKD = float(os.environ.get("CASHBACK_DAILY_WITHDRAW_LIMIT_HKD", "1500"))
CASHBACK_MAX_PER_TXN_HKD = float(os.environ.get("CASHBACK_MAX_PER_TXN_HKD", "0"))  # 0 = 不设上限
CASHBACK_MISSING_WINDOW_DAYS = int(os.environ.get("CASHBACK_MISSING_WINDOW_DAYS", "30"))
# --- M10: cashback master switch (default OFF) ---
# §3.2 already hides the cashback entry on the frontend; the backend
# must not keep recording invisible HK$ liability either. When off,
# redemptions skip record_spend entirely (including its dine-in unlock
# of earlier pending earnings — they unlock again once the switch
# re-opens) and new withdrawals return 403. Read at call time via the
# module global so tests can monkeypatch it.
CASHBACK_ENABLED = os.environ.get("CASHBACK_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
# --- M10: /app-config display fields ---
# points_exchange_cost: single source of truth for the 9-points voucher
# (was a module constant in routers/wallet.py). open_login is field-only
# for now — no behaviour change (PROMPTS_OPEN_LOGIN_20261120.md not found).
POINTS_EXCHANGE_COST = int(os.environ.get("POINTS_EXCHANGE_COST", "9"))
OPEN_LOGIN = os.environ.get("OPEN_LOGIN", "false").strip().lower() in ("1", "true", "yes", "on")
# --- line C (套餐 2A): payments (Stripe test mode only) ---
# No real merchant account is connected and no real key is stored anywhere
# in the repo. With STRIPE_SECRET_KEY unset the gateway runs its local
# test-mode simulator; with a key set only sk_test_… is accepted.
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
# Stripe webhook signature timestamp tolerance (seconds; Stripe default 300).
STRIPE_WEBHOOK_TOLERANCE_SECONDS = int(
    os.environ.get("STRIPE_WEBHOOK_TOLERANCE_SECONDS", "300"))


def cors_origins() -> list:
    """P1-2: CORS allow-list from the CORS_ORIGINS env var
    (comma-separated). Dev defaults to ["*"] for local development;
    any other env defaults to [] (fail closed — no browser origin is
    trusted until the deploy lists it explicitly). Reads env + the
    APP_ENV module global at call time so tests can monkeypatch them."""
    raw = os.environ.get("CORS_ORIGINS", "")
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return ["*"] if APP_ENV == "dev" else []


# Resolved once at import (the CORSMiddleware is built at import in
# app.main); cors_origins() above is the testable source of truth.
CORS_ORIGINS = cors_origins()


def validate_payment_config():
    """FIX-1 startup gate: outside dev the webhook endpoint secret must
    be configured, otherwise a forged webhook could settle orders.
    P1-2: outside dev the app secret must also be a real value — a
    process still running the shipped dev default refuses to start.
    Reads module globals at call time so tests can monkeypatch them."""
    if APP_ENV != "dev" and not STRIPE_WEBHOOK_SECRET:
        raise RuntimeError(
            "非開發環境必須配置 STRIPE_WEBHOOK_SECRET，否則拒絕啟動 "
            "(FIX-1: webhook 簽名校驗無密鑰可驗）")
    if APP_ENV != "dev" and SECRET_KEY == DEFAULT_SECRET_KEY:
        raise RuntimeError(
            "非開發環境必須配置 SECRET_KEY（目前仍是開發預設值），否則拒絕啟動 "
            "(P1-2: 預設密鑰不可用於非開發環境）")
# Pending-payment timeout (minutes): an unpaid order older than this is
# lazily marked timeout and releases its stock reservation.
PAYMENT_TIMEOUT_MINUTES = int(os.environ.get("PAYMENT_TIMEOUT_MINUTES", "15"))

# --- M1: background scheduler (expired refunds / bargain settle /
# payment-timeout close / cashback sweep) ---
# SCHEDULER_ENABLED is tri-state: "1" = on, "0" = off, unset = dev
# default on, non-dev off WITH an explicit startup warning (never
# silent — see scheduler.validate_scheduler_config).
SCHEDULER_ENABLED_RAW = os.environ.get("SCHEDULER_ENABLED")
SCHEDULER_INTERVAL_SECONDS = int(os.environ.get("SCHEDULER_INTERVAL_SECONDS", "300"))
SCHEDULER_CASHBACK_BATCH_SIZE = int(os.environ.get("SCHEDULER_CASHBACK_BATCH_SIZE", "100"))
# Row-claim lock lease (non-Postgres fallback): a crashed holder's
# lock becomes reclaimable after this long.
SCHEDULER_LOCK_LEASE_SECONDS = int(os.environ.get("SCHEDULER_LOCK_LEASE_SECONDS", "600"))


def scheduler_status() -> dict:
    """Resolve the scheduler switch. Reads module globals at call time
    so tests can monkeypatch them. Never silently off: the reason
    string always says why, and non-dev without an explicit setting
    is off + warned at startup (see app.scheduler)."""
    raw = SCHEDULER_ENABLED_RAW
    if raw is not None:
        enabled = raw.strip().lower() in ("1", "true", "yes", "on")
        reason = ("SCHEDULER_ENABLED=1：调度器已启用，每 "
                  f"{SCHEDULER_INTERVAL_SECONDS} 秒执行一次"
                  if enabled else
                  "SCHEDULER_ENABLED=0：调度器已显式关闭，过期退款 / 砍价结算 / "
                  "超时关闭 / 返现 sweep 不会自动执行，只能手动接口补跑")
        return {"enabled": enabled, "configured_explicitly": True,
                "interval_seconds": SCHEDULER_INTERVAL_SECONDS,
                "cashback_batch_size": SCHEDULER_CASHBACK_BATCH_SIZE,
                "reason": reason}
    if APP_ENV == "dev":
        return {"enabled": True, "configured_explicitly": False,
                "interval_seconds": SCHEDULER_INTERVAL_SECONDS,
                "cashback_batch_size": SCHEDULER_CASHBACK_BATCH_SIZE,
                "reason": "开发环境默认启用调度器，"
                          f"每 {SCHEDULER_INTERVAL_SECONDS} 秒执行一次"}
    return {"enabled": False, "configured_explicitly": False,
            "interval_seconds": SCHEDULER_INTERVAL_SECONDS,
            "cashback_batch_size": SCHEDULER_CASHBACK_BATCH_SIZE,
            "reason": "非开发环境未配置 SCHEDULER_ENABLED，调度器未启动："
                      "过期套餐退款 / 砍价到期结算 / 超时订单关闭 / 返现 sweep "
                      "不会自动执行。请设置 SCHEDULER_ENABLED=1 再启动"}