"""Points policy layer — the single place that decides how many
points each action awards and how a clawback finds what to reverse.

Low-level ledger writes stay in ``deps.add_points`` /
``deps.points_balance``; this module is the policy on top of them:

- claim  -> ``POINTS_CLAIM`` (+10)
- redeem -> ``POINTS_REDEEM`` (+20)
- review -> ``POINTS_REVIEW`` (+15)

P1-C (points by spend, FEATURE_GAP §3.4) changes exactly one
function: ``award_redeem_points`` — paid coupons earn points on their
own frozen share of the order's actual payment; free coupons keep the
flat value. Every redemption path (scan, supplementary, batch) calls
that function, and every revoke path calls ``clawback_points`` with
the award it reverses, so no caller re-derives the rule.

Clawback rule (unchanged from the revoke endpoint): negate the
latest ledger award with the given reason/ref — never recompute the
amount from the current policy, so a policy change cannot claw back
a different amount than was actually granted.
"""
from sqlalchemy.orm import Session

from . import config, models
from .deps import add_points


def award_claim_points(db: Session, user_id: int, coupon_id: int):
    """Free claim: flat POINTS_CLAIM, ref = the coupon."""
    add_points(db, user_id, config.POINTS_CLAIM, "claim", ref_id=coupon_id)


def award_redeem_points(db: Session, user_id: int, wallet_item_id: int, *, paid_basis=None):
    """Paid redemptions use their frozen share; free redemptions stay flat."""
    from .points_service import award_redeem_points as award
    item = db.get(models.WalletItem, wallet_item_id)
    return award(db, user_id, item, paid_basis=paid_basis)


def award_review_points(db: Session, user_id: int, wallet_item_id: int):
    """Review: flat POINTS_REVIEW, ref = the wallet item."""
    add_points(db, user_id, config.POINTS_REVIEW, "review",
               ref_id=wallet_item_id)


def spend_points(db: Session, user_id: int, amount: int, reason: str,
                 ref_id=None):
    """Deduct points (points-mall exchange, transfer-out). Same ledger,
    negative delta; the §5.2 mall replaces the fixed cost with the
    product's own price but still calls this one function."""
    add_points(db, user_id, -amount, reason, ref_id=ref_id)


def clawback_points(db: Session, user_id: int, reason: str, ref_id,
                    *, revoke_reason: str, revoke_ref_id,
                    created_after=None) -> int:
    """Negate the latest award with (user_id, reason, ref_id).

    Returns the amount clawed back (0 when no such award exists).
    Callers own idempotency (revoke is blocked by revoked_at);
    this function only finds and negates.
    """
    q = db.query(models.PointTransaction).filter_by(
        user_id=user_id, reason=reason, ref_id=ref_id)
    if created_after is not None:
        q = q.filter(models.PointTransaction.created_at >= created_after)
    award = q.order_by(models.PointTransaction.id.desc()).first()
    if award is None:
        return 0
    add_points(db, user_id, -award.delta, revoke_reason,
               ref_id=revoke_ref_id)
    return award.delta
