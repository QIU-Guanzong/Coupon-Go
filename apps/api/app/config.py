"""Settings for the Coupon GO! API (development build).

No production secrets live here. SECRET_KEY / DATABASE_URL / REDIS_URL come
from the environment (see infra/.env.example). The dev defaults below are
deliberately insecure and must be overridden before any real deployment.
"""
import os

ENV = os.environ.get("COUPON_GO_ENV", "dev")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure-secret-change-me")
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
# 【待更换】以下值目前暫用原 Coupon GO! 1.2.6 APK bundle 裡提取的舊憑據，
# 只放在本地 infra/.env（不進倉庫、不推送），讓 Places 與 Google 登錄先跑起來。
# 上線前必須換成自有新 Key / 新 Client ID，並給原 Key 加 API 限制、輪換原 Key。
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_OAUTH_CLIENT_ID_1 = os.environ.get("GOOGLE_OAUTH_CLIENT_ID_1", "")
GOOGLE_OAUTH_CLIENT_ID_2 = os.environ.get("GOOGLE_OAUTH_CLIENT_ID_2", "")

QR_TTL_SECONDS = int(os.environ.get("QR_TTL_SECONDS", "900"))  # 15:00
SCAN_DAILY_LIMIT = int(os.environ.get("SCAN_DAILY_LIMIT", "200"))
OTP_TTL_SECONDS = int(os.environ.get("OTP_TTL_SECONDS", "600"))
OTP_LENGTH = 6
