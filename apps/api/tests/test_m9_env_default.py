"""M9-1: an unset environment is production (fail closed)."""
import os
import subprocess
import sys

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _probe(env_overrides, code):
    env = {k: v for k, v in os.environ.items()
           if k not in ("APP_ENV", "COUPON_GO_ENV", "STRIPE_WEBHOOK_SECRET",
                        "STRIPE_SECRET_KEY", "SECRET_KEY", "CORS_ORIGINS")}
    env.update(env_overrides)
    return subprocess.run([sys.executable, "-c", code], cwd=API_DIR, env=env,
                          capture_output=True, text=True, timeout=60)


def test_unset_env_is_production():
    r = _probe({}, "from app import config; print(config.APP_ENV); print(config.CORS_ORIGINS)")
    assert r.returncode == 0, r.stderr
    env, cors = r.stdout.split("\n")[:2]
    assert env == "production"
    assert "*" not in cors


def test_unset_env_refuses_to_start_without_webhook_secret():
    r = _probe({}, "from app import config; config.validate_payment_config()")
    assert r.returncode != 0
    assert "STRIPE_WEBHOOK_SECRET" in r.stderr


def test_unset_env_refuses_demo_payment():
    r = _probe({}, "from app import config; "
                   "print(config.APP_ENV != 'dev' or bool(config.STRIPE_SECRET_KEY))")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "True"  # /orders/{id}/demo-payment guard


def test_explicit_dev_still_dev():
    r = _probe({"APP_ENV": "dev"}, "from app import config; print(config.APP_ENV)")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "dev"
