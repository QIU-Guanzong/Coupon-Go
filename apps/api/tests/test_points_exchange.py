"""Native, SQL-backed points-exchange contracts plus opt-in PostgreSQL contention."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import config, errors, models, security
from app.database import Base, get_db
from app.deps import points_balance
from app.main import app
from app.routers import wallet
from app.timeutil import utcnow


def make_user(db, label, *, role="customer", balance=0):
    user = models.User(email=f"{label}@example.test", password_hash="unused", role=role,
                       is_active=True)
    db.add(user)
    db.flush()
    if balance:
        db.add(models.PointTransaction(user_id=user.id, delta=balance, reason="test"))
    return user


def make_shop(db, vendor, *, approved=True, name="Points test shop"):
    shop = models.Shop(name=name, vendor_id=vendor.id, is_approved=approved)
    db.add(shop)
    db.flush()
    return shop


def make_reward(db, shop, **changes):
    now = utcnow()
    kwargs = dict(shop_id=shop.id, vendor_id=shop.vendor_id, coupon_name="Test points reward",
                  status="active", is_points_exchange=True, total=1, remaining=1,
                  valid_from=now - timedelta(days=1), valid_to=now + timedelta(days=1))
    coupon = models.Coupon(**(kwargs | changes))
    db.add(coupon)
    db.flush()
    return coupon


def auth(user_id, role="customer"):
    token = security.create_access_token(str(user_id), role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sqlite_api(tmp_path, monkeypatch):
    db_engine = create_engine(f"sqlite:///{tmp_path}/points.sqlite",
                              connect_args={"check_same_thread": False})
    Base.metadata.create_all(db_engine)
    sessions = sessionmaker(bind=db_engine, autoflush=False)

    def request_session():
        with sessions() as db:
            yield db

    # Do not run application startup or touch its configured database.
    monkeypatch.setitem(app.dependency_overrides, get_db, request_session)
    with sessions.begin() as db:
        vendor = make_user(db, "vendor", role="vendor")
        shop = make_shop(db, vendor)
        customer = make_user(db, "buyer", balance=config.POINTS_EXCHANGE_COST + 1)
        second = make_user(db, "second", balance=config.POINTS_EXCHANGE_COST)
        ids = (vendor.id, shop.id, customer.id, second.id)
    try:
        yield TestClient(app, raise_server_exceptions=False), sessions, ids
    finally:
        db_engine.dispose()


def test_native_list_filters_role_and_eligibility_then_issues_exact_wallet_item(sqlite_api,
                                                                          monkeypatch):
    monkeypatch.setattr(config, "POINTS_EXCHANGE_COST", 7)
    client, sessions, (vendor_id, shop_id, buyer, second) = sqlite_api
    with sessions.begin() as db:
        shop = db.get(models.Shop, shop_id)
        unapproved = make_shop(db, db.get(models.User, vendor_id), approved=False, name="Unapproved")
        good = make_reward(db, shop, coupon_name="Redeem me", sale_price=0).id
        existing = make_reward(db, shop, coupon_name="Already owned").id
        db.add(models.WalletItem(user_id=buyer, coupon_id=existing, status=3, source="exchange"))
        bad = [
            make_reward(db, unapproved).id,
            make_reward(db, shop, sale_price=1).id,
            make_reward(db, shop, sale_price=-1).id,
            make_reward(db, shop, kind="package").id,
            make_reward(db, shop, is_points_exchange=False).id,
            make_reward(db, shop, status="paused").id,
            make_reward(db, shop, valid_from=utcnow() + timedelta(hours=1)).id,
            make_reward(db, shop, valid_to=utcnow()).id,
            make_reward(db, shop, remaining=0).id,
        ]
        starting_balance = points_balance(db, buyer)

    url, redeem = "/api/v3/points/exchange-options", "/api/v3/points/exchange"
    assert client.get(url).status_code == client.post(redeem, params={"coupon_id": good}).status_code == 401
    assert client.get(url, headers=auth(vendor_id, "vendor")).status_code == 403
    assert client.post(redeem, headers=auth(vendor_id, "vendor"), params={"coupon_id": good}).status_code == 403
    choices = client.get(url, headers=auth(buyer))
    assert choices.status_code == 200, choices.text
    assert choices.headers["cache-control"] == "no-store"
    assert choices.json() == {"cost": 7, "coupons": [
        {"id": good, "coupon_name": "Redeem me", "shop_name": "Points test shop"},
    ]}
    for cid in bad + [existing]:
        result = client.post(redeem, headers=auth(buyer), params={"coupon_id": cid})
        assert result.status_code in (400, 404), result.text
    assert client.post("/api/v3/new-favorite-coupon", headers=auth(buyer),
                       params={"coupon_id": good}).json()["code"] == "coupon_points_only"
    result = client.post(redeem, headers=auth(buyer), params={"coupon_id": good})
    assert result.status_code == 200, result.text
    assert result.json()["balance"] == starting_balance - 7
    item_id = result.json()["wallet_item_id"]
    assert item_id > 0
    assert client.post(redeem, headers=auth(buyer), params={"coupon_id": good}).status_code == 400
    assert client.post(redeem, headers=auth(second), params={"coupon_id": good}).status_code == 400

    transactions = client.get("/api/v3/points", headers=auth(buyer)).json()["transactions"]
    assert [(row["ref_id"], row["delta"]) for row in transactions if row["reason"] == "exchange"] == [(good, -7)]
    items = client.get("/api/v3/get-favorite-coupons-list", headers=auth(buyer)).json()["items"]
    assert [(row["id"], row["source"]) for row in items if row["coupon_id"] == good] == [(item_id, "exchange")]
    with sessions.begin() as db:
        coupon = db.get(models.Coupon, good)
        assert (coupon.remaining, coupon.downloads) == (0, 1)
        received = db.get(models.WalletItem, item_id)
        assert (received.user_id, received.status, received.order_id) == (buyer, 1, None)
        # Scheduler may transition inventory status after the last unit goes.
        coupon.status = "sold_out"
    qr = client.post("/api/v3/store-scaned-qr-values", headers=auth(buyer),
                     params={"wallet_item_id": item_id})
    assert qr.status_code == 200, qr.text
    scan = client.post("/api/v3/new-scan-coupon", headers=auth(vendor_id, "vendor"),
                       json={"code": qr.json()["code"]})
    assert scan.status_code == 200, scan.text


def test_failure_after_debit_flush_rolls_back_stock_wallet_and_points(sqlite_api, monkeypatch):
    client, sessions, (_, shop_id, buyer, _) = sqlite_api
    with sessions.begin() as db:
        cid = make_reward(db, db.get(models.Shop, shop_id)).id
    original = wallet.points.spend_points

    def fail_after_flush(db, *args, **kwargs):
        original(db, *args, **kwargs)
        assert db.query(models.WalletItem).filter_by(coupon_id=cid).count() == 1
        assert db.query(models.PointTransaction).filter_by(reason="exchange", ref_id=cid).count() == 1
        assert db.get(models.Coupon, cid).remaining == 0
        raise RuntimeError("forced late test failure")

    monkeypatch.setattr(wallet.points, "spend_points", fail_after_flush)
    failed = client.post("/api/v3/points/exchange", headers=auth(buyer), params={"coupon_id": cid})
    assert failed.status_code == 500
    with sessions() as db:
        assert (db.get(models.Coupon, cid).remaining, db.get(models.Coupon, cid).downloads) == (1, 0)
        assert db.query(models.WalletItem).filter_by(coupon_id=cid).count() == 0
        assert db.query(models.PointTransaction).filter_by(reason="exchange", ref_id=cid).count() == 0
        assert points_balance(db, buyer) == config.POINTS_EXCHANGE_COST + 1

    monkeypatch.setattr(wallet.points, "spend_points", original)
    retried = client.post("/api/v3/points/exchange", headers=auth(buyer), params={"coupon_id": cid})
    assert retried.status_code == 200, retried.text


@pytest.fixture
def postgres_sessions():
    url = os.getenv("COUPON_POINTS_TEST_PG_URL")
    if not url:
        pytest.skip("requires isolated PostgreSQL COUPON_POINTS_TEST_PG_URL")
    from sqlalchemy.engine import make_url
    target = make_url(url)
    if (target.get_backend_name() != "postgresql"
            or target.host not in (None, "127.0.0.1", "localhost", "::1")
            or not (target.database or "").startswith("coupon_go_points_test_")):
        pytest.fail("use a locally isolated coupon_go_points_test_* database")
    schema = f"test_{uuid4().hex}"
    admin = create_engine(url)
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with admin.begin() as db:
            db.execute(text(f"CREATE SCHEMA {schema}"))
        Base.metadata.create_all(engine)
        yield sessionmaker(bind=engine, autoflush=False)
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        admin.dispose()


@pytest.mark.parametrize("same_account", [True, False], ids=["one-balance", "last-stock"])
def test_postgres_parallel_exchange_serializes(same_account, postgres_sessions, monkeypatch):
    sessions = postgres_sessions
    cost = config.POINTS_EXCHANGE_COST
    with sessions.begin() as db:
        vendor = make_user(db, "vendor", role="vendor")
        shop = make_shop(db, vendor)
        first = make_user(db, "buyer", balance=cost)
        first_id = first.id
        second_id = first_id if same_account else make_user(db, "other", balance=cost).id
        reward_id = make_reward(db, shop).id
        second_reward_id = make_reward(db, shop).id if same_account else reward_id

    first_reserved, allow_commit, second_started = Event(), Event(), Event()
    original_issue = wallet.issue_wallet_item
    pids = {}

    def pause_first(db, user_id, coupon_id, source, order_id=None):
        if user_id == first_id and coupon_id == reward_id:
            first_reserved.set()
            assert allow_commit.wait(10)
        return original_issue(db, user_id, coupon_id, source, order_id=order_id)

    monkeypatch.setattr(wallet, "issue_wallet_item", pause_first)

    def run(label, uid, cid):
        with sessions() as db:
            pids[label] = db.scalar(text("SELECT pg_backend_pid()"))
            if label == "second":
                second_started.set()
            try:
                return wallet.exchange_points(cid, user=db.get(models.User, uid), db=db)
            except errors.ApiError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_job = pool.submit(run, "first", first_id, reward_id)
        try:
            assert first_reserved.wait(10)
            second_job = pool.submit(run, "second", second_id, second_reward_id)
            assert second_started.wait(10)
            deadline = monotonic() + 5
            with sessions() as observer:
                while pids["first"] not in observer.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": pids["second"]}):
                    assert monotonic() < deadline, "second exchange never blocked on first transaction"
                    sleep(.025)
        finally:
            allow_commit.set()
        assert first_job.result(timeout=10)["wallet_item_id"] > 0
        assert second_job.result(timeout=10) == (
            "points_insufficient" if same_account else "points_coupon_unavailable")

    with sessions() as db:
        assert points_balance(db, first_id) == 0
        assert points_balance(db, second_id) == (0 if same_account else cost)
        assert db.query(models.PointTransaction).filter_by(reason="exchange").count() == 1
        assert db.query(models.WalletItem).filter_by(source="exchange").count() == 1
        first_coupon = db.get(models.Coupon, reward_id)
        assert (first_coupon.remaining, first_coupon.downloads) == (0, 1)
        if same_account:
            second_coupon = db.get(models.Coupon, second_reward_id)
            assert (second_coupon.remaining, second_coupon.downloads) == (1, 0)
