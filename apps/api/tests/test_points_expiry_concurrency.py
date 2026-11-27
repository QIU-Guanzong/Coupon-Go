"""Real PostgreSQL contention between customer spending/refunds and expiry."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event, current_thread

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import cashback_service, models, points_service
from app.database import Base
from app.deps import add_points, points_balance
from app.models_packages import Order
from app.routers import wallet as wallet_router
from app.seed import seed

PG_URL = os.environ.get("P1C_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires disposable PostgreSQL")


@pytest.fixture
def sessions():
    engine = create_engine(PG_URL, pool_size=5, max_overflow=5)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        seed(db)
    try:
        yield factory
    finally:
        engine.dispose()


def customer_and_due(db, reason="claim"):
    account = models.User(email="expiry-race@example.com", password_hash="unused",
                          role="customer", is_active=True)
    db.add(account)
    db.flush()
    grant = models.PointTransaction(user_id=account.id, delta=10, reason=reason,
        expires_at=datetime.utcnow() - timedelta(days=1))
    db.add(grant)
    db.flush()
    return account, grant


def run_sweep(factory, started):
    with factory() as db:
        current_thread().name = "expiry-test-sweep"
        return points_service.sweep_expired_points(db)


def observe_sweep_lock(monkeypatch, reached):
    original = cashback_service.lock_account
    def observed(db, uid):
        if current_thread().name == "expiry-test-sweep":
            reached.set()
        return original(db, uid)
    monkeypatch.setattr(cashback_service, "lock_account", observed)


def test_exchange_commits_before_expiry_recalculates(sessions, monkeypatch):
    with sessions() as db:
        account, _ = customer_and_due(db)
        coupon = db.query(models.Coupon).first()
        coupon.is_points_exchange = True
        account_id, coupon_id = account.id, coupon.id
        db.commit()
    exchange_has_lock, finish_exchange, sweep_started = Event(), Event(), Event()
    observe_sweep_lock(monkeypatch, sweep_started)
    original_balance = points_service.points_balance
    def paused_balance(db, uid):
        exchange_has_lock.set()
        assert finish_exchange.wait(10)
        return original_balance(db, uid)
    monkeypatch.setattr(points_service, "points_balance", paused_balance)
    def exchange():
        with sessions() as db:
            return wallet_router.exchange_points(coupon_id, user=db.get(models.User, account_id), db=db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        spend = pool.submit(exchange)
        assert exchange_has_lock.wait(10)
        expire = pool.submit(run_sweep, sessions, sweep_started)
        assert sweep_started.wait(10)
        assert not expire.done()
        finish_exchange.set()
        assert spend.result(timeout=10)["message"]
        result = expire.result(timeout=10)
    assert result["points"] == 1 and result["expired"] == 1
    with sessions() as db:
        assert points_balance(db, account_id) == 0
        assert db.query(models.WalletItem).filter_by(user_id=account_id, coupon_id=coupon_id).count() == 1


def test_refund_points_clawback_commits_before_expiry_recalculates(sessions, monkeypatch):
    with sessions() as db:
        account, grant = customer_and_due(db, reason="redeem")
        coupon = db.query(models.Coupon).first()
        order = Order(user_id=account.id, amount=10, paid_amount=10, payment_status="paid")
        db.add(order)
        db.flush()
        item = models.WalletItem(user_id=account.id, coupon_id=coupon.id,
                                 order_id=order.id, source="purchase", status=2)
        db.add(item)
        db.flush()
        grant.ref_id = item.id
        account_id, order_id = account.id, order.id
        db.commit()
    refund = sessions()
    try:
        assert points_service.clawback_order_points(refund, refund.get(Order, order_id)) == 10
        started = Event()
        observe_sweep_lock(monkeypatch, started)
        with ThreadPoolExecutor(max_workers=1) as pool:
            expire = pool.submit(run_sweep, sessions, started)
            assert started.wait(10)
            assert not expire.done()
            refund.commit()
            result = expire.result(timeout=10)
        assert result["points"] == 0 and result["expired"] == 0
        with sessions() as db:
            assert points_balance(db, account_id) == 0
            assert db.query(models.PointTransaction).filter_by(user_id=account_id, reason="refund_clawback").count() == 1
            assert db.query(models.PointTransaction).filter_by(user_id=account_id, reason="expire").count() == 0
    finally:
        refund.close()


def test_expiry_commits_before_exchange_checks_balance(sessions, monkeypatch):
    with sessions() as db:
        account, _ = customer_and_due(db)
        coupon = db.query(models.Coupon).first()
        coupon.is_points_exchange = True
        account_id, coupon_id = account.id, coupon.id
        db.commit()
    expiry_has_lock, finish_expiry, exchange_waiting = Event(), Event(), Event()
    original_lots = points_service.lot_remaining
    def paused_lots(db, uid):
        expiry_has_lock.set()
        assert finish_expiry.wait(10)
        return original_lots(db, uid)
    monkeypatch.setattr(points_service, "lot_remaining", paused_lots)
    original_lock = cashback_service.lock_account
    def observed_lock(db, uid):
        if current_thread().name == "expiry-test-exchange":
            exchange_waiting.set()
        return original_lock(db, uid)
    monkeypatch.setattr(cashback_service, "lock_account", observed_lock)
    def exchange():
        current_thread().name = "expiry-test-exchange"
        with sessions() as db:
            return wallet_router.exchange_points(coupon_id, user=db.get(models.User, account_id), db=db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        expire = pool.submit(run_sweep, sessions, Event())
        assert expiry_has_lock.wait(10)
        spend = pool.submit(exchange)
        assert exchange_waiting.wait(10)
        assert not spend.done()
        finish_expiry.set()
        assert expire.result(timeout=10)["points"] == 10
        with pytest.raises(HTTPException) as error:
            spend.result(timeout=10)
        assert error.value.status_code == 400
    with sessions() as db:
        assert points_balance(db, account_id) == 0
        assert db.query(models.WalletItem).filter_by(user_id=account_id, coupon_id=coupon_id).count() == 0
