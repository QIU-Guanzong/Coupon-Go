"""Initial schema: all tables from app.models (users, otp, categories, areas,
shops, branches, coupons, wallet, redemptions, points, favorites,
notifications, search history, subscription plans)."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    from app.database import Base
    from app import models  # noqa: F401
    Base.metadata.create_all(op.get_bind())


def downgrade():
    from app.database import Base
    from app import models  # noqa: F401
    Base.metadata.drop_all(op.get_bind())
