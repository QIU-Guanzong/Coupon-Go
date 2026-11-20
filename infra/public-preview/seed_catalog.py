"""Populate a NEW isolated preview database with the repository's demo seed."""
from pathlib import Path
import os
import secrets

from app import main, models, security  # register every model before create_all

from app.database import init_db, SessionLocal
from app.seed import seed
from app.seed_bargain import seed_bargain_campaigns
from app.seed_showcase import seed_showcase

path = Path(os.environ["PREVIEW_SEED_PATH"])
if path.exists():
    raise SystemExit("Refusing to overwrite an existing catalogue")
path.parent.mkdir(parents=True, exist_ok=True)
init_db()
with SessionLocal() as db:
    seed(db)
    seed_bargain_campaigns(db)
    seed_showcase(db)
    # Discard every known demo password before the database is published.
    for user in db.query(models.User):
        user.password_hash = security.hash_password(secrets.token_urlsafe(32))
    db.commit()
print("Fresh isolated demo catalogue created")
