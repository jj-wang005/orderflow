"""Add order version, simulation provenance, audit and unique purchase position."""
from alembic import op
import sqlalchemy as sa
revision="0004"
down_revision="0003"
branch_labels=None
depends_on=None

def upgrade():
    op.add_column("sales_order",sa.Column("edit_revision",sa.Integer(),nullable=False,server_default="0"))
    op.add_column("sales_order",sa.Column("is_simulated_manual",sa.Boolean(),nullable=False,server_default=sa.text("0")))
    op.create_unique_constraint("uq_sales_order_purchase_position","sales_order",["import_task_id","source_sheet_name","source_row_number"])
    op.create_table("order_change",
        sa.Column("id",sa.BigInteger(),primary_key=True,autoincrement=True),
        sa.Column("order_id",sa.BigInteger(),sa.ForeignKey("sales_order.id"),nullable=False),
        sa.Column("target",sa.String(64),nullable=False),sa.Column("request_digest",sa.String(64),nullable=False),
        sa.Column("request_hash",sa.String(64),nullable=False),sa.Column("payload",sa.JSON(),nullable=False),
        sa.Column("before_data",sa.JSON(),nullable=True),sa.Column("after_data",sa.JSON(),nullable=False),
        sa.Column("note",sa.String(500),nullable=False),sa.Column("actor_label",sa.String(128),nullable=False),
        sa.Column("created_at",sa.DateTime(),nullable=False,server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at",sa.DateTime(),nullable=False,server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("target","request_digest",name="uq_order_change_request"),
        mysql_engine="InnoDB",mysql_charset="utf8mb4",mysql_collate="utf8mb4_bin")
    op.create_index("ix_order_change_order_id","order_change",["order_id"])

def downgrade():
    op.drop_table("order_change")
    op.drop_constraint("uq_sales_order_purchase_position","sales_order",type_="unique")
    op.drop_column("sales_order","is_simulated_manual")
    op.drop_column("sales_order","edit_revision")
