"""V2.0-B additive task links, review receipts and atomic purchase claims.

Existing orders keep NULL keys. No invented line identifiers or data updates.
"""
from alembic import op
import sqlalchemy as sa
revision="0006"
down_revision="0005"
branch_labels=None
depends_on=None

def upgrade():
    op.add_column("import_task",sa.Column("batch_id",sa.String(36),nullable=True))
    op.add_column("import_task",sa.Column("batch_slot",sa.String(36),nullable=True))
    op.add_column("import_task",sa.Column("retry_of_task_id",sa.BigInteger(),nullable=True))
    for field in ("duplicate_rows","review_rows"):
        op.add_column("import_task",sa.Column(field,sa.Integer(),nullable=False,server_default="0"))
    op.create_foreign_key("fk_import_task_retry_of_task_id_import_task","import_task","import_task",["retry_of_task_id"],["id"])
    op.create_index("ix_import_task_retry_of_task_id","import_task",["retry_of_task_id"])
    op.create_index("ix_import_task_file","import_task",["customer_id","file_hash"])
    op.create_unique_constraint("uq_import_task_batch_slot","import_task",["batch_id","batch_slot"])
    op.add_column("import_task_row",sa.Column("duplicate_order_id",sa.BigInteger(),nullable=True))
    op.add_column("import_task_row",sa.Column("purchase_review",sa.JSON(),nullable=True))
    op.create_foreign_key("fk_import_task_row_duplicate_order_id_sales_order","import_task_row","sales_order",["duplicate_order_id"],["id"])
    op.create_index("ix_import_task_row_duplicate_order_id","import_task_row",["duplicate_order_id"])
    for field in ("purchase_source_digest","purchase_reference_digest","purchase_group_digest"):
        op.add_column("sales_order",sa.Column(field,sa.String(64),nullable=True))
    for field in ("purchase_source_digest","purchase_reference_digest"):
        op.create_unique_constraint("uq_sales_order_"+field,"sales_order",[field])
    op.create_index("ix_sales_order_purchase_group_digest","sales_order",["purchase_group_digest"])

def downgrade():
    for field in ("purchase_source_digest","purchase_reference_digest"):
        op.drop_constraint("uq_sales_order_"+field,"sales_order",type_="unique")
    op.drop_index("ix_sales_order_purchase_group_digest",table_name="sales_order")
    for field in ("purchase_source_digest","purchase_reference_digest","purchase_group_digest"):
        op.drop_column("sales_order",field)
    op.drop_constraint("fk_import_task_row_duplicate_order_id_sales_order","import_task_row",type_="foreignkey")
    op.drop_index("ix_import_task_row_duplicate_order_id",table_name="import_task_row")
    op.drop_column("import_task_row","purchase_review")
    op.drop_column("import_task_row","duplicate_order_id")
    op.drop_constraint("uq_import_task_batch_slot","import_task",type_="unique")
    op.drop_index("ix_import_task_file",table_name="import_task")
    op.drop_index("ix_import_task_retry_of_task_id",table_name="import_task")
    op.drop_constraint("fk_import_task_retry_of_task_id_import_task","import_task",type_="foreignkey")
    for field in ("batch_id","batch_slot","retry_of_task_id","duplicate_rows","review_rows"):
        op.drop_column("import_task",field)
