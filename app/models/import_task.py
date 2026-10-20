from datetime import datetime
from decimal import Decimal
from typing import Any
from sqlalchemy import BigInteger, String, Integer, Boolean, JSON, Numeric, DateTime, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, Timestamps

class ImportTask(Timestamps, Base):
    __tablename__ = "import_task"
    __table_args__ = (Index("ix_import_task_customer_status", "customer_id", "status"), Index("ix_import_task_file", "customer_id", "file_hash"), UniqueConstraint("batch_id", "batch_slot", name="uq_import_task_batch_slot"), {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"})
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(String(64), ForeignKey("customer_config.customer_id"))
    file_name: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key_digest: Mapped[str | None] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(32), server_default="PROCESSING")
    total_rows: Mapped[int] = mapped_column(Integer, server_default="0")
    success_rows: Mapped[int] = mapped_column(Integer, server_default="0")
    unmatched_rows: Mapped[int] = mapped_column(Integer, server_default="0")
    failed_rows: Mapped[int] = mapped_column(Integer, server_default="0")
    error_message: Mapped[str | None] = mapped_column(String(2000))
    config_version: Mapped[int | None] = mapped_column(Integer)
    rule_version: Mapped[int | None] = mapped_column(Integer)
    config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(36))
    batch_slot: Mapped[str | None] = mapped_column(String(36))
    retry_of_task_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("import_task.id"), index=True)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    review_rows: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
