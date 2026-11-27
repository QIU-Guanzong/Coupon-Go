"""Actual dashboard reconciliation and route authorization in isolated SQLite."""

from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import merchant_finance as mf, models as m, security
from app.database import Base, get_db
from app.main import app
from app.models_line_f import Settlement, ShopMember
from app.models_packages import Order


@pytest.fixture
def weekly(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/weekly.db",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    parse = datetime.fromisoformat
    with sessions.begin() as db:
        roles = {"owner": "vendor", "other": "vendor", "buyer": "customer",
                 "admin": "admin", "staff": "merchant-admin", "pending": "merchant-admin",
                 "cashier": "merchant-admin"}
        users = {name: m.User(email=f"{name}@example.test", password_hash="unused", role=role)
                 for name, role in roles.items()}
        db.add_all(users.values())
        db.flush()
        a = m.Shop(vendor_id=users["owner"].id, name="Shop A", is_approved=True)
        b = m.Shop(vendor_id=users["other"].id, name="Shop B", is_approved=True)
        db.add_all([a, b])
        db.flush()
        for user, role, status in (("staff", "finance", "active"), ("pending", "finance", "pending"),
                                   ("cashier", "cashier", "active")):
            db.add(ShopMember(shop_id=a.id, user_id=users[user].id, role=role, status=status))

        def voucher(shop, claim, scan, amount, rate="0.10", *, refunded=False,
                    revoked=False, free=False):
            coupon = m.Coupon(shop_id=shop.id, vendor_id=shop.vendor_id, coupon_name="PRIVATE_COUPON",
                              coupon_type="Gift" if free else "Price", valid_from=parse("2026-10-01"),
                              valid_to=parse("2026-12-01"))
            db.add(coupon)
            db.flush()
            order = None
            if refunded:
                order = Order(user_id=users["buyer"].id, order_type="coupon", coupon_id=coupon.id,
                              amount=amount, paid_amount=amount, payment_status="paid",
                              refund_status="refunded")
                db.add(order)
                db.flush()
            item = m.WalletItem(user_id=users["buyer"].id, coupon_id=coupon.id,
                                claimed_at=parse(claim), order_id=order.id if order else None)
            db.add(item)
            db.flush()
            db.add(m.Redemption(wallet_item_id=item.id, coupon_id=coupon.id, shop_id=shop.id,
                                scanned_by_vendor_id=shop.vendor_id, scanned_at=parse(scan),
                                face_value=amount, commission_rate_snapshot=Decimal(rate),
                                revoked_at=parse(scan) if revoked else None))

        voucher(a, "2026-10-27T10:00:00", "2026-10-29T10:00:00", 80)
        voucher(a, "2026-11-01T23:59:59", "2026-11-02T00:00:00", 60)
        voucher(a, "2026-11-02T00:00:00", "2026-11-04T09:00:00", 100, ".05")
        voucher(a, "2026-11-07T10:00:00", "2026-11-08T23:59:59", 0, free=True)
        voucher(a, "2026-11-03T10:00:00", "2026-11-05T10:00:00", 90, refunded=True)
        voucher(a, "2026-11-06T10:00:00", "2026-11-08T10:00:00", 120, revoked=True)
        voucher(a, "2026-11-09T00:00:00", "2026-11-09T00:00:00", 500)
        voucher(b, "2026-10-24T23:59:59", "2026-11-02T00:00:00", 40)
        shop_ids = (a.id, b.id)
        headers = {name: {"Authorization": "Bearer " + security.create_access_token(str(u.id), u.role)}
                   for name, u in users.items()}

    def db_override():
        with sessions() as db:
            yield db

    monkeypatch.setitem(app.dependency_overrides, get_db, db_override)
    monkeypatch.setattr(mf, "_now", lambda: parse("2026-11-09T00:00:00+00:00"))
    try:
        yield TestClient(app, raise_server_exceptions=False), headers, shop_ids, sessions
    finally:
        engine.dispose()


def endpoint(shop_id):
    return f"/api/v3/merchant/shops/{shop_id}/weekly-report"


def test_weekly_matches_both_dashboard_windows_and_exposes_only_totals(weekly):
    client, headers, (a, _), sessions = weekly
    response = client.get(endpoint(a), headers=headers["owner"])
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    report = response.json()
    assert set(report) == {"shop_id", "shop_name", "currency", "timezone", "current",
                           "previous", "changes"}
    assert report["shop_id"] == a
    assert report["currency"] == "HKD" and report["timezone"] == "UTC"
    assert report["previous"]["period"] == {"from": "2026-10-26", "to": "2026-11-01", "days": 7}
    assert report["current"]["period"] == {"from": "2026-11-02", "to": "2026-11-08", "days": 7}
    fields = ("claims", "redemptions", "redeemed_claims", "gross_amount", "commission", "net_amount")
    previous, current = report["previous"]["totals"], report["current"]["totals"]
    assert tuple(previous[n] for n in fields) == (2, 1, 1, 80, 8, 72)
    assert tuple(current[n] for n in fields) == (4, 3, 2, 160, 11, 149)
    assert previous["conversion_rate"] == current["conversion_rate"] == .5
    assert report["changes"]["redemptions"] == {"delta": 2, "percent": 200}
    assert report["changes"]["commission"] == {"delta": 3, "percent": 37.5}
    assert report["changes"]["conversion_percentage_points"] == 0
    assert "PRIVATE_COUPON" not in response.text and "@example.test" not in response.text

    with sessions() as db:
        for key in ("current", "previous"):
            period = report[key]["period"]
            expected = mf.shop_dashboard(db, db.get(m.Shop, a), days=7,
                                         end=datetime.fromisoformat(period["to"]))
            assert report[key] == {"period": expected["period"], "totals": expected["totals"]}
        # A report does not create a settlement, mutate claims, or record redemptions.
        assert db.query(Settlement).count() == 0
        assert db.query(m.WalletItem).count() == db.query(m.Redemption).count() == 8


def test_zero_denominator_and_baseline_remain_null_and_counts_stay_shop_local(weekly):
    client, headers, (_, b), _ = weekly
    response = client.get(endpoint(b), headers=headers["other"])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["current"]["totals"]["redemptions"] == 1
    assert result["current"]["totals"]["claims"] == result["current"]["totals"]["redeemed_claims"] == 0
    assert result["current"]["totals"]["net_amount"] == 36
    assert result["current"]["totals"]["conversion_rate"] is None
    assert result["previous"]["totals"]["conversion_rate"] is None
    assert result["changes"]["redemptions"] == {"delta": 1, "percent": None}
    assert result["changes"]["claims"] == {"delta": 0, "percent": None}
    assert result["changes"]["conversion_percentage_points"] is None


@pytest.mark.parametrize("role,status", [
    ("owner", 200), ("admin", 200), ("staff", 200), ("other", 403), ("buyer", 403),
    ("pending", 403), ("cashier", 403), (None, 401),
])
def test_weekly_uses_existing_single_shop_dashboard_scope(weekly, role, status):
    client, headers, (a, _), _ = weekly
    h = headers[role] if role else {}
    assert client.get(endpoint(a), headers=h).status_code == status
    assert client.get(f"/api/v3/merchant/shops/{a}/dashboard", headers=h).status_code == status
    if role == "admin":
        assert client.get(endpoint(99999), headers=h).status_code == 404


@pytest.mark.parametrize("as_of,current_to", [
    ("2026-11-08T23:59:59+00:00", "2026-11-01"),
    ("2026-11-09T07:59:59+08:00", "2026-11-01"),
    ("2026-11-09T00:00:00+00:00", "2026-11-08"),
    ("2026-11-09T08:00:00+08:00", "2026-11-08"),
    ("2026-11-15T15:00:00+00:00", "2026-11-08"),
])
def test_completed_week_switches_at_utc_monday_not_local_monday(weekly, as_of, current_to):
    _, _, (a, _), sessions = weekly
    with sessions() as db:
        report = mf.shop_weekly_report(db, db.get(m.Shop, a), as_of=datetime.fromisoformat(as_of))
    assert report["current"]["period"]["to"] == current_to
    start = datetime.fromisoformat(report["current"]["period"]["from"])
    previous_end = datetime.fromisoformat(report["previous"]["period"]["to"])
    assert (start - previous_end).days == 1


def test_dashboard_trend_sums_visible_shops_without_refunds_or_revocations(weekly, monkeypatch):
    from app.routers import merchant_stats

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromisoformat("2026-11-08T23:45:00+00:00")

    monkeypatch.setattr(merchant_stats, "datetime", FixedDatetime)
    client, headers, _, _ = weekly
    for role, count, gross, net in (("owner", 3, 160, 149), ("admin", 4, 200, 185)):
        response = client.get("/api/v3/merchant/dashboard", params={"days": 7}, headers=headers[role])
        assert response.status_code == 200, response.text
        report = response.json()
        assert len(report["trend"]) == 7
        assert report["trend"][0]["date"] == report["period"]["from"] == "2026-11-02"
        assert report["trend"][-1]["date"] == report["period"]["to"] == "2026-11-08"
        for metric, expected in [("redemptions", count), ("gross_amount", gross), ("net_amount", net)]:
            assert report["totals"][metric] == sum(day[metric] for day in report["trend"]) == expected
        assert report["trend"][3]["gross_amount"] == 0  # refunded
        assert report["trend"][-1]["redemptions"] == 1  # free accepted; revoked excluded
