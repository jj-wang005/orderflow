"""Immutable confirmed configuration history; current configuration stays in existing tables."""
from typing import Any
from sqlalchemy import BigInteger, String, Integer, JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, Timestamps

class ConfigurationVersion(Timestamps, Base):
    __tablename__ = "configuration_version"
    __table_args__ = (UniqueConstraint("customer_id", "config_version", name="uq_configuration_version_customer"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(String(64), ForeignKey("customer_config.customer_id"))
    config_version: Mapped[int] = mapped_column(Integer)
    rule_version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
