"""Module 06 transaction workflow. Caller commits/rolls back the outer transaction.

Only this workflow writes AUTO orders: lock task -> lock row -> per-row SAVEPOINT
-> insert order and mark SUCCESS together. No upload/request API is implemented.
"""
import hashlib
import json
import logging
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError, IntegrityError, DataError, DBAPIError
from sqlalchemy.orm import Session
from pydantic import ValidationError
from app.models import CustomerConfig, ImportTask, ImportTaskRow
from app.schemas.purchase_row import NormalizedPurchaseRow
from app.schemas.matching import HistoricalOrderReference
from app.repositories import order_repository as orders
from app.repositories import import_repository as imports
from app.services.excel_parser import parse_purchase_file, ParseLimits, parse_excel
from app.repositories.customer_configuration import load_configuration, task_configuration, ConfigurationError
from app.services.matching_engine import match_purchase_row
from app.services.order_service import build_sales_order, OrderValidationError
from app.services.purchase_guard import check_purchase, PurchaseBlocked, input_key

logger = logging.getLogger(__name__)

class ImportRejected(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)

class FatalImportError(RuntimeError):
    """Outer transaction must be rolled back; do not claim row-local recovery."""

def _source_bytes(source, file_name, limits):
    try:
        if isinstance(source, bytes):
            content, name = source, Path(file_name or "").name
        else:
            path = Path(source)
            name = path.name
            with path.open("rb") as handle:
                content = handle.read(limits.max_size_bytes + 1)
    except (OSError, TypeError):
        raise ImportRejected("INVALID_FILE", "文件无法读取") from None
    if not name or len(name) > 255 or len(content) > limits.max_size_bytes:
        raise ImportRejected("INVALID_FILE", "文件名缺失/过长或文件超过大小限制")
    return content, name

def create_import_task(session: Session, customer_id: str, source: Path | str | bytes, *,
                       file_name: str | None = None, limits: ParseLimits | None = None,
                       idempotency_key_digest: str | None = None,
                       batch_id: str | None = None, batch_slot: str | None = None, retry_of_task_id: int | None = None) -> int:
    """Parse once and persist an immutable row input snapshot, without processing it."""
    limits = limits or ParseLimits()
    content, name = _source_bytes(source, file_name, limits)
    if session.scalar(select(CustomerConfig.id).where(CustomerConfig.customer_id == customer_id)) is None:
        raise ImportRejected("CONFIG_ERROR", "客户不存在，不能创建任务")
    # Serialize configuration capture with management updates; parse with the same immutable value.
    session.scalar(select(CustomerConfig).where(CustomerConfig.customer_id == customer_id).with_for_update().execution_options(populate_existing=True))
    try:
        config = load_configuration(session, customer_id, for_update=True)
    except (ConfigurationError, ValidationError):
        raise ImportRejected("CONFIG_ERROR", "客户尚未确认合法配置或已禁用") from None
    parsed = parse_excel(config, content, file_name=name, limits=limits)
    task = ImportTask(customer_id=customer_id, file_name=name, file_hash=hashlib.sha256(content).hexdigest(),
                      idempotency_key_digest=idempotency_key_digest,
                      config_version=parsed.config_version, rule_version=parsed.rule_version,
                      config_snapshot=config.model_dump(mode="json"),
                      batch_id=batch_id, batch_slot=batch_slot, retry_of_task_id=retry_of_task_id,
                      started_at=func.current_timestamp(), status="PROCESSING")
    session.add(task)
    session.flush()
    if parsed.file_error is not None:
        task.error_message = (parsed.file_error.code + ": " + parsed.file_error.message)[:2000]
        task.status, task.finished_at = "FAILED", func.current_timestamp()
    else:
        for row in parsed.rows:
            message = "; ".join(", ".join(e.fields) + ": " + e.message for e in row.errors)[:2000]
            session.add(ImportTaskRow(task_id=task.id, sheet_name=row.sheet_name, row_number=row.row_number,
                                     raw_data=row.raw_data,
                                     normalized_data=row.data.model_dump(mode="json") if row.data is not None else None,
                                     status="PENDING" if row.status == "SUCCESS" else "INVALID_DATA",
                                     error_code=None if row.status == "SUCCESS" else "INVALID_INPUT",
                                     error_message=None if row.status == "SUCCESS" else message))
    session.flush()
    imports.refresh_statistics(session, task)
    return task.id

def _mark_failure(session, row, status, code, message, reference_id=None):
    # If matching succeeded but order write failed, retain the existing reference
    # as trace only. Lock current existence before assigning its FK.
    row.matched_order_id = reference_id if reference_id is not None and orders.lock_reference(session, reference_id) is not None else None
    row.created_order_id = None
    row.status, row.error_code, row.error_message = status, code, message[:2000]
    session.flush()

def _mysql_errno(exc):
    args = getattr(getattr(exc, "orig", None), "args", ())
    return args[0] if args else None

def _is_fatal(exc, session):
    return (not session.is_active or
            isinstance(exc, DBAPIError) and (exc.connection_invalidated or _mysql_errno(exc) == 1213))

def _process_locked_row(session, task, row):
    if row.status != "PENDING":
        return imports.row_result(session, row)  # Success and other terminal outcomes are immutable/reusable.
    if row.created_order_id is not None:
        raise imports.ImportInvariantError("待处理行已有订单关联，禁止再次创建")
    row_id, reference_id = row.id, None
    position = (row.sheet_name, row.row_number)
    try:
        with session.begin_nested():
            if row.normalized_data is None:
                raise OrderValidationError("INVALID_INPUT", "任务行缺少标准化采购数据")
            try:
                purchase = NormalizedPurchaseRow.model_validate_json(json.dumps(row.normalized_data, ensure_ascii=False))
            except ValidationError:
                raise OrderValidationError("INVALID_INPUT", "任务行的标准化数据不合法") from None
            if (purchase.customer_id != task.customer_id or purchase.sheet_name != row.sheet_name
                or purchase.row_number != row.row_number):
                raise OrderValidationError("INVALID_INPUT", "采购数据与任务来源位置不一致")
            try:
                config = task_configuration(session, task)
            except (ConfigurationError, ValidationError):
                raise OrderValidationError("CONFIG_CHANGED", "原任务配置快照不可用，请核查版本记录") from None
            check_purchase(session, task, row)
            matched = match_purchase_row(session, purchase, config=config)
            if matched.config_version is not None and (matched.config_version != task.config_version or matched.rule_version != task.rule_version):
                raise OrderValidationError("CONFIG_CHANGED", "任务创建后的客户配置/规则版本已变更，请创建新任务")
            if matched.status != "MATCHED":
                status = {"NOT_MATCHED":"NOT_MATCHED", "AMBIGUOUS_MATCH":"AMBIGUOUS",
                          "INVALID_REFERENCE":"INVALID_DATA", "INVALID_INPUT":"INVALID_DATA"}.get(matched.status, "FAILED")
                details = matched.message + ("；字段=" + ",".join(matched.error_fields) if matched.error_fields else "")
                details += "；候选ID=" + str(matched.candidate_order_ids) if matched.candidate_order_ids else ""
                _mark_failure(session, row, status, matched.error_code or matched.status, details)
            else:
                reference = matched.matched_order
                if reference is None:
                    raise OrderValidationError("INVALID_REFERENCE", "匹配结果缺少可靠历史订单")
                reference_id = reference.id
                current = orders.lock_reference(session, reference_id)
                try:
                    locked = HistoricalOrderReference.model_validate(current)
                except ValidationError:
                    raise OrderValidationError("INVALID_REFERENCE", "历史订单当前已不存在或不再合格") from None
                if locked != reference:
                    raise OrderValidationError("REFERENCE_CHANGED", "历史参考在匹配后发生变化，禁止使用过期产品或价格")
                order = build_sales_order(purchase, locked, task.id)
                orders.insert_order(session, order)
                row.matched_order_id, row.created_order_id = locked.id, order.id
                row.status, row.error_code, row.error_message = "SUCCESS", None, None
                session.flush()  # This update and the order insert are in the same SAVEPOINT.
    except PurchaseBlocked as exc:
        row = imports.lock_row(session, task.id, *position)
        if exc.risk is not None:
            review=deepcopy(row.purchase_review or {})
            review['risk']={**exc.risk,'input_digest':input_key(row),'detected_at':datetime.now().isoformat()}
            row.purchase_review=review
        row.status, row.error_code, row.error_message = exc.status, exc.status, exc.message
        row.duplicate_order_id = exc.order_id
        row.matched_order_id, row.created_order_id = None, None
        session.flush()
    except OrderValidationError as exc:
        row = imports.lock_row(session, task.id, *position)
        _mark_failure(session, row, "FAILED" if exc.code == "CONFIG_CHANGED" else "INVALID_DATA",
                      exc.code, exc.message, reference_id)
    except SQLAlchemyError as exc:
        if _is_fatal(exc, session) or not isinstance(exc, (IntegrityError, DataError)):
            raise FatalImportError("数据库事务无法局部恢复，请回滚外层事务并重试") from exc
        row = imports.lock_row(session, task.id, *position)
        code = "DB_UNIQUE_CONFLICT" if isinstance(exc, IntegrityError) and _mysql_errno(exc) == 1062 else "DB_WRITE_FAILED"
        logger.warning("Import row write rejected: task=%s row_id=%s type=%s", task.id, row_id, type(exc).__name__)
        _mark_failure(session, row, "FAILED", code, "数据库写入失败，该行订单与成功状态已回滚", reference_id)
    return imports.row_result(session, row)

def process_task_row(session: Session, task_id: int, sheet_name: str, row_number: int):
    """Idempotent one-row entry. Caller must commit its transaction to release locks."""
    task = imports.lock_task(session, task_id)
    row = imports.lock_row(session, task_id, sheet_name, row_number)
    result = _process_locked_row(session, task, row)
    imports.refresh_statistics(session, task)
    return result

def process_import_task(session: Session, task_id: int):
    """Process/resume existing stored rows; never replace task input with new Excel."""
    task = imports.lock_task(session, task_id)
    positions = session.execute(select(ImportTaskRow.sheet_name, ImportTaskRow.row_number)
                                .where(ImportTaskRow.task_id == task_id).order_by(ImportTaskRow.sheet_name, ImportTaskRow.row_number)).all()
    for sheet_name, row_number in positions:
        row = imports.lock_row(session, task.id, sheet_name, row_number)
        _process_locked_row(session, task, row)
    imports.refresh_statistics(session, task)
    return imports.task_result(session, task)

def import_purchase_file(session: Session, customer_id: str, source: Path | str | bytes, **options):
    """New file -> new task -> processing with source claims and business review."""
    task_id = create_import_task(session, customer_id, source, **options)
    return process_import_task(session, task_id)
