"""Create a fresh, isolated, synthetic SQLite template for interactive trial.

Run with DATABASE_URL pointing to TRIAL_STATE_DIR/template.db. Never use a
customer database. The application must run separately behind trial_api.
"""
import os
from pathlib import Path
import secrets
import time

requested_root = Path(os.environ['TRIAL_STATE_DIR'])
root = requested_root.resolve()
if not requested_root.is_absolute() or str(root) == '/':
    raise SystemExit('TRIAL_STATE_DIR must be a dedicated absolute directory')
if os.environ.get('APP_ENV') != 'dev' or os.environ.get('SCHEDULER_ENABLED') != '0':
    raise SystemExit('trial requires development simulator with scheduler disabled')
if any(os.environ.get(k) for k in ('STRIPE_SECRET_KEY', 'STRIPE_PUBLISHABLE_KEY', 'STRIPE_WEBHOOK_SECRET',
                                   'DEEPSEEK_API_KEY', 'QBOT_GEMINI_API_KEY', 'QBOT_OPENAI_API_KEY')):
    raise SystemExit('trial must not receive Stripe or AI credentials')
template = root / 'template.db'
if os.environ.get('DATABASE_URL') != f'sqlite:///{template}':
    raise SystemExit('DATABASE_URL must reference the dedicated template')
if template.exists():
    raise SystemExit('template already exists; refusing to overwrite')
root.mkdir(parents=True, exist_ok=True, mode=0o700)
(root / 'uploads').mkdir(exist_ok=True, mode=0o700)
from app import main, models, security  # registers all models
from app.database import init_db, SessionLocal
from app.models_connect import ConnectAccount
from app.seed import seed
from app.seed_bargain import seed_bargain_campaigns
from app.seed_showcase import seed_showcase

init_db()
with SessionLocal() as db:
    seed(db)
    seed_bargain_campaigns(db)
    seed_showcase(db)
    # Never publish the known example passwords from seed.py.
    for user in db.query(models.User).all():
        user.password_hash = security.hash_password(secrets.token_urlsafe(32))
    for shop in db.query(models.Shop).all():
        if db.query(ConnectAccount.id).filter_by(shop_id=shop.id).first() is None:
            db.add(ConnectAccount(shop_id=shop.id,
                stripe_account_id=f'acct_trial_{shop.id}',
                charges_enabled=True, payouts_enabled=True,
                transfers_enabled=True, details_submitted=True,
                onboarding_status='active', manual_payouts=True))
    db.commit()
    db.connection().exec_driver_sql(
        'CREATE TABLE trial_template_metadata (created_at INTEGER NOT NULL)')
    db.connection().exec_driver_sql(
        'INSERT INTO trial_template_metadata (created_at) VALUES (?)', (int(time.time()),))
    db.commit()
os.chmod(template, 0o600)
(root / '.coupon-interactive-trial').write_text('coupon-go-interactive-trial-v1\n')
os.chmod(root / '.coupon-interactive-trial', 0o600)
print('Created isolated synthetic trial template')
