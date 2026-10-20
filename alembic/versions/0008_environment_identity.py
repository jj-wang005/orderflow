"""Managed demo/test ownership identity; original history database is not upgraded."""
from alembic import op
import sqlalchemy as sa
revision='0008'
down_revision='0007'
branch_labels=None
depends_on=None
def upgrade():
    op.create_table('environment_identity',sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('profile',sa.String(16),nullable=False),sa.Column('instance_id',sa.String(36),nullable=False,unique=True),
        sa.Column('dataset_version',sa.String(64),nullable=False),mysql_engine='InnoDB',mysql_charset='utf8mb4',mysql_collate='utf8mb4_bin')
def downgrade():op.drop_table('environment_identity')
