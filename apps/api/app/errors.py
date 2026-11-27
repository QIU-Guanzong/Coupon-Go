"""Stable machine-readable error codes (dianping unify).

Wire format is unchanged for old clients: FastAPI's
``{"detail": message}`` body gains exactly one field, ``code``.
``ApiError`` is an ``HTTPException`` subclass; ``main.py`` registers
a handler that renders ``{"detail": ..., "code": ...}`` for it, so
existing assertions on ``detail`` and status keep passing.

Rules:
- one code per failure condition, not per message string;
- messages stay byte-identical to the ones callers already returned
  (Traditional Chinese copy is a product decision, not an error-code
  decision);
- new endpoints use the factories here; the purchase / redemption /
  refund surfaces migrated in this PR are coupons, redemption and
  wallet. packages / bargain keep plain HTTPException for now and
  follow the same table in a later pass (their messages are unchanged).
"""
from fastapi import HTTPException


class ApiError(HTTPException):
    """HTTPException with a stable machine-readable ``code``."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(status_code=status_code, detail=message)
        self.code = code


def bad_request(message: str):
    """Generic 400 for input problems with no dedicated code (e.g. a
    malformed date filter). Prefer a dedicated factory when one fits."""
    return ApiError(400, "bad_request", message)


# --- coupons / wallet items ---
def coupon_not_found():
    return ApiError(404, "coupon_not_found", "找不到此優惠券")


def coupon_expired():
    return ApiError(400, "coupon_expired", "此優惠券已失效")


def coupon_inactive():
    return ApiError(400, "coupon_inactive", "此優惠券已停用")


def coupon_not_started():
    return ApiError(400, "coupon_not_started", "此優惠券尚未到使用日期")


def coupon_package_must_purchase():
    return ApiError(400, "coupon_package_must_purchase",
                    "套餐券請購買，不可免費領取")


def coupon_points_only():
    return ApiError(400, "coupon_points_only", "積分券請用積分兌換")


def coupon_already_claimed():
    return ApiError(409, "coupon_already_claimed", "您已領取此 Coupon")


def coupon_sold_out():
    return ApiError(400, "coupon_sold_out", "優惠券已達下載上限")


def coupon_unavailable():
    return ApiError(400, "coupon_unavailable", "此優惠券已不能領取")


def coupon_not_usable():
    return ApiError(400, "coupon_not_usable", "此優惠券不能使用")


def qr_generate_failed():
    return ApiError(503, "qr_generate_failed", "暫時未能產生兌換碼，請重試")


# --- redemption ---
def qr_invalid():
    return ApiError(400, "qr_invalid", "無效的 QR 碼！")


def qr_wrong_shop():
    return ApiError(400, "qr_wrong_shop", "無效的 QR 碼！此碼屬於其他商店。")


def qr_expired():
    return ApiError(400, "qr_expired", "QR 碼已過期，請重新出示")


def code_expired():
    return ApiError(400, "code_expired", "兌換碼已失效，請重新出示")


def prepare_expired():
    return ApiError(400, "prepare_expired", "預核銷已失效，請重新預核銷")


def voucher_unpaid():
    return ApiError(400, "voucher_unpaid", "套餐券付款或退款狀態不允許核銷")


def voucher_used():
    return ApiError(400, "voucher_used", "此優惠券已使用")


def voucher_expired():
    return ApiError(400, "voucher_expired", "此優惠券已失效")


def scan_daily_limit():
    return ApiError(400, "scan_daily_limit", "今日掃碼已達上限")


def redemption_replay_mismatch():
    return ApiError(409, "redemption_replay_mismatch",
                    "核銷記錄不符，請重新掃碼")


def redemption_not_found():
    return ApiError(404, "redemption_not_found", "找不到此核銷記錄")


def redemption_already_revoked():
    return ApiError(400, "redemption_already_revoked", "此核銷已被撤销")


def revoke_window_expired(window_minutes: int):
    return ApiError(400, "revoke_window_expired",
                    f"已超過 {window_minutes} 分鐘撤销窗口，請聯絡客服處理")


def forbidden():
    return ApiError(403, "forbidden", "沒有權限")


# --- points exchange ---
def points_coupon_not_found():
    return ApiError(404, "points_coupon_not_found", "找不到此積分券")


def points_insufficient():
    return ApiError(400, "points_insufficient", "積分不足")


def points_coupon_unavailable():
    return ApiError(400, "points_coupon_unavailable", "此優惠券現時不可兌換")


def points_coupon_already_exchanged():
    return ApiError(400, "points_coupon_already_exchanged", "已經兌換過此券")


# --- reviews (legacy /review surface) ---
def review_not_redeemed():
    return ApiError(400, "review_not_redeemed", "只有已使用的優惠券可以評價")


def review_already_done():
    return ApiError(400, "review_already_done", "已經評價過")
