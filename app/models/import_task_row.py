from datetime import datetime
from decimal import Decimal
from typing import Any
from sqlalchemy import BigInteger, String, Integer, Boolean, JSON, Numeric, DateTime, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, Timestamps

class ImportTaskRow(Timestamps, Base):
    __tablename__ = "import_task_row"
    __table_args__ = (UniqueConstraint("task_id", "sheet_name", "row_number", name="uq_import_task_row_position"),
                      Index("ix_import_task_row_task_status", "task_id", "status"), {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"})
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("import_task.id"))
    sheet_name: Mapped[str] = mapped_column(String(128))
    row_number: Mapped[int] = mapped_column(Integer)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    normalized_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), server_default="PENDING")
    matched_order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sales_order.id"), index=True)
    created_order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sales_order.id"), index=True)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(2000))
    duplicate_order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sales_order.id"), index=True)
    purchase_review: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    manual_revision: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
