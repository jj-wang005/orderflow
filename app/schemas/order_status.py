from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

OrderStatus=Literal['CREATED','VALID','PROCESSING','SHIPPED','COMPLETED']
HISTORICAL_STATUSES=['VALID','PROCESSING','SHIPPED','COMPLETED']
HISTORICAL_SOURCES=['SEED','AUTO']
STATUS_LABELS={'CREATED':'已创建','VALID':'已确认','PROCESSING':'进行中','SHIPPED':'已发货','COMPLETED':'已完成','INVALID':'无效历史订单'}
STATUS_LABELS['VOIDED']='已作废'
NEXT_STATUS={'CREATED':'VALID','VALID':'PROCESSING','PROCESSING':'SHIPPED','SHIPPED':'COMPLETED'}

class StatusRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    request_key: UUID=Field(description='本次操作 UUID；网络重试沿用原键及请求内容')
    expected_revision: int=Field(ge=0,strict=True,description='当前订单 edit_revision，避免覆盖其他修改')
    status: OrderStatus=Field(description='目标正常状态，只允许当前状态或下一步')
    note: str=Field(default='通过订单管理更新状态',min_length=1,max_length=500,description='操作说明，可沿用默认值')

class VoidRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    request_key: UUID
    expected_revision: int=Field(ge=0,strict=True)
    confirmed: Literal[True]
    note: str=Field(min_length=1,max_length=500)
