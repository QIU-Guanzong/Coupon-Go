"""Merchant payout execution seam (Dianping §2.2, P0-4).

The money movement for a merchant withdrawal lives here — and only
here. It is deliberately a stub until P1-B (Stripe Connect) lands:

* ``execute_merchant_payout`` raises ``PayoutNotConfigured`` today.
  The admin execute-payout endpoint turns that into HTTP 503 and the
  withdrawal stays ``approved`` — it must NEVER fall back to marking
  the withdrawal (or its settlement) paid without a real transfer.
* P1-B implements the provider call in this module (create a Stripe
  Connect transfer to the merchant's connected account, keyed by the
  merchant's ``stripe_account_id``) and returns
  ``{"payout_reference": ..., "provider": "stripe_connect"}``. The
  caller records the reference, writes the SettlementTransfer and
  flips the settlement to paid when fully transferred. Nothing else
  in the codebase needs to change.
* User cashback withdrawals (cashback_service) are a different pot of
  money and are untouched by this module.
"""
from sqlalchemy.orm import Session


class PayoutNotConfigured(Exception):
    """No payout provider is wired in yet (P1-B Stripe Connect)."""


class PayoutFailed(Exception):
    """The provider call was attempted and failed; nothing was paid."""


def execute_merchant_payout(db: Session, withdrawal) -> dict:
    """Move `withdrawal.amount` to the merchant. Fail closed.

    P1-B (Stripe Connect) replaces this body with a real transfer to
    the merchant's connected account and returns
    {"payout_reference": <provider transfer id>,
     "provider": "stripe_connect"}.

    Until then this raises PayoutNotConfigured — the caller must leave
    the withdrawal `approved` and record nothing.
    """
    raise PayoutNotConfigured(
        "商户打款尚未接通：P1-B Stripe Connect 未配置，"
        "无法执行真实打款（提款单保持已批准，未标记已付）")
