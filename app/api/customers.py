from typing import Annotated
from fastapi import APIRouter, Depends, UploadFile, File, Form
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.config import get_settings
from app.schemas.api import Envelope
from app.schemas.customer_management import CustomerCreate, CustomerEdit, ConfigurationSave
from app.services import customer_management as management
from app.services.customer_preview import parse_draft, trial_match
from app.api.errors import APIError

router = APIRouter(prefix="/api/v1", tags=["客户配置管理"])
DB = Annotated[Session, Depends(get_db)]

def sample_bytes(file):
    try:
        content = file.file.read(get_settings().upload_max_bytes + 1)
        if len(content) > get_settings().upload_max_bytes:
            raise APIError(413, "FILE_TOO_LARGE", "样例超过允许大小")
        return content
    finally:
        file.file.close()

@router.get("/customers")
def customers(session: DB):
    return Envelope(data=management.list_customers(session))

@router.post("/customers", status_code=201)
def create_customer(body: CustomerCreate, session: DB):
    with session.begin():
        result = management.create_customer(session, body)
    return Envelope(data=result, message="客户已新增，请确认字段映射和匹配规则")

@router.post("/customers/field-mapping/preview")
def mapping_preview(file: Annotated[UploadFile, File()], sheet_name: Annotated[str | None, Form(max_length=128)] = None,
                    header_row: Annotated[int, Form(ge=1, le=10000)] = 1):
    return Envelope(data=management.suggest_mapping(sample_bytes(file), file.filename or "", sheet_name, header_row))

@router.get("/match-rule-templates")
def rule_templates():
    return Envelope(data={"fields": management.FIELD_LABELS, "templates": management.TEMPLATES})

@router.get("/customers/{customer_id}")
@router.get("/customers/{customer_id}/config")
def customer_detail(customer_id: str, session: DB):
    return Envelope(data=management.detail(session, customer_id))

@router.patch("/customers/{customer_id}")
def edit_customer(customer_id: str, body: CustomerEdit, session: DB):
    with session.begin():
        result = management.edit_customer(session, customer_id, body)
    return Envelope(data=result, message="客户信息已保存")

@router.post("/customers/{customer_id}/config")
@router.put("/customers/{customer_id}/config")
def save_configuration(customer_id: str, body: ConfigurationSave, session: DB):
    with session.begin():
        result = management.save_configuration(session, customer_id, body)
    return Envelope(data=result, message="正式配置已保存，仅新导入任务使用此版本")

@router.post("/customers/{customer_id}/config/preview")
def configured_preview(customer_id: str, session: DB, configuration: Annotated[str, Form()], file: Annotated[UploadFile, File()]):
    content = sample_bytes(file)
    _, result = parse_draft(session, customer_id, configuration, content, file.filename or "")
    return Envelope(data={"file_error": result.file_error, "total_rows": len(result.rows),
                          "valid_rows": result.success_rows, "invalid_rows": result.invalid_rows,
                          "rows": result.rows[:20]})


@router.post("/customers/{customer_id}/config/trial-match")
def historical_trial(customer_id: str, session: DB, configuration: Annotated[str, Form()], file: Annotated[UploadFile, File()]):
    config, parsed = parse_draft(session, customer_id, configuration, sample_bytes(file), file.filename or "")
    return Envelope(data=trial_match(session, config, parsed),
                    message="仅反映本次查询的历史数据；正式导入仍重新匹配并执行采购防重及建单校验")
