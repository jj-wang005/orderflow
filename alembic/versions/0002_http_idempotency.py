"""Nullable request-key digest; historical tasks retain NULL and remain compatible."""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("import_task", sa.Column("idempotency_key_digest", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_import_task_idempotency_key_digest", "import_task", ["idempotency_key_digest"])


def downgrade():
    op.drop_constraint("uq_import_task_idempotency_key_digest", "import_task", type_="unique")
    op.drop_column("import_task", "idempotency_key_digest")
