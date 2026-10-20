from datetime import datetime
from sqlalchemy import BigInteger, String, Integer, JSON, ForeignKey, DateTime
from sqlalchemy.dialects.mysql import MEDIUMBLOB
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, Timestamps


class AIExtraction(Timestamps, Base):
    __tablename__ = 'ai_extraction'
    __table_args__ = {'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_bin'}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(String(64), ForeignKey('customer_config.customer_id'), index=True)
    request_digest: Mapped[str] = mapped_column(String(64), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    source_kind: Mapped[str] = mapped_column(String(16))
    file_name: Mapped[str] = mapped_column(String(255))
    original_content: Mapped[bytes] = mapped_column(MEDIUMBLOB)
    config_snapshot: Mapped[dict] = mapped_column(JSON)
    source_manifest: Mapped[list] = mapped_column(JSON, default=list)
    original_result: Mapped[dict | None] = mapped_column(JSON)
    edited_result: Mapped[dict | None] = mapped_column(JSON)
    audit: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default='PARSING', index=True)
    provider: Mapped[str] = mapped_column(String(16))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(1000))
    confirm_digest: Mapped[str | None] = mapped_column(String(64), unique=True)
    confirm_hash: Mapped[str | None] = mapped_column(String(64))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    import_task_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('import_task.id'), unique=True)
