"""Module 03 only: insert fixed SEED fixtures without updating existing records.

Run from project root: python -m scripts.seed_data
"""
import json
import os
import re
import logging
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.core.database import get_session_factory
from app.models import CustomerConfig, SalesOrder
from app.repositories.customer_configuration import load_configuration, ConfigurationError

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "config" / "historical_seed_demo_v1.json"
PREFIX = "SEED-M03-V1-"

class SeedConflictError(ValueError):
    pass

class SeedOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_no: str = Field(min_length=1, max_length=64)
    customer_id: Literal["A", "B", "C"]
    customer_order_no: str | None = Field(default=None, max_length=128)
    customer_material_code: str | None = Field(default=None, max_length=128)
    customer_batch: str | None = Field(default=None, max_length=128)
    customer_model: str | None = Field(default=None, max_length=128)
    product_code: str = Field(min_length=1, max_length=128)
    product_name: str = Field(min_length=1, max_length=255)
    unit_price: Decimal = Field(gt=0, max_digits=18, decimal_places=4, allow_inf_nan=False)
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3, allow_inf_nan=False)
    status: Literal["VALID", "INVALID", "CREATED"]
    source_type: Literal["SEED"]
    created_at: datetime
    purpose: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_fixture(self):
        if not self.order_no.startswith(PREFIX + self.customer_id + "-"):
            raise ValueError("Order number is outside the managed seed namespace")
        if self.created_at.tzinfo is not None or self.created_at.microsecond:
            raise ValueError("Use fixed naive DATETIME with second precision")
        if not self.product_code.startswith("SIM-") or not self.product_name.startswith("模拟测试产品 "):
            raise ValueError("Product must be explicitly marked as simulated")
        if not self.product_code.strip() or not self.product_name.strip():
            raise ValueError("Blank product fields")
        if self.product_code != self.product_code.strip() or self.product_name != self.product_name.strip():
            raise ValueError("Unnormalized product text")
        return self

    def order_values(self) -> dict:
        values = self.model_dump(exclude={"purpose"})
        values.update(amount=(self.unit_price * self.quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                      updated_at=self.created_at, source_order_id=None, import_task_id=None,
                      source_sheet_name=None, source_row_number=None)
        if abs(values["amount"]) >= Decimal("1e18"):
            raise ValueError("Amount exceeds DECIMAL(20,2)")
        return values

def load_seed_orders(path: Path = MANIFEST_PATH) -> list[SeedOrder]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("dataset_version") != 1:
        raise ValueError("Unsupported seed dataset version")
    orders = TypeAdapter(list[SeedOrder]).validate_python(manifest["orders"])
    if not orders or len({o.order_no for o in orders}) != len(orders):
        raise ValueError("Empty seed dataset or duplicate order numbers")
    return orders

def _validate_seed_orders(orders):
    orders = TypeAdapter(list[SeedOrder]).validate_python([o.model_dump() for o in orders])
    if not orders or len({o.order_no for o in orders}) != len(orders):
        raise ValueError("Empty seed dataset or duplicate order numbers")
    return orders


def seed_orders(session: Session, orders: list[SeedOrder]) -> dict[str, int]:
    """Formal tool entry; the original target restriction is unchanged."""
    orders = _validate_seed_orders(orders)
    if session.scalar(text("SELECT DATABASE()")) != "order_automation":
        raise SeedConflictError("Seed target must be order_automation")
    return _insert_seed_orders(session, orders)


def seed_orders_in_temporary_test_database(session: Session, orders: list[SeedOrder]) -> dict[str, int]:
    """Explicit test entry; never accepts history/demo/test managed databases."""
    if os.environ.get('APP_ENV') != 'test' or os.environ.get('RUN_DB_TESTS') != '1':
        raise SeedConflictError('Temporary seed tests require an explicit test environment')
    name = session.scalar(text('SELECT DATABASE()'))
    if not re.fullmatch(r'order_automation_(?:v2b_test|envcheck)_[a-f0-9]{32}', name or ''):
        raise SeedConflictError('Seed test target must be a disposable test database')
    return _insert_seed_orders(session, _validate_seed_orders(orders))


def _insert_seed_orders(session: Session, orders: list[SeedOrder]) -> dict[str, int]:
    """Shared insert-only algorithm; callers own target checks and transaction."""
    for cid in sorted({o.customer_id for o in orders}):
        customer = session.scalar(select(CustomerConfig).where(CustomerConfig.customer_id == cid).with_for_update())
        if customer is None:
            raise ConfigurationError("Initialize module 02 configuration first")
        config = load_configuration(session, cid)
        for order in orders:
            if order.customer_id == cid:
                # quantity here is historical, independent of purchase expectations.
                config.validate_required_values(order.order_values())
    existing = {o.order_no: o for o in session.scalars(select(SalesOrder).where(SalesOrder.order_no.in_([o.order_no for o in orders])).with_for_update())}
    # Preflight the entire dataset before writing; collision never means ownership.
    values = [(o, o.order_values()) for o in orders]
    for order, data in values:
        if order.order_no in existing:
            stored = existing[order.order_no]
            if any(getattr(stored, field) != value for field, value in data.items()):
                raise SeedConflictError("Existing order differs from fixture; left unchanged: " + order.order_no)
    inserted = 0
    for order, data in values:
        if order.order_no not in existing:
            session.add(SalesOrder(**data))
            session.flush()  # Fixed manifest order establishes reproducible ID tie-break.
            inserted += 1
    return {"inserted": inserted, "unchanged": len(orders) - inserted, "managed_total": len(orders)}

def main() -> int:
    try:
        orders = load_seed_orders()
        with get_session_factory()() as session, session.begin():
            result = seed_orders(session, orders)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        logging.error("Seed rejected; no changes committed: %s", exc)
        return 1
    except SQLAlchemyError:
        logging.error("Seed database error; transaction rolled back. Check connection and constraints.")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
