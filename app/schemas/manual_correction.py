from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from uuid import UUID
from datetime import datetime
from typing import Any
from app.schemas.api import RowDetail

EditableField = Literal["customer_order_no", "customer_material_code", "customer_batch", "customer_model", "quantity"]


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID = Field(description="本次补录操作 UUID；网络重试必须沿用原值")
    expected_revision: int = Field(ge=0, strict=True)
    values: dict[EditableField, StrictStr | None]
    note: str = Field(min_length=1, max_length=500, description="修改说明；不作为已认证人员身份")


class CorrectionFieldDefinition(BaseModel):
    field: EditableField
    label: str
    required: bool


class CorrectionAudit(BaseModel):
    id: int
    revision: int
    submitted_values: dict[EditableField, str | None]
    before_data: dict[str, Any] | None
    after_data: dict[str, Any] | None
    result: RowDetail
    note: str
    actor_label: str
    created_at: datetime


class CorrectionContext(BaseModel):
    row: RowDetail
    revision: int
    customer_id: str
    values: dict[EditableField, str | None]
    fields: list[CorrectionFieldDefinition]
    editable: bool
    history: list[CorrectionAudit]
    history_total: int


class CorrectionResult(BaseModel):
    row: RowDetail
    revision: int
    replayed: bool
    attempt: CorrectionAudit
