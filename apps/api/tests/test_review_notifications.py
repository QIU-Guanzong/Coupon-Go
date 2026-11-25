"""R5: app inbox notices use existing review permissions and committed state."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from app import models
from app.models_line_b import Review
from app.routers import reviews
from tests.test_merchant_reviews_read import (
    ADMIN, CUSTOMER, OTHER_OWNER, OWNER, STAFF, _headers, workspace,
)
from tests.test_review_read_metadata import _database


def _post(client, review_id, action, actor, **json):
    return client.post(f"/api/v3/reviews/{review_id}/{action}",
                       headers=_headers(actor), json=json)


def _inbox(client, actor):
    response = client.get("/api/v3/get-notification-list", headers=_headers(actor))
    assert response.status_code == 200, response.text
    return response.json()["notifications"]


def test_first_visible_reply_notifies_its_author_only_and_retries_edits_do_not(workspace):
    c, _ = workspace
    for actor in (CUSTOMER, OTHER_OWNER, STAFF):
        assert _post(c, 101, "reply", actor, reply="not allowed").status_code == 403
    assert not _inbox(c, CUSTOMER)

    for actor, body in [(OWNER, "Private reply body"),
                        (OWNER, "Private reply body"), (ADMIN, "Updated reply")]:
        assert _post(c, 101, "reply", actor, reply=body).status_code == 200
    assert _post(c, 102, "reply", OWNER, reply="Already replied").status_code == 200
    notices = _inbox(c, CUSTOMER)
    assert len(notices) == 1
    assert notices[0]["notification_type"] == "review_reply"
    assert notices[0]["target_shop_id"] == 11
    assert "Private reply body" not in str(notices)
    assert "Updated reply" not in str(notices)
    for actor in (OWNER, OTHER_OWNER, ADMIN, STAFF):
        assert not _inbox(c, actor)


def test_two_authorized_first_replies_concurrently_deliver_one_notice(workspace):
    c, _ = workspace

    def reply(actor):
        return _post(c, 101, "reply", actor, reply="Nearly simultaneous").status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reply, (OWNER, ADMIN))) == [200, 200]
    assert [item["notification_type"] for item in _inbox(c, CUSTOMER)] == ["review_reply"]


@pytest.mark.parametrize("status,author_self,inactive", [
    ("disputed", False, False), ("hidden", False, False),
    ("visible", True, False), ("visible", False, True),
])
def test_reply_skips_nonpublic_self_and_inactive_recipient(workspace, status, author_self, inactive):
    c, _ = workspace
    with _database() as db:
        target = db.get(Review, 101)
        target.status = status
        if author_self:
            target.user_id = ADMIN[0]
        if inactive:
            db.get(models.User, CUSTOMER[0]).is_active = False
        db.commit()
    assert _post(c, 101, "reply", ADMIN, reply="No notice").status_code == 200
    with _database() as db:
        assert db.query(models.Notification).count() == 0


@pytest.mark.parametrize("decision,result,body", [
    ("uphold", "hidden", "評價已隱藏"),
    ("dismiss", "visible", "評價已恢復可見"),
])
def test_successful_dispute_decision_notifies_shop_owner_only(workspace, decision, result, body):
    c, _ = workspace
    for actor in (CUSTOMER, OWNER, OTHER_OWNER, STAFF):
        assert _post(c, 105, "resolve", actor, decision=decision).status_code == 403
    assert _post(c, 101, "resolve", ADMIN, decision=decision).status_code == 400
    assert not _inbox(c, OWNER)

    first = _post(c, 105, "resolve", ADMIN, decision=decision, note="Private note")
    assert first.status_code == 200, first.text
    assert first.json()["review"]["status"] == result
    assert _post(c, 105, "resolve", ADMIN, decision=decision).status_code == 400
    notices = _inbox(c, OWNER)
    assert len(notices) == 1
    assert notices[0]["notification_type"] == "review_dispute_resolved"
    assert notices[0]["target_shop_id"] == 11
    assert body in notices[0]["body"]
    assert "Private note" not in str(notices)
    assert "sensitive reason" not in str(notices)
    for actor in (CUSTOMER, STAFF, OTHER_OWNER, ADMIN):
        assert not _inbox(c, actor)


def test_resolution_skips_deleted_owner_and_assigns_other_shop_correctly(workspace):
    c, _ = workspace
    with _database() as db:
        db.get(models.User, OWNER[0]).deleted_at = datetime(2026, 11, 25, 10)
        db.commit()
    assert _post(c, 105, "resolve", ADMIN, decision="uphold").status_code == 200
    assert _post(c, 221, "resolve", ADMIN, decision="uphold").status_code == 200
    assert [row["target_shop_id"] for row in _inbox(c, OTHER_OWNER)] == [22]
    with _database() as db:
        target_ids = [n.user_id for n in db.query(models.Notification)]
        assert target_ids == [OTHER_OWNER[0]]


def test_rolled_back_first_reply_does_not_leave_notification(workspace, monkeypatch):
    c, _ = workspace
    real_notify = reviews.notify_review_account

    def fail_after_insert(*args, **kwargs):
        real_notify(*args, **kwargs)
        raise RuntimeError("synthetic transaction failure")

    monkeypatch.setattr(reviews, "notify_review_account", fail_after_insert)
    with pytest.raises(RuntimeError, match="synthetic transaction"):
        _post(c, 101, "reply", OWNER, reply="Never committed")
    assert not _inbox(c, CUSTOMER)
    with _database() as db:
        assert not db.get(Review, 101).reply_text
