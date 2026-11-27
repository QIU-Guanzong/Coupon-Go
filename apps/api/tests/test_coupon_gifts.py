"""Coupon links through real auth, wallet, issuance and redemption routes."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import models
from app.database import get_db
from app.main import app
from app.merchant_finance import shop_dashboard
from app.timeutil import utcnow
from tests.test_points_exchange import auth, make_reward, make_shop, make_user
from tests.test_points_exchange import postgres_sessions, sqlite_api  # noqa: F401

GIFT_PREVIEW = "/api/v3/wallet/gifts/preview"
GIFT_ACCEPT = "/api/v3/wallet/gifts/accept"
WALLET_LIST = "/api/v3/get-favorite-coupons-list"
CLAIM = "/api/v3/new-favorite-coupon"
SHOW = "/api/v3/store-scaned-qr-values"
REDEEM = "/api/v3/new-scan-coupon"


def gift_path(item):
    return f"/api/v3/wallet/items/{item}/gift"


def set_up_coupon(client, sessions, ids, **changes):
    vendor, shop, owner, friend = ids
    with sessions.begin() as db:
        # A customer may already have claimed the last remaining public copy.
        coupon = make_reward(db, db.get(models.Shop, shop), **(
            dict(coupon_name="Afternoon tea", is_points_exchange=False,
                 total=2, remaining=2) | changes))
        coupon_id = coupon.id
    claim = client.post(CLAIM, params={"coupon_id": coupon_id}, headers=auth(owner))
    assert claim.status_code == 200, claim.text
    return coupon_id, claim.json()["wallet_item_id"]


def wallet_item(client, user, item, status):
    result = client.get(WALLET_LIST, params={"status": status}, headers=auth(user))
    assert result.status_code == 200, result.text
    return next((row for row in result.json()["items"] if row["id"] == item), None)


def create_link(client, owner, item):
    result = client.post(gift_path(item), headers=auth(owner))
    assert result.status_code == 200, result.text
    assert result.headers["Cache-Control"] == "no-store"
    return result.json()


def test_gift_moves_usable_coupon_without_inventory_or_points_changes(sqlite_api):
    client, sessions, ids = sqlite_api
    vendor, shop, owner, friend = ids
    coupon_id, original = set_up_coupon(client, sessions, ids, remaining=1)
    qr = client.post(SHOW, params={"wallet_item_id": original}, headers=auth(owner))
    assert qr.status_code == 200, qr.text
    old_code = qr.json()["code"]
    with sessions.begin() as db:
        coupon = db.get(models.Coupon, coupon_id)
        inventory = (coupon.remaining, coupon.downloads)
        ledger_entries = db.query(models.PointTransaction).count()
        db.get(models.WalletItem, original).claimed_at = utcnow() - timedelta(days=2)

    assert wallet_item(client, owner, original, 1)["gift_eligible"] is True
    created = create_link(client, owner, original)
    assert created == create_link(client, owner, original)
    assert wallet_item(client, owner, original, 1)["gift_status"] == "pending"
    link = {"token": created["token"]}
    assert client.post(GIFT_PREVIEW, json=link).status_code == 401
    assert client.post(GIFT_ACCEPT, json=link, headers=auth(vendor, "vendor")).status_code == 403
    assert client.post(GIFT_ACCEPT, json=link, headers=auth(owner)).json()["code"] == "gift_self"
    assert client.post(gift_path(original), headers=auth(friend)).status_code == 404
    assert client.delete(gift_path(original), headers=auth(friend)).status_code == 404

    preview = client.post(GIFT_PREVIEW, json=link, headers=auth(friend))
    assert preview.status_code == 200, preview.text
    assert preview.headers["Cache-Control"] == "no-store"
    assert preview.json() == {
        "coupon_name": "Afternoon tea", "shop_name": "Points test shop",
        "expires_at": created["expires_at"],
    }
    accepted = client.post(GIFT_ACCEPT, json=link, headers=auth(friend))
    assert accepted.status_code == 200, accepted.text
    received = accepted.json()["wallet_item_id"]
    assert received != original
    assert client.post(GIFT_ACCEPT, json=link, headers=auth(friend)).json() == accepted.json()
    assert client.post(GIFT_PREVIEW, json=link, headers=auth(friend)).json()["wallet_item_id"] == received
    assert client.delete(gift_path(original), headers=auth(owner)).status_code == 409
    assert wallet_item(client, owner, original, 1) is None
    sent = wallet_item(client, owner, original, 3)
    assert sent["gift_status"] == "sent" and sent["code"] == ""
    landed = wallet_item(client, friend, received, 1)
    assert landed["source"] == "gift" and landed["gift_eligible"] is False
    with sessions() as db:
        coupon = db.get(models.Coupon, coupon_id)
        assert (coupon.remaining, coupon.downloads) == inventory
        assert db.query(models.PointTransaction).count() == ledger_entries
        assert db.query(models.WalletItem).filter_by(coupon_id=coupon_id, status=1).count() == 1
        assert db.get(models.WalletItem, original).claimed_at == db.get(models.WalletItem, received).claimed_at
        summary = shop_dashboard(db, db.get(models.Shop, shop), days=7)
        assert summary["totals"]["claims"] == 1
        assert summary["coupons"][0]["expired"] == 0

    old = client.post(REDEEM, json={"code": old_code}, headers=auth(vendor, "vendor"))
    assert old.status_code == 400, old.text
    assert client.post(SHOW, params={"wallet_item_id": original},
                       headers=auth(owner)).status_code == 400
    assert client.post(SHOW, params={"wallet_item_id": received},
                       headers=auth(owner)).status_code == 404
    new_qr = client.post(SHOW, params={"wallet_item_id": received}, headers=auth(friend))
    redeemed = client.post(REDEEM, json={"code": new_qr.json()["code"]},
                           headers=auth(vendor, "vendor"))
    assert redeemed.status_code == 200, redeemed.text
    with sessions() as db:
        assert db.query(models.Redemption).filter_by(wallet_item_id=received).count() == 1
        assert db.query(models.PointTransaction).filter_by(user_id=friend, reason="redeem").count() == 1
        summary = shop_dashboard(db, db.get(models.Shop, shop), days=7)
        assert summary["totals"]["redeemed_claims"] == 1
        assert summary["totals"]["conversion_rate"] == 1


def test_cancellation_expiry_and_sender_redemption_invalidate_links(sqlite_api):
    client, sessions, ids = sqlite_api
    vendor, shop, owner, friend = ids
    validity = utcnow() + timedelta(hours=1)
    coupon, original = set_up_coupon(client, sessions, ids, valid_to=validity)
    first = create_link(client, owner, original)
    assert datetime.fromisoformat(first["expires_at"]) == validity.replace(tzinfo=timezone.utc)
    for _ in range(2):
        cancelled = client.delete(gift_path(original), headers=auth(owner))
        assert cancelled.status_code == 204 and not cancelled.content
    second = create_link(client, owner, original)
    assert first["token"] != second["token"]
    with sessions.begin() as db:
        db.get(models.CouponGift, original).expires_at = utcnow()
    third = create_link(client, owner, original)
    assert second["token"] != third["token"]
    for token in (first["token"], second["token"], third["token"] + "x"):
        assert client.post(GIFT_ACCEPT, json={"token": token}, headers=auth(friend)).status_code == 404

    qr = client.post(SHOW, params={"wallet_item_id": original}, headers=auth(owner))
    redeemed = client.post(REDEEM, json={"code": qr.json()["code"]}, headers=auth(vendor, "vendor"))
    assert redeemed.status_code == 200, redeemed.text
    assert client.post(GIFT_ACCEPT, json={"token": third["token"]},
                       headers=auth(friend)).status_code == 404
    red_id = redeemed.json()["redemption_id"]
    revoked = client.post(f"/api/v3/redemptions/{red_id}/revoke", headers=auth(vendor, "vendor"))
    assert revoked.status_code == 200, revoked.text
    assert wallet_item(client, owner, original, 1)["gift_eligible"] is False
    assert client.post(gift_path(original), headers=auth(owner)).json()["code"] == "gift_not_eligible"


def test_per_recipient_limit_and_excluded_provenance(sqlite_api):
    client, sessions, ids = sqlite_api
    vendor, shop, owner, friend = ids
    cid, original = set_up_coupon(client, sessions, ids)
    claimed = client.post(CLAIM, params={"coupon_id": cid}, headers=auth(friend))
    assert claimed.status_code == 200, claimed.text
    link = create_link(client, owner, original)
    res = client.post(GIFT_ACCEPT, json={"token": link["token"]}, headers=auth(friend))
    assert res.status_code == 409, res.text
    assert wallet_item(client, owner, original, 1)["gift_status"] == "pending"

    with sessions.begin() as db:
        shop = db.get(models.Shop, shop)
        excluded = []
        for source, overrides in (
            ("claim", {"sale_price": 1}),
            ("claim", {"kind": "package"}),
            ("exchange", {}),
            ("purchase", {}),
            ("bargain", {}),
            ("gift", {}),
        ):
            coupon = make_reward(db, shop, **overrides)
            if source != "exchange":
                coupon.is_points_exchange = False
            item = models.WalletItem(user_id=owner, coupon_id=coupon.id, source=source)
            db.add(item)
            db.flush()
            excluded.append(item.id)
    for item_id in excluded:
        assert wallet_item(client, owner, item_id, 1)["gift_eligible"] is False
        assert client.post(gift_path(item_id), headers=auth(owner)).json()["code"] == "gift_not_eligible"


@pytest.mark.parametrize("race", ["two_recipients", "regular_claim", "redeem"])
def test_postgresql_competing_operations_only_commit_once(postgres_sessions, monkeypatch, race):
    sessions = postgres_sessions
    assert sessions.kw["bind"].dialect.name == "postgresql"

    def request_session():
        with sessions() as db:
            yield db

    monkeypatch.setitem(app.dependency_overrides, get_db, request_session)
    with sessions.begin() as db:
        vendor = make_user(db, "vendor", role="vendor")
        shop = make_shop(db, vendor)
        owner = make_user(db, "owner")
        friend = make_user(db, "friend")
        other = make_user(db, "other")
        ids, other_id = (vendor.id, shop.id, owner.id, friend.id), other.id

    client = TestClient(app, raise_server_exceptions=False)
    vendor, shop, owner, friend = ids
    cid, original = set_up_coupon(client, sessions, ids)
    token = create_link(client, owner, original)["token"]
    gate = Barrier(2)

    def accept(user_id):
        racer = TestClient(app)
        gate.wait(timeout=10)
        return racer.post(GIFT_ACCEPT, json={"token": token}, headers=auth(user_id))

    def compete():
        if race == "two_recipients":
            return accept(other_id)
        racer = TestClient(app)
        gate.wait(timeout=10)
        if race == "regular_claim":
            return racer.post(CLAIM, params={"coupon_id": cid}, headers=auth(friend))
        return racer.post(REDEEM, json={"code": old_code}, headers=auth(vendor, "vendor"))

    if race == "redeem":
        qr = client.post(SHOW, params={"wallet_item_id": original}, headers=auth(owner))
        assert qr.status_code == 200, qr.text
        old_code = qr.json()["code"]
    model, wanted_id = (models.User, friend) if race == "regular_claim" else (
        models.WalletItem, original)
    with ThreadPoolExecutor(2) as pool, sessions() as blocker:
        blocker.query(model.id).filter_by(id=wanted_id).with_for_update(key_share=True).one()
        first = pool.submit(accept, friend)
        second = pool.submit(compete)
        deadline = monotonic() + 8
        blocked = 0
        while monotonic() < deadline and blocked < 2:
            with sessions() as observer:
                blocked = observer.execute(text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname=current_database() AND wait_event_type='Lock' "
                    "AND position(:table_name in query) > 0"),
                    {"table_name": model.__tablename__}).scalar_one()
            if blocked < 2:
                sleep(0.02)
        blocker.commit()
        results = [first.result(timeout=25), second.result(timeout=25)]
    assert blocked == 2, "both PostgreSQL requests must contend for the held row lock"
    assert sum(r.status_code == 200 for r in results) == 1, [r.text for r in results]
    assert all(r.status_code in (200, 400, 404, 409) for r in results), [r.text for r in results]
    with sessions() as db:
        gift = db.get(models.CouponGift, original)
        item = db.get(models.WalletItem, original)
        received = db.query(models.WalletItem).filter_by(coupon_id=cid, source="gift").all()
        assert len(received) <= 1
        assert db.query(models.WalletItem).filter_by(coupon_id=cid, user_id=friend).count() <= 1
        if received:
            assert item.status == 3 and gift.recipient_wallet_item_id == received[0].id
            assert db.get(models.Coupon, cid).remaining == 1
            assert db.query(models.Redemption).filter_by(wallet_item_id=original).count() == 0
        else:
            assert gift.recipient_wallet_item_id is None
            assert item.status == (2 if race == "redeem" else 1)
