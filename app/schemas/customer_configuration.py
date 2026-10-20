"""Validated configuration contract; no SQL expressions or matching execution."""
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.schemas.order_status import HISTORICAL_STATUSES, HISTORICAL_SOURCES

CustomerField = Literal["customer_order_no", "customer_material_code", "customer_batch", "customer_model"]
MappedField = Literal["customer_order_no", "customer_material_code", "customer_batch", "customer_model", "quantity"]

class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class CustomerDefinition(ConfigModel):
    customer_id: str = Field(min_length=1, max_length=64)
    customer_name: str = Field(min_length=1, max_length=255)
    sheet_name: str = Field(min_length=1, max_length=128)
    header_row: int = Field(ge=1, strict=True)
    field_mapping: dict[str, MappedField] = Field(min_length=1)
    enabled: bool = True
    config_version: int = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def unique_targets(self):
        if any(not key.strip() or key != key.strip() for key in self.field_mapping):
            raise ValueError("Mapping column names must be nonblank and trimmed")
        if len(set(self.field_mapping.values())) != len(self.field_mapping):
            raise ValueError("Multiple columns cannot map to the same standard field")
        return self

class QuantityFilter(ConfigModel):
    operator: Literal["gt", "gte", "eq"]
    value: Decimal = Field(ge=0, max_digits=18, decimal_places=0, allow_inf_nan=False)

class FilterConditions(ConfigModel):
    customer_scope: Literal["same_customer"]
    eligible_statuses: list[Literal['VALID','PROCESSING','SHIPPED','COMPLETED']]
    allowed_source_types: list[Literal['SEED','AUTO']]
    historical_quantity: QuantityFilter | None = None

    @model_validator(mode='before')
    @classmethod
    def legacy_policy(cls,value):
        if isinstance(value,dict) and ('status' in value or 'excluded_source_types' in value):
            if (value.get('status')!='VALID' or value.get('excluded_source_types')!=['AUTO'] or
                'eligible_statuses' in value or 'allowed_source_types' in value):
                raise ValueError('Conflicting or invalid legacy history policy')
            value={**value};value.pop('status');value.pop('excluded_source_types')
            value.update(eligible_statuses=HISTORICAL_STATUSES.copy(),allowed_source_types=HISTORICAL_SOURCES.copy())
        return value

    @model_validator(mode="after")
    def confirmed_policy(self):
        if self.eligible_statuses!=HISTORICAL_STATUSES or self.allowed_source_types!=HISTORICAL_SOURCES:
            raise ValueError('Confirmed history policy must include both sources and all four eligible states')
        return self

class SortItem(ConfigModel):
    field: Literal["created_at", "id"]
    direction: Literal["DESC"]

class ValidationConfig(ConfigModel):
    extra_required_fields: list[CustomerField] = Field(default_factory=list)
    consistency_fields: list[CustomerField] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_fields(self):
        for fields in (self.extra_required_fields, self.consistency_fields):
            if len(fields) != len(set(fields)):
                raise ValueError("Duplicate validation fields")
        if not set(self.consistency_fields) <= set(self.extra_required_fields):
            raise ValueError("Consistency fields must be required safety fields")
        return self

class RuleDefinition(ConfigModel):
    match_fields: list[CustomerField] = Field(min_length=1)
    filter_conditions: FilterConditions
    sort_config: list[SortItem]
    validation_config: ValidationConfig
    enabled: bool = True
    rule_version: int = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def validate_rule(self):
        if len(self.match_fields) != len(set(self.match_fields)):
            raise ValueError("Duplicate matching fields")
        if [(s.field, s.direction) for s in self.sort_config] != [("created_at", "DESC"), ("id", "DESC")]:
            raise ValueError("Sorting must be created_at DESC, id DESC")
        if set(self.match_fields) & set(self.validation_config.extra_required_fields):
            raise ValueError("Do not duplicate matching fields in extra required fields")
        return self

    @property
    def required_fields(self) -> list[str]:
        return list(dict.fromkeys([*self.match_fields, "quantity", *self.validation_config.extra_required_fields]))

class SourceColumn(BaseModel):
    """Original header and physical position; never infer absent Excel columns."""
    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=1, le=256, strict=True)
    name: str | None = Field(default=None, max_length=32767)

class CustomerConfiguration(ConfigModel):
    customer: CustomerDefinition
    rule: RuleDefinition
    source_columns: list[SourceColumn] = Field(default_factory=list, max_length=256)

    @model_validator(mode="after")
    def mapping_covers_required(self):
        if not set(self.rule.required_fields) <= set(self.customer.field_mapping.values()):
            raise ValueError("Mapping does not cover all effective required fields")
        if self.source_columns:
            if [c.index for c in self.source_columns] != list(range(1, len(self.source_columns)+1)):
                raise ValueError("Source column positions must be contiguous and ordered")
            names = [c.name for c in self.source_columns]
            if any(names.count(name) != 1 for name in self.customer.field_mapping):
                raise ValueError("Mapped header must occur exactly once in original columns")
        return self

    def validate_required_values(self, values: dict) -> None:
        # Configuration-level requiredness only; normalization belongs to module 04.
        missing = [field for field in self.rule.required_fields
                   if values.get(field) is None or isinstance(values.get(field), str) and not values[field].strip()]
        if missing:
            raise ValueError("Missing required fields: " + ", ".join(missing))
