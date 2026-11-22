"""Expired points processing against isolated test data."""
from datetime import datetime, timedelta
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models, points_service
from app.database import Base


@pytest.fixture
def db(tmp_path):
    url = os.environ.get("P1C_POSTGRES_URL")
    engine = create_engine(url or f"sqlite:///{tmp_path / 'points.db'}")
    if url:
        # Only a caller-supplied, disposable PostgreSQL test DB is reset.
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def user(db, number):
    row = models.User(email=f"points-{number}@example.com", password_hash="unused",
                      role="customer", is_active=True)
    db.add(row)
    db.flush()
    return row


def tx(db, uid, delta, reason, *, expires=None, ref_id=None):
    row = models.PointTransaction(user_id=uid, delta=delta, reason=reason,
                                  expires_at=expires, ref_id=ref_id)
    db.add(row)
    db.flush()
    return row


def test_sweep_only_loads_due_unprocessed_candidates(db, monkeypatch):
    now = datetime(2026, 11, 22, 12)
    monkeypatch.setattr(points_service, "_now", lambda: now)
    for i in range(30):
        account = user(db, i)
        tx(db, account.id, 10, "claim", expires=now + timedelta(days=2))
    previously_expired = user(db, 31)
    old = tx(db, previously_expired.id, 8, "claim", expires=now - timedelta(days=1))
    tx(db, previously_expired.id, -8, "expire", ref_id=old.id)
    fully_spent = user(db, 32)
    tx(db, fully_spent.id, 6, "claim", expires=now - timedelta(days=1))
    tx(db, fully_spent.id, -6, "exchange")
    due = user(db, 33)
    due_id = due.id
    tx(db, due.id, 9, "claim", expires=now - timedelta(days=1))
    db.commit()
    visited = []
    original = points_service.lot_remaining
    def record_lots(session, uid):
        visited.append(uid)
        return original(session, uid)
    monkeypatch.setattr(points_service, "lot_remaining", record_lots)
    first = points_service.sweep_expired_points(db)
    assert first == {"expired": 1, "points": 9, "users": 1, "failed": 0}
    assert set(visited) == {due_id, fully_spent.id}
    visited.clear()
    assert points_service.sweep_expired_points(db)["expired"] == 0
    assert visited == []  # fully spent lots are finalized without a ledger row
    assert sum(t.delta for t in db.query(models.PointTransaction).filter_by(user_id=due_id)) == 0


def test_expiration_targets_the_named_lot_when_dates_are_out_of_order(db, monkeypatch):
    now = datetime(2026, 11, 22, 12)
    monkeypatch.setattr(points_service, "_now", lambda: now)
    account = user(db, 50)
    uid = account.id
    future = tx(db, uid, 10, "claim", expires=now + timedelta(days=30))
    due = tx(db, uid, 20, "review", expires=now - timedelta(days=1))
    future_id, due_id = future.id, due.id
    db.commit()
    assert points_service.sweep_expired_points(db)["points"] == 20
    remaining = {grant.id: amount for grant, amount in points_service.lot_remaining(db, uid)}
    assert remaining == {future_id: 10, due_id: 0}
    assert points_service.sweep_expired_points(db)["points"] == 0
    assert sum(t.delta for t in db.query(models.PointTransaction).filter_by(user_id=uid)) == 10


def test_one_failed_expiry_lot_does_not_block_other_lots_or_retry(db, monkeypatch):
    now = datetime(2026, 11, 22, 12)
    monkeypatch.setattr(points_service, "_now", lambda: now)
    account = user(db, 60)
    first = tx(db, account.id, 10, "claim", expires=now - timedelta(days=1))
    second = tx(db, account.id, 7, "review", expires=now - timedelta(days=1))
    first_id, second_id = first.id, second.id
    db.commit()
    original = points_service.add_points
    def fail_one(session, uid, delta, reason, ref_id=None, **kwargs):
        if ref_id == first_id:
            raise RuntimeError("simulated per-lot failure")
        return original(session, uid, delta, reason, ref_id=ref_id, **kwargs)
    with monkeypatch.context() as patcher:
        patcher.setattr(points_service, "add_points", fail_one)
        result = points_service.sweep_expired_points(db)
    assert result == {"expired": 1, "points": 7, "users": 1, "failed": 1}
    remaining = {grant.id: rem for grant, rem in points_service.lot_remaining(db, account.id)}
    assert remaining == {first_id: 10, second_id: 0}
    assert points_service.sweep_expired_points(db)["points"] == 10
