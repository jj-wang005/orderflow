from typing import Literal
from uuid import UUID
from pydantic import Field, model_validator
from app.schemas.customer_configuration import ConfigModel

class PurchaseReviewRequest(ConfigModel):
    request_key: UUID
    expected_revision: int = Field(ge=0,strict=True)
    decision: Literal["NEW","EXISTING"]
    note: str = Field(min_length=5,max_length=500)
    purchase_reference: str | None = Field(default=None,min_length=8,max_length=128)
    existing_order_id: int | None = Field(default=None,gt=0)

    @model_validator(mode="after")
    def decision_fields(self):
        if self.decision=="NEW" and self.existing_order_id:
            raise ValueError("New procurement cannot select an existing order")
        if self.decision=="EXISTING" and (not self.existing_order_id or self.purchase_reference):
            raise ValueError("Existing procurement requires an existing order only")
        return self
