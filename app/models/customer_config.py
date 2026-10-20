from datetime import datetime
from decimal import Decimal
from typing import Any
from sqlalchemy import BigInteger, String, Integer, Boolean, JSON, Numeric, DateTime, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, Timestamps

class CustomerConfig(Timestamps, Base):
    __tablename__ = "customer_config"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(String(64), unique=True)
    customer_name: Mapped[str] = mapped_column(String(255))
    sheet_name: Mapped[str] = mapped_column(String(128), server_default="Sheet1")
    header_row: Mapped[int] = mapped_column(Integer, server_default="1")
    field_mapping: Mapped[dict[str, Any]] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("1"))
    config_version: Mapped[int] = mapped_column(Integer, server_default="1")
