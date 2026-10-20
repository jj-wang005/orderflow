from datetime import datetime
from decimal import Decimal
from typing import Any
from sqlalchemy import BigInteger, String, Integer, Boolean, JSON, Numeric, DateTime, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, Timestamps

class SalesOrder(Timestamps, Base):
    __tablename__ = "sales_order"
    __table_args__ = (
        UniqueConstraint("import_task_id", "source_sheet_name", "source_row_number", name="uq_sales_order_purchase_position"),
        Index("ix_sales_order_match_a", "customer_id", "customer_order_no", "status", "created_at", "id"),
        Index("ix_sales_order_match_b", "customer_id", "customer_order_no", "customer_model", "status", "created_at", "id"),
        Index("ix_sales_order_match_c", "customer_id", "customer_batch", "customer_material_code", "status", "created_at", "id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"},
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(64), unique=True)
    customer_id: Mapped[str] = mapped_column(String(64), ForeignKey("customer_config.customer_id"))
    customer_order_no: Mapped[str | None] = mapped_column(String(128))
    customer_material_code: Mapped[str | None] = mapped_column(String(128))
    customer_batch: Mapped[str | None] = mapped_column(String(128))
    customer_model: Mapped[str | None] = mapped_column(String(128))
    product_code: Mapped[str] = mapped_column(String(128))
    product_name: Mapped[str] = mapped_column(String(255))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    status: Mapped[str] = mapped_column(String(32), server_default="CREATED")
    source_type: Mapped[str] = mapped_column(String(16))
    source_order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sales_order.id"), index=True)
    import_task_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("import_task.id"), index=True)
    source_sheet_name: Mapped[str | None] = mapped_column(String(128))
    source_row_number: Mapped[int | None] = mapped_column(Integer)
    purchase_source_digest: Mapped[str | None] = mapped_column(String(64), unique=True)
    purchase_reference_digest: Mapped[str | None] = mapped_column(String(64), unique=True)
    purchase_group_digest: Mapped[str | None] = mapped_column(String(64), index=True)
    edit_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_simulated_manual: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
