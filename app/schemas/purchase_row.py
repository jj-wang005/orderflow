"""Module 04 output contract. SUCCESS means parsing only, not matching/order creation."""
from decimal import Decimal
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class ParseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class ParseIssue(ParseModel):
    code: Literal["INVALID_FILE", "MISSING_COLUMN", "INVALID_INPUT", "CONFIG_ERROR"]
    message: str
    fields: list[str] = Field(default_factory=list)

class RawPurchaseRow(ParseModel):
    sheet_name: str
    row_number: int = Field(ge=1)
    raw_data: dict[str, Any]
    raw_cells: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # Evaluation values are separate from originals (formula text stays in raw_data).
    values: dict[str, Any]
    cell_errors: dict[str, str] = Field(default_factory=dict)

class NormalizedPurchaseRow(ParseModel):
    customer_id: str = Field(min_length=1, max_length=64)
    customer_order_no: str | None = Field(default=None, min_length=1, max_length=128)
    customer_material_code: str | None = Field(default=None, min_length=1, max_length=128)
    customer_batch: str | None = Field(default=None, min_length=1, max_length=128)
    customer_model: str | None = Field(default=None, min_length=1, max_length=128)
    quantity: Decimal = Field(strict=True, gt=0, max_digits=18, decimal_places=3, allow_inf_nan=False)
    raw_data: dict[str, Any]
    raw_cells: dict[str, dict[str, Any]] = Field(default_factory=dict)
    sheet_name: str
    row_number: int = Field(ge=1)

    @model_validator(mode="after")
    def whole_units(self):
        if self.quantity != self.quantity.to_integral_value():
            raise ValueError("采购数量必须为正整数件数")
        return self

class RowParseResult(ParseModel):
    status: Literal["SUCCESS", "INVALID_INPUT"]
    sheet_name: str
    row_number: int
    raw_data: dict[str, Any]
    raw_cells: dict[str, dict[str, Any]] = Field(default_factory=dict)
    data: NormalizedPurchaseRow | None = None
    errors: list[ParseIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_status(self):
        if (self.status == "SUCCESS") != (self.data is not None and not self.errors):
            raise ValueError("Row status must agree with data and errors")
        return self

class FileParseResult(ParseModel):
    customer_id: str
    file_name: str
    config_version: int | None = None
    rule_version: int | None = None
    rows: list[RowParseResult] = Field(default_factory=list)
    file_error: ParseIssue | None = None

    @property
    def success_rows(self) -> int:
        return sum(row.status == "SUCCESS" for row in self.rows)

    @property
    def invalid_rows(self) -> int:
        return sum(row.status == "INVALID_INPUT" for row in self.rows)
