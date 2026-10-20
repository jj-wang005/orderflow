from datetime import datetime
from decimal import Decimal
from typing import Any
from sqlalchemy import BigInteger, String, Integer, Boolean, JSON, Numeric, DateTime, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, Timestamps

class MatchRule(Timestamps, Base):
    __tablename__ = "match_rule"
    __table_args__ = (Index("ix_match_rule_customer_enabled", "customer_id", "enabled"), {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"})
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(String(64), ForeignKey("customer_config.customer_id"))
    match_fields: Mapped[list[str]] = mapped_column(JSON)
    filter_conditions: Mapped[dict[str, Any]] = mapped_column(JSON)
    sort_config: Mapped[list[Any]] = mapped_column(JSON)
    validation_config: Mapped[dict[str, Any]] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("1"))
    rule_version: Mapped[int] = mapped_column(Integer, server_default="1")
