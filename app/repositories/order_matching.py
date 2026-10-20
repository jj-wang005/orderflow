"""Bounded, parameterized SQLAlchemy candidate queries. No insert/update/delete."""
from sqlalchemy import select, cast, LargeBinary, or_
from sqlalchemy.orm import Session
from app.models import SalesOrder
from app.repositories.customer_configuration import ConfigurationError
from app.schemas.customer_configuration import CustomerConfiguration
from app.schemas.purchase_row import NormalizedPurchaseRow
from app.schemas.matching import HistoricalOrderReference

CUSTOMER_COLUMNS = {name: getattr(SalesOrder, name) for name in
                    ("customer_order_no", "customer_material_code", "customer_batch", "customer_model")}
SORT_COLUMNS = {"created_at": SalesOrder.created_at, "id": SalesOrder.id}
REFERENCE_COLUMNS = [getattr(SalesOrder, name) for name in HistoricalOrderReference.model_fields]

def exact_text(column, value: str):
    # MySQL utf8mb4_bin is case sensitive but PAD SPACE can ignore trailing spaces.
    # Retain equality for index lookup and add exact byte equality for business IDs.
    return (column == value, cast(column, LargeBinary) == value.encode("utf-8"))

def candidate_query(config: CustomerConfiguration, purchase: NormalizedPurchaseRow):
    config = CustomerConfiguration.model_validate(config.model_dump(mode="json"))
    if config.customer.customer_id != purchase.customer_id or not config.customer.enabled or not config.rule.enabled:
        raise ConfigurationError("Configuration must belong to the enabled current customer")
    values = purchase.model_dump()
    config.validate_required_values(values)
    filters = config.rule.filter_conditions
    conditions = [*exact_text(SalesOrder.customer_id, purchase.customer_id),
                  cast(SalesOrder.status, LargeBinary).in_([x.encode() for x in filters.eligible_statuses]),
                  cast(SalesOrder.source_type, LargeBinary).in_([x.encode() for x in filters.allowed_source_types])]
    # Match keys and additional safety validation remain separately defined.
    for fields in (config.rule.match_fields, config.rule.validation_config.consistency_fields):
        for field in fields:
            if field not in CUSTOMER_COLUMNS:
                raise ConfigurationError("Unsupported customer field")
            value = values[field]
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError("Matching and safety fields require normalized nonblank text")
            conditions.extend(exact_text(CUSTOMER_COLUMNS[field], value))
    if filters.historical_quantity is not None:
        quantity = filters.historical_quantity
        comparisons = {"gt": SalesOrder.quantity.__gt__, "gte": SalesOrder.quantity.__ge__, "eq": SalesOrder.quantity.__eq__}
        conditions.append(comparisons[quantity.operator](quantity.value))
    ordering = []
    for item in config.rule.sort_config:
        if item.field not in SORT_COLUMNS or item.direction != "DESC":
            raise ConfigurationError("Unsupported sort field/direction")
        ordering.append(SORT_COLUMNS[item.field].desc())
    return select(*REFERENCE_COLUMNS).where(*conditions).order_by(*ordering)

def latest_candidate(session: Session, query) -> dict | None:
    # Column mappings avoid dirty/stale ORM identity-map values, and return no mutable order.
    row = session.execute(query.limit(1)).mappings().first()
    return dict(row) if row is not None else None

def conflicting_product(session: Session, query, product_code: str) -> dict | None:
    different = or_(SalesOrder.product_code.is_(None),
                    cast(SalesOrder.product_code, LargeBinary) != product_code.encode("utf-8"))
    probe = query.with_only_columns(SalesOrder.id, SalesOrder.product_code).where(different).limit(1)
    row = session.execute(probe).mappings().first()
    return dict(row) if row is not None else None
