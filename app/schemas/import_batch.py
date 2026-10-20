from uuid import UUID
from pydantic import Field
from app.schemas.customer_configuration import ConfigModel

class BatchItem(ConfigModel):
    slot_id: UUID
    customer_id: str = Field(min_length=1,max_length=64)
    request_key: str = Field(min_length=1,max_length=128,pattern=r"^[!-~]+$")
    duplicate_confirmed: bool = False
    retry_of_task_id: int | None = Field(default=None,gt=0)
