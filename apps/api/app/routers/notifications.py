"""Notifications + limited-time notification toggle."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_user

router = APIRouter()


@router.get("/get-notification-list")
def get_notifications(user: models.User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    rows = (db.query(models.Notification)
            .filter((models.Notification.user_id == user.id) |
                    (models.Notification.user_id.is_(None)))
            .order_by(models.Notification.id.desc()).limit(50).all())
    unread = sum(1 for r in rows if not r.is_read and r.user_id == user.id)
    return {
        "notification_count": unread,
        "notifications": [schemas.NotificationOut.model_validate(r).model_dump(mode="json") for r in rows],
    }


@router.post("/notifications/{notification_id}/read")
def mark_read(notification_id: int, user: models.User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    n = db.query(models.Notification).filter(
        models.Notification.id == notification_id,
        models.Notification.user_id == user.id).first()
    if n is None:
        raise HTTPException(status_code=404, detail="找不到此通知")
    n.is_read = True
    db.commit()
    return {"message": "已標示為已讀"}


@router.post("/notifications/read-all")
def mark_all_read(user: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    db.query(models.Notification).filter(
        models.Notification.user_id == user.id).update({"is_read": True})
    db.commit()
    return {"message": "全部已標示為已讀"}


@router.post("/limited-time-notification")
def limited_time_notification(enabled: bool,
                              user: models.User = Depends(get_current_user),
                              db: Session = Depends(get_db)):
    user.limited_time_notify = enabled
    db.commit()
    return {"limited_time_notification": enabled}
