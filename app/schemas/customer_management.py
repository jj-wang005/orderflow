from pydantic import Field
from app.schemas.customer_configuration import ConfigModel, MappedField, CustomerField, QuantityFilter, SourceColumn

class CustomerCreate(ConfigModel):
    customer_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    customer_name: str = Field(min_length=1, max_length=255)

class ConfigurationSave(ConfigModel):
    expected_version: int = Field(ge=0, strict=True)
    customer_name: str = Field(min_length=1, max_length=255)
    sheet_name: str = Field(min_length=1, max_length=128)
    header_row: int = Field(ge=1, le=10000, strict=True)
    field_mapping: dict[str, MappedField] = Field(min_length=1, max_length=256)
    match_fields: list[CustomerField] = Field(min_length=1, max_length=4)
    historical_quantity: QuantityFilter | None = None
    enabled: bool = True
    source_columns: list[SourceColumn] | None = Field(default=None, max_length=256)

class CustomerEdit(ConfigModel):
    expected_version: int = Field(ge=0, strict=True)
    customer_name: str = Field(min_length=1, max_length=255)
