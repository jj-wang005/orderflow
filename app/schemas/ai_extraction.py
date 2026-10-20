from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictStr

Code = Annotated[StrictStr, Field(min_length=1, max_length=128)]
FieldName = Literal['customer_order_no','customer_material_code','customer_model','customer_batch','quantity']


class ExtractionRow(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    customer_order_no: Code | None
    customer_material_code: Code | None
    customer_model: Code | None
    customer_batch: Code | None
    quantity: Annotated[StrictStr, Field(min_length=1, max_length=32, pattern=r'^\d+(?:\.\d+)?$')] | None
    source_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    source_excerpt: Annotated[StrictStr, Field(max_length=2000)]
    extraction_warnings: Annotated[list[Annotated[StrictStr, Field(max_length=300)]], Field(max_length=20)]
    uncertain_fields: Annotated[list[FieldName], Field(max_length=5)]


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    rows: list[ExtractionRow] = Field(max_length=100)
    warnings: list[Annotated[StrictStr, Field(max_length=300)]] = Field(max_length=30)


class DraftEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)
    request_key: UUID
    result: 'DraftResult'
    note: str = Field(min_length=1, max_length=500)

class DraftRow(ExtractionRow):
    # A human may save incomplete/invalid quantity text; confirmation remains strict.
    quantity: Annotated[StrictStr,Field(max_length=32)] | None

class DraftResult(ExtractionResult):
    rows:list[DraftRow]=Field(max_length=100)
    removed_rows:list[DraftRow]=Field(default_factory=list,max_length=100)

DraftEdit.model_rebuild()


class DraftConfirm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)
    request_key: UUID
    confirmed: Literal[True]
    note: str = Field(min_length=1, max_length=500)
