from typing import Any
from sqlalchemy import BigInteger, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, Timestamps

class OrderChange(Timestamps, Base):
    __tablename__ = "order_change"
    __table_args__ = (UniqueConstraint("target", "request_digest", name="uq_order_change_request"),
                      {"mysql_engine":"InnoDB", "mysql_charset":"utf8mb4", "mysql_collate":"utf8mb4_bin"})
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_order.id"), index=True)
    target: Mapped[str] = mapped_column(String(64))
    request_digest: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    note: Mapped[str] = mapped_column(String(500))
    actor_label: Mapped[str] = mapped_column(String(128), default="本地销管（未鉴权）")
