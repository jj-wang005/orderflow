"""HTTP DTOs and one shared Chinese presentation layer; business codes stay unchanged."""
from datetime import datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar
from pydantic import BaseModel, ConfigDict, Field, computed_field
from app.schemas.import_task import TaskStatus, RowStatus
from app.schemas.order_status import STATUS_LABELS

ROW_LABELS = {
    "DUPLICATE": "已关联已有采购订单，未重复建单", "REVIEW_REQUIRED": "采购唯一性待人工核实",
    "PENDING": "待处理", "MATCHED": "历史订单匹配成功", "SUCCESS": "建单成功",
    "NOT_MATCHED": "已解析 · 待人工补全",
    "AMBIGUOUS": "找到多个不同产品，需人工确认",
    "AMBIGUOUS_MATCH": "找到多个不同产品，需人工确认",
    "INVALID_REFERENCE": "历史参考数据无效，无法自动建单",
    "INVALID_DATA": "采购数据无效，无法自动建单", "FAILED": "处理失败",
}
TASK_LABELS = {"PROCESSING": "处理中", "SUCCESS": "任务处理完成",
               "PARTIAL_SUCCESS": "部分建单成功", "FAILED": "未成功建单"}

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    code: str = "OK"
    message: str = "查询成功"
    data: T


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str


class TaskSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    task_id: int = Field(validation_alias="id", description="导入任务 ID")
    customer_id: str
    file_name: str
    file_hash: str
    status: TaskStatus
    total_rows: int
    success_rows: int
    unmatched_rows: int
    failed_rows: int
    config_version: int | None
    rule_version: int | None
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    duplicate_rows: int = 0
    review_rows: int = 0
    batch_id: str | None = None
    retry_of_task_id: int | None = None
    created_at: datetime | None = None
    idempotency_replayed: bool = False

    @computed_field
    @property
    def status_description(self) -> str:
        if self.status=='FAILED':
            if self.error_message and self.total_rows==0:return '文件解析失败'
            if self.total_rows and self.unmatched_rows==self.total_rows:return '已解析 · 待人工补全'
            if self.total_rows and self.review_rows==self.total_rows:return '已解析 · 采购待核实'
            if self.total_rows and self.duplicate_rows==self.total_rows:return '已关联已有采购'
            return '待处理异常，请查看明细'
        return TASK_LABELS[self.status]

    @computed_field
    @property
    def pending_rows(self) -> int:
        return self.total_rows - self.success_rows - self.unmatched_rows - self.failed_rows - self.duplicate_rows - self.review_rows

    @computed_field
    @property
    def error_summary(self) -> str | None:
        return self.error_message or (f"未匹配 {self.unmatched_rows} 行；其他失败 {self.failed_rows} 行；待核实 {self.review_rows} 行，请查询明细原因"
                                     if self.unmatched_rows or self.failed_rows or self.review_rows else None)


class PurchaseView(BaseModel):
    customer_id: str
    customer_order_no: str | None = None
    customer_material_code: str | None = None
    customer_batch: str | None = None
    customer_model: str | None = None
    quantity: Decimal = Field(description="本次客户采购数量，不是历史数量")
    sheet_name: str
    row_number: int
    raw_data: dict[str, Any]
    raw_cells: dict[str, Any] = Field(default_factory=dict)


class RowDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_id: int
    sheet_name: str
    row_number: int
    raw_data: dict[str, Any]
    normalized_data: PurchaseView | None
    status: RowStatus
    matched_order_id: int | None
    created_order_id: int | None
    error_code: str | None
    error_message: str | None
    duplicate_order_id: int | None = None
    purchase_review: dict[str, Any] | None = None
    manual_revision: int = 0

    @computed_field
    @property
    def status_description(self) -> str:
        return ROW_LABELS.get(self.error_code or "", ROW_LABELS[self.status])


class Page(BaseModel, Generic[T]):
    total: int
    page: int
    page_size: int
    items: list[T]


class OrderDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
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
    status: str
    source_type: str
    source_order_id: int | None
    import_task_id: int | None
    source_sheet_name: str | None
    source_row_number: int | None
    created_at: datetime
    is_simulated_reference: bool = False
    edit_revision: int = 0
    is_simulated_manual: bool = False

    @computed_field
    @property
    def creation_description(self) -> str:
        if self.source_type=='SEED':return '模拟历史参考'
        if self.is_simulated_manual:return '人工补全建单'
        if self.source_order_id is not None:return '历史匹配建单'
        return '来源待核实'

    @computed_field
    @property
    def status_description(self) -> str:
        return STATUS_LABELS.get(self.status,self.status)

    @computed_field
    @property
    def price_description(self) -> str:
        if self.is_simulated_manual:
            return "销管手动录入的模拟产品与价格，未经过真实 OMS/ERP 验证"
        return ("产品源自模拟历史参考；当前单价可能经人工调整，请核实变更记录，未经过真实 OMS/ERP 验证" if self.is_simulated_reference
                else "产品来自历史参考；当前单价可能经人工调整，请核实价格及变更记录；未经过真实 OMS/ERP 验证")
