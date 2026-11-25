"""Merchant notifications (dianping §1.5).

In-app站内信 only — no push / SMS channel yet. Callers add the row and
commit as part of their own transaction; notify() only flushes so a
failed caller transaction never leaves an orphan notification behind.
"""
from sqlalchemy.orm import Session

from . import models


def notify(db: Session, user_id: int, title: str, body: str,
           notification_type: str = "general", target_coupon_id=None,
           target_shop_id=None) -> models.Notification:
    n = models.Notification(
        user_id=user_id, title=title, body=body,
        notification_type=notification_type,
        target_coupon_id=target_coupon_id,
        target_shop_id=target_shop_id)
    db.add(n)
    db.flush()
    return n


def notify_review_account(db: Session, recipient_id: int | None, actor_id: int,
                          title: str, body: str, notification_type: str,
                          shop_id: int) -> None:
    """Deliver only to a real, active recipient, within the review transaction."""
    if recipient_id is None or recipient_id == actor_id:
        return
    active_id = (db.query(models.User.id)
                 .filter(models.User.id == recipient_id,
                         models.User.is_active.is_(True),
                         models.User.deleted_at.is_(None)).scalar())
    if active_id is not None:
        notify(db, active_id, title, body, notification_type,
               target_shop_id=shop_id)
