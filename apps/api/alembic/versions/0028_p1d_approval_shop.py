"""Persist the shop created or safely bound by an approved application."""
import sqlalchemy as sa
from alembic import op

revision = '0028_p1d_approval_shop'
down_revision = '0027_p1d_forward_bridge'
branch_labels = None
depends_on = None


def upgrade():
    columns = {column['name']: column for column in sa.inspect(op.get_bind()).get_columns('merchant_applications')}
    existing = columns.get('approved_shop_id')
    if existing is None:
        op.add_column('merchant_applications', sa.Column('approved_shop_id', sa.Integer(), nullable=True))
    elif not isinstance(existing['type'], sa.Integer) or not existing['nullable']:
        raise RuntimeError('Incompatible onboarding column: merchant_applications.approved_shop_id')


def downgrade():
    raise RuntimeError('Approved shop bindings require a coordinated restore')
