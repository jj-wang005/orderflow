"""Non-destructive manual revision and append-only correction audit."""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("import_task_row", sa.Column("manual_revision", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "manual_correction",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("row_id", sa.BigInteger(), sa.ForeignKey("import_task_row.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("request_key_digest", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("submitted_values", sa.JSON(), nullable=False),
        sa.Column("before_data", sa.JSON(), nullable=True),
        sa.Column("after_data", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("actor_label", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("row_id", "request_key_digest", name="uq_manual_correction_request"),
        sa.UniqueConstraint("row_id", "revision", name="uq_manual_correction_revision"),
        mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_bin",
    )


def downgrade():
    op.drop_table("manual_correction")
    op.drop_column("import_task_row", "manual_revision")
