"""AI drafts, original input and explicit confirmation audit; no historical updates."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import MEDIUMBLOB

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ai_extraction',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('customer_id', sa.String(64), sa.ForeignKey('customer_config.customer_id'), nullable=False),
        sa.Column('request_digest', sa.String(64), nullable=False, unique=True),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('source_hash', sa.String(64), nullable=False),
        sa.Column('source_kind', sa.String(16), nullable=False),
        sa.Column('file_name', sa.String(255), nullable=False),
        sa.Column('original_content', MEDIUMBLOB(), nullable=False),
        sa.Column('config_snapshot', sa.JSON(), nullable=False),
        sa.Column('source_manifest', sa.JSON(), nullable=False),
        sa.Column('original_result', sa.JSON(), nullable=True),
        sa.Column('edited_result', sa.JSON(), nullable=True),
        sa.Column('audit', sa.JSON(), nullable=False),
        sa.Column('metrics', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('provider', sa.String(16), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('error_code', sa.String(64)), sa.Column('error_message', sa.String(1000)),
        sa.Column('confirm_digest', sa.String(64), unique=True), sa.Column('confirm_hash', sa.String(64)),
        sa.Column('confirmed_at', sa.DateTime()),
        sa.Column('import_task_id', sa.BigInteger(), sa.ForeignKey('import_task.id'), unique=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        mysql_engine='InnoDB', mysql_charset='utf8mb4', mysql_collate='utf8mb4_bin')
    for field in ('customer_id','source_hash','status'):
        op.create_index('ix_ai_extraction_'+field, 'ai_extraction', [field])


def downgrade():
    op.drop_table('ai_extraction')
