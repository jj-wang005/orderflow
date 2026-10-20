"""Read-only module 05 results: validated snapshots, never mutable ORM orders."""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MatchStatus = Literal["MATCHED", "NOT_MATCHED", "AMBIGUOUS_MATCH", "INVALID_REFERENCE", "INVALID_INPUT", "CONFIG_ERROR", "MATCH_ERROR"]

class HistoricalOrderReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: int = Field(gt=0)
    order_no: str = Field(min_length=1, max_length=64)
    customer_id: str = Field(min_length=1, max_length=64)
    customer_order_no: str | None
    customer_material_code: str | None
    customer_batch: str | None
    customer_model: str | None
    product_code: str = Field(min_length=1, max_length=128)
    product_name: str = Field(min_length=1, max_length=255)
    unit_price: Decimal = Field(strict=True, gt=0, max_digits=18, decimal_places=4, allow_inf_nan=False)
    quantity: Decimal = Field(strict=True, gt=0, max_digits=18, decimal_places=3, allow_inf_nan=False)
    amount: Decimal = Field(strict=True, ge=0, max_digits=20, decimal_places=2, allow_inf_nan=False)
    status: Literal['VALID','PROCESSING','SHIPPED','COMPLETED']
    source_type: Literal['SEED','AUTO']
    created_at: datetime

    @field_validator("order_no", "customer_id", "product_code")
    @classmethod
    def clean_identifier(cls, value):
        if not value.strip() or value != value.strip():
            raise ValueError("Reference identifiers must be nonblank and normalized")
        return value

    @field_validator("product_name")
    @classmethod
    def nonblank_product(cls, value):
        if not value.strip():
            raise ValueError("Product name is blank")
        return value

    @field_validator("source_type")
    @classmethod
    def valid_source(cls, value):
        if not value.strip():
            raise ValueError("Historical source is blank")
        return value

    @model_validator(mode="after")
    def correct_amount(self):
        with localcontext() as context:
            context.prec = 50
            expected = (self.unit_price * self.quantity).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if self.amount != expected:
            raise ValueError('Historical amount does not equal price times quantity')
        return self

class MatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: MatchStatus
    customer_id: str | None
    sheet_name: str | None
    row_number: int | None
    config_version: int | None = None
    rule_version: int | None = None
    matched_order: HistoricalOrderReference | None = None
    error_code: str | None = None
    message: str
    error_fields: list[str] = Field(default_factory=list)
    # Diagnostic only: an invalid/ambiguous candidate is never a matched reference.
    candidate_order_ids: list[int] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def consistent_result(self):
        if self.status == "MATCHED":
            if self.matched_order is None or self.error_code is not None:
                raise ValueError("MATCHED requires a reliable reference without an error")
        elif self.matched_order is not None or self.error_code != self.status:
            raise ValueError("Failure must expose only an error, never a usable reference")
        return self
