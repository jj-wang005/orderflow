"""Append-only input corrections; original Excel remains on import_task_row."""
from typing import Any
from sqlalchemy import BigInteger, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, Timestamps


class ManualCorrection(Timestamps, Base):
    __tablename__ = "manual_correction"
    __table_args__ = (
        UniqueConstraint("row_id", "request_key_digest", name="uq_manual_correction_request"),
        UniqueConstraint("row_id", "revision", name="uq_manual_correction_revision"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"},
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    row_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("import_task_row.id"))
    revision: Mapped[int] = mapped_column(Integer)
    request_key_digest: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    submitted_values: Mapped[dict[str, Any]] = mapped_column(JSON)
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    note: Mapped[str] = mapped_column(String(500))
    actor_label: Mapped[str] = mapped_column(String(128), default="本地销管（未鉴权）")
