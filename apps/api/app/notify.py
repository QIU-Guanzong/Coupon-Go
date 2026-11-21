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
