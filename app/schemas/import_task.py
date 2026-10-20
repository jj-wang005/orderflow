"""Module 06 service results; persistence is controlled by the caller's transaction."""
from collections import Counter
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

RowStatus = Literal["PENDING", "SUCCESS", "NOT_MATCHED", "AMBIGUOUS", "INVALID_DATA", "FAILED", "DUPLICATE", "REVIEW_REQUIRED"]
TaskStatus = Literal["PROCESSING", "SUCCESS", "PARTIAL_SUCCESS", "FAILED"]

class CreatedOrder(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    id: int
    order_no: str
    customer_id: str
    customer_order_no: str | None
    customer_material_code: str | None
    customer_batch: str | None
    customer_model: str | None
    product_code: str
    product_name: str
    unit_price: Decimal
    quantity: Decimal
    amount: Decimal
    status: Literal["CREATED"]
    source_type: Literal["AUTO"]
    source_order_id: int | None
    import_task_id: int
    source_sheet_name: str
    source_row_number: int

class ImportRowResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_id: int
    sheet_name: str
    row_number: int
    raw_data: dict[str, Any]
    normalized_data: dict[str, Any] | None
    status: RowStatus
    matched_order_id: int | None
    created_order_id: int | None
    error_code: str | None
    error_message: str | None
    created_order: CreatedOrder | None = None

class ImportTaskResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_id: str
    file_name: str
    file_hash: str
    status: TaskStatus
    total_rows: int
    success_rows: int
    unmatched_rows: int
    failed_rows: int
    duplicate_rows: int = 0
    review_rows: int = 0
    pending_rows: int
    config_version: int | None
    rule_version: int | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    batch_id: str | None = None
    retry_of_task_id: int | None = None
    created_at: datetime | None = None
    rows: list[ImportRowResult] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_counts(self):
        if self.total_rows != self.success_rows + self.unmatched_rows + self.failed_rows + self.pending_rows + self.duplicate_rows + self.review_rows:
            raise ValueError("Import task counts are inconsistent")
        if len(self.rows) != self.total_rows:
            raise ValueError("Task details must agree with total count")
        counts = Counter(row.status for row in self.rows)
        if (self.success_rows != counts["SUCCESS"] or self.unmatched_rows != counts["NOT_MATCHED"]
            or self.failed_rows != counts["AMBIGUOUS"] + counts["INVALID_DATA"] + counts["FAILED"]
            or self.duplicate_rows != counts["DUPLICATE"] or self.review_rows != counts["REVIEW_REQUIRED"]
            or self.pending_rows != counts["PENDING"]):
            raise ValueError("Task counters must match actual row outcomes")
        return self
