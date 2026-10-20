from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, StrictStr, StrictBool
from typing import Any
from app.schemas.api import OrderDetail, RowDetail

class ProductInput(BaseModel):
    model_config=ConfigDict(extra="forbid",str_strip_whitespace=True)
    product_code: str=Field(min_length=1,max_length=128)
    product_name: str=Field(min_length=1,max_length=255)
    unit_price: Decimal=Field(gt=0,max_digits=18,decimal_places=4,allow_inf_nan=False)
    @field_validator("unit_price",mode="before")
    @classmethod
    def decimal_text(cls,value):
        if not isinstance(value,str):raise ValueError("单价须使用十进制字符串")
        if len(value)>128:raise ValueError("单价文本过长")
        return value

class ManualOrderRequest(BaseModel):
    model_config=ConfigDict(extra="forbid",str_strip_whitespace=True)
    request_key: UUID
    expected_revision: int=Field(ge=0,strict=True)
    product: ProductInput
    note: str=Field(min_length=1,max_length=500)
    verification_basis: str | None=Field(default=None,max_length=1000,description="产品与价格核对依据（选填）")
    # Retain legacy bodies/UUID retries exactly; this does not control provenance.
    simulation_confirmed: StrictBool | None=Field(default=None,deprecated=True,
        description="兼容旧客户端的可选确认参数；不控制数据来源或表示 OMS 验证")

class EditOrderRequest(ManualOrderRequest):
    values: dict[Literal["customer_order_no","customer_material_code","customer_model","customer_batch","quantity"],StrictStr|None]

    @field_validator("values")
    @classmethod
    def bounded_values(cls,values):
        if any(v is not None and len(v)>128 for v in values.values()):
            raise ValueError("客户字段或数量文本过长")
        return values

class OrderOperationResult(BaseModel):
    order: OrderDetail
    row: RowDetail | None = None
    replayed: bool

class OrderEditContext(BaseModel):
    order: OrderDetail
    editable: bool
    historical_unit_price: Decimal | None = None
    history: list[dict[str,Any]]
