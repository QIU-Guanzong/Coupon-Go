"""Run with Python 3.10 and requirements.lock from any working directory."""
import os
from datetime import datetime as real_datetime, timezone
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
API = HERE.parents[1] / "apps" / "api"
if not (API / "app" / "main.py").exists():
    raise SystemExit("Cannot find apps/api in this checkout")
sys.path[:0] = [str(HERE), str(API)]

temp = TemporaryDirectory(prefix="coupon-public-test-")
directory = Path(temp.name)
database = directory / "catalog.db"
uploads = directory / "uploads"
env = dict(os.environ, APP_ENV="dev", SCHEDULER_ENABLED="0",
           DATABASE_URL="sqlite:///" + str(database),
           PREVIEW_SEED_PATH=str(database), UPLOAD_DIR=str(uploads),
           PYTHONPATH=str(API))
env.pop("COUPON_WEB_DIR", None)
subprocess.run([sys.executable, str(HERE / "seed_catalog.py")],
               env=env, check=True, stdout=subprocess.DEVNULL)
with sqlite3.connect(database) as db:
    db.execute("UPDATE bargain_campaigns SET expires_at='2020-01-01' WHERE id=1")
    db.commit()

os.environ.update(APP_ENV="production", SECRET_KEY=secrets.token_urlsafe(32),
                  STRIPE_WEBHOOK_SECRET=secrets.token_urlsafe(32),
                  SCHEDULER_ENABLED="0", SEED_ON_STARTUP="0",
                  DATABASE_URL="sqlite:///file:" + str(database) + "?mode=ro&uri=true",
                  UPLOAD_DIR=str(uploads))
os.environ.pop("COUPON_WEB_DIR", None)

from fastapi.testclient import TestClient
from sqlalchemy import text
from app.database import engine
from public_api import app, _qbot_slots, _reserve_qbot_budget


class PublicPreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        engine.dispose()
        temp.cleanup()

    def test_catalogue_routes_and_search(self):
        for path in ("/api/v3/home", "/api/v3/packages",
                     "/api/v3/package-details?package_id=1",
                     "/api/v3/shop-details?shop_id=1",
                     "/api/v3/coupon-details-new?coupon_id=1",
                     "/api/v3/new-filter-details",
                     "/api/v3/get-classification-coupons",
                     "/api/v3/nearby-shops?lat=22.28&lng=114.17",
                     "/api/v3/shops/1/map", "/api/v3/shops/1/reviews",
                     "/api/v3/shops/1/rating", "/api/v3/bargain-campaigns"):
            with self.subTest(path=path):
                r = self.client.get(path, headers={
                    "Authorization": "Bearer invalid", "Cookie": "session=invalid"})
                self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.post(
            "/api/v3/auto-suggestion-new", params={"keyword": "壽司"}).status_code, 200)
        self.assertEqual(self.client.post(
            "/api/v3/auto-suggestion-new", params={"keyword": "x" * 101}).status_code, 403)

    def test_sensitive_and_mutating_routes_are_denied(self):
        cases = (("POST", "/api/v3/login"), ("POST", "/api/v3/new-register"),
                 ("GET", "/api/v3/_dev/otp?email=x"), ("GET", "/api/v3/admin/orders"),
                 ("POST", "/api/v3/orders/1/demo-payment"), ("POST", "/api/v3/orders/1/refund"),
                 ("POST", "/api/v3/new-favorite-coupon"), ("GET", "/api/v3/bargain-sessions/1"),
                 ("GET", "/api/v3/get-profile"), ("GET", "/docs"),
                 ("GET", "/openapi.json"), ("GET", "/uploads/file"))
        for method, path in cases:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.client.request(method, path).status_code, 403)

    def test_expired_campaign_does_not_write(self):
        campaigns = self.client.get("/api/v3/bargain-campaigns").json()["campaigns"]
        self.assertEqual(next(c for c in campaigns if c["id"] == 1)["status"], "expired")
        with sqlite3.connect(database) as db:
            self.assertEqual(db.execute(
                "SELECT status FROM bargain_campaigns WHERE id=1").fetchone()[0], "active")

    def test_sqlite_rejects_writes(self):
        with self.assertRaises(Exception):
            with engine.begin() as conn:
                conn.execute(text("UPDATE bargain_campaigns SET name='changed' WHERE id=1"))

    def test_qbot_public_only_bounded_and_separate_from_accounts(self):
        from app.routers import qbot
        quota = directory / "qbot-quota.sqlite"
        payload = {"message": "找一張餐飲券", "language": "zh-Hant", "history": []}
        os.environ.update(PUBLIC_QBOT_ENABLED="1", PUBLIC_QBOT_QUOTA_DB=str(quota),
                          DEEPSEEK_API_KEY="synthetic-test-key")
        qbot._counts.clear()
        class ProviderResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"choices": [{"message": {"content": "這是示例優惠。"}}]}
        def provider(_url, **kwargs):
            instructions = kwargs["json"]["messages"][0]["content"]
            self.assertIn("都是示例", instructions)
            self.assertNotIn("synthetic-test-key", str(kwargs["json"]))
            return ProviderResponse()
        with patch.object(qbot.httpx, "post", side_effect=provider) as send:
            result = self.client.post("/api/v3/qbot/chat", json=payload,
                                      headers={"Authorization": "Bearer fake", "Cookie": "session=fake"})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json(), {"answer": "這是示例優惠。", "points_balance": None})
            self.assertEqual(send.call_count, 1)
            self.assertEqual(self.client.post("/api/v3/qbot/chat", json={"message": "x" * 1201}).status_code, 422)
            self.assertEqual(self.client.post("/api/v3/qbot/chat", content=b"x" * 40001,
                                             headers={"content-type": "application/json"}).status_code, 413)
            self.assertEqual(self.client.post("/api/v3/qbot/chat", data="hello").status_code, 415)
            self.assertEqual(self.client.post("/api/v3/qbot/chat?message=private", json=payload).status_code, 400)
            with sqlite3.connect(quota) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM budgets").fetchone()[0], 2)
                db.execute("UPDATE budgets SET used=200 WHERE period LIKE 'day:%'")
            self.assertEqual(self.client.post("/api/v3/qbot/chat", json=payload).status_code, 429)
            self.assertEqual(send.call_count, 1)
            os.environ["PUBLIC_QBOT_QUOTA_DB"] = str(directory / "missing" / "budget.sqlite")
            self.assertEqual(self.client.post("/api/v3/qbot/chat", json=payload).status_code, 429)
            self.assertEqual(send.call_count, 1)
        self.assertNotIn("找一張餐飲券".encode(), quota.read_bytes())
        os.environ["PUBLIC_QBOT_ENABLED"] = "0"
        self.assertEqual(self.client.post("/api/v3/qbot/chat", json=payload).status_code, 503)

    def test_qbot_concurrency_guard(self):
        os.environ.update(PUBLIC_QBOT_ENABLED="1", PUBLIC_QBOT_QUOTA_DB=str(directory / "concurrency.sqlite"))
        for _ in range(3):
            self.assertTrue(_qbot_slots.acquire(blocking=False))
        try:
            self.assertEqual(self.client.post("/api/v3/qbot/chat", json={"message": "你好"}).status_code, 429)
        finally:
            for _ in range(3):
                _qbot_slots.release()

    def test_qbot_budget_samples_time_under_lock_and_preserves_future_periods(self):
        quota = directory / "midnight-quota.sqlite"
        os.environ["PUBLIC_QBOT_QUOTA_DB"] = str(quota)
        with sqlite3.connect(quota) as db:
            db.execute("CREATE TABLE budgets (period TEXT PRIMARY KEY, used INTEGER NOT NULL)")
            db.execute("INSERT INTO budgets VALUES ('day:2026-11-21', 200)")
            db.execute("INSERT INTO budgets VALUES ('minute:2026-11-21T00:00', 20)")

        class CheckedTime:
            @staticmethod
            def now(_timezone):
                # A second writer must be unable to acquire the lock before
                # the period is sampled, even when the request was queued.
                with sqlite3.connect(quota, timeout=0) as other:
                    with self.assertRaises(sqlite3.OperationalError):
                        other.execute("BEGIN IMMEDIATE")
                return real_datetime(2026, 11, 20, 23, 59, tzinfo=timezone.utc)

        with patch("public_api.datetime", CheckedTime):
            self.assertTrue(_reserve_qbot_budget())
        with sqlite3.connect(quota) as db:
            rows = dict(db.execute("SELECT period, used FROM budgets"))
        self.assertEqual(rows['day:2026-11-20'], 1)
        self.assertEqual(rows['day:2026-11-21'], 200)
        self.assertEqual(rows['minute:2026-11-21T00:00'], 20)


if __name__ == "__main__":
    unittest.main()
