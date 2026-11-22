"""Join historic redemption extension with current checkout and record batches."""
import sqlalchemy as sa
from alembic import op

revision = '0030_redemption_batch_bridge'
down_revision = ('0029_application_connect_bridge', '0021_dianping_redemption')
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {c['name']: c for c in inspector.get_columns('redemptions')}
    for name, expected in (('method', sa.String), ('note', sa.Text)):
        if name not in columns or not isinstance(columns[name]['type'], expected):
            raise RuntimeError(f'Incompatible redemption column: {name}')
    if not inspector.has_table('redemption_batches'):
        op.create_table('redemption_batches',
            sa.Column('id', sa.Integer, primary_key=True),
            sa.Column('vendor_id', sa.Integer, sa.ForeignKey('users.id'), nullable=False),
            sa.Column('request_key', sa.String(80), nullable=False),
            sa.Column('request_fingerprint', sa.String(64), nullable=False),
            sa.Column('redemption_ids', sa.Text, nullable=False),
            sa.Column('created_at', sa.DateTime, nullable=False),
            sa.UniqueConstraint('vendor_id', 'request_key', name='uq_redemption_batch_vendor_key'))
    inspector = sa.inspect(op.get_bind())
    columns = {c['name']: c for c in inspector.get_columns('redemption_batches')}
    for name, expected in (('vendor_id', sa.Integer), ('request_key', sa.String),
                           ('request_fingerprint', sa.String), ('redemption_ids', sa.Text)):
        if name not in columns or not isinstance(columns[name]['type'], expected):
            raise RuntimeError(f'Incompatible redemption batch column: {name}')
    unique = [set(c['column_names']) for c in inspector.get_unique_constraints('redemption_batches')]
    if {'vendor_id', 'request_key'} not in unique:
        raise RuntimeError('Missing redemption batch uniqueness constraint')


def downgrade():
    raise RuntimeError('Redemption history requires a coordinated restore')
