"""Correct input, then reuse existing rules/row transaction; never accept product or price."""
import hashlib
import json
from copy import deepcopy
from sqlalchemy import select, func
from pydantic import ValidationError
from app.models import ImportTask, ImportTaskRow, ManualCorrection, SalesOrder
from app.api.errors import APIError
from app.repositories import import_repository as imports
from app.repositories.customer_configuration import load_configuration, task_configuration, ConfigurationError
from app.services.data_normalizer import normalize_row
from app.services.import_service import process_task_row
from app.schemas.purchase_row import RawPurchaseRow
from app.schemas.api import RowDetail


def _row(session, task_id, row_id, locked=False):
    query = select(ImportTaskRow).where(ImportTaskRow.task_id == task_id, ImportTaskRow.id == row_id)
    if locked:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(query)
    if row is None:
        raise APIError(404, "ROW_NOT_FOUND", "该任务中不存在此采购明细")
    return row


def _config(session, task):
    try:
        config = task_configuration(session, task)
    except ConfigurationError:
        raise APIError(409, "CONFIG_CHANGED", "原任务配置快照或版本不一致，请核查历史记录") from None
    except ValidationError:
        raise APIError(422, "CONFIG_ERROR", "原任务配置快照不合法") from None
    if (config.customer.config_version, config.rule.rule_version) != (task.config_version, task.rule_version):
        raise APIError(409, "CONFIG_CHANGED", "任务创建后的配置版本已变更，禁止使用不同规则补录")
    return config


def _attempt(attempt):
    return {name: getattr(attempt, name) for name in
            ("id", "revision", "submitted_values", "before_data", "after_data", "result", "note", "actor_label", "created_at")}


def correction_context(session, task_id, row_id):
    task = session.get(ImportTask, task_id)
    if task is None:
        raise APIError(404, "TASK_NOT_FOUND", "导入任务不存在")
    row = _row(session, task_id, row_id)
    config = _config(session, task)
    mapping = config.customer.field_mapping
    attempts = list(session.scalars(select(ManualCorrection).where(ManualCorrection.row_id == row_id)
                                    .order_by(ManualCorrection.revision.desc()).limit(100)))
    values = {target: (row.normalized_data or {}).get(target, row.raw_data.get(column)) for column, target in mapping.items()}
    if attempts:
        values = attempts[0].submitted_values  # Retain the latest invalid submission too.
    values = {k: None if v is None else str(v) for k, v in values.items()}
    return {"row": RowDetail.model_validate(row).model_dump(mode="json"), "revision": row.manual_revision,
            "customer_id": task.customer_id, "values": values,
            "fields": [{"field": target, "label": column, "required": target in config.rule.required_fields}
                       for column, target in mapping.items()],
            "editable": task.status != "PROCESSING" and row.status in {"INVALID_DATA", "NOT_MATCHED", "AMBIGUOUS", "FAILED", "REVIEW_REQUIRED"} and row.created_order_id is None,
            "history": [_attempt(a) for a in attempts], "history_total": session.scalar(select(func.count()).select_from(ManualCorrection).where(ManualCorrection.row_id==row_id))}


def correct_row(session, task_id, row_id, request):
    # Same lock order as module 06 prevents concurrent edit/build racing.
    if session.get(ImportTask, task_id) is None:
        raise APIError(404, "TASK_NOT_FOUND", "导入任务不存在")
    task = imports.lock_task(session, task_id)
    row = _row(session, task_id, row_id, locked=True)
    digest = hashlib.sha256(str(request.request_key).encode()).hexdigest()
    payload = request.model_dump(mode="json", exclude={"request_key"})
    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    old = session.scalar(select(ManualCorrection).where(ManualCorrection.row_id == row.id,
                           ManualCorrection.request_key_digest == digest).with_for_update())
    if old is not None:
        if old.request_hash != payload_hash:
            raise APIError(409, "CORRECTION_KEY_CONFLICT", "同一补录操作标识不能提交不同内容")
        return {"row": RowDetail.model_validate(row), "revision": row.manual_revision, "replayed": True,
                "attempt": _attempt(old)}
    if row.created_order_id is not None or row.status == "SUCCESS":
        raise APIError(409, "ROW_ALREADY_CREATED", "该行已创建销售订单，禁止修改或重复建单")
    existing_order = session.scalar(select(SalesOrder.id).where(SalesOrder.import_task_id == task_id,
                                   SalesOrder.source_sheet_name == row.sheet_name,
                                   SalesOrder.source_row_number == row.row_number).with_for_update())
    if existing_order is not None:
        raise APIError(409, "ROW_ALREADY_CREATED", "该采购位置已有销售订单，关联异常需核查，禁止再次建单")
    if task.status == "PROCESSING":
        raise APIError(409, "TASK_PROCESSING", "任务尚在处理中，请等待原处理结束")
    if row.status not in {"INVALID_DATA", "NOT_MATCHED", "AMBIGUOUS", "FAILED", "REVIEW_REQUIRED"}:
        raise APIError(409, "ROW_NOT_EDITABLE", "该行尚在处理，不能人工补录")
    if row.manual_revision != request.expected_revision:
        raise APIError(409, "REVISION_CONFLICT", "明细已被其他操作修改，请刷新后重新核对")
    config = _config(session, task)
    mapping = config.customer.field_mapping
    if set(request.values) != set(mapping.values()) or not request.note.strip():
        raise APIError(422, "INVALID_CORRECTION", "请提交全部可编辑采购字段及非空修改说明")
    if any(v is not None and len(v) > 1024 for v in request.values.values()):
        raise APIError(422, "INVALID_CORRECTION", "补录字段过长")
    before = {key: deepcopy(getattr(row, key)) for key in
              ("normalized_data", "status", "error_code", "error_message", "matched_order_id", "created_order_id")}
    # Original cells never overwritten; only evaluation values change.
    raw = RawPurchaseRow(sheet_name=row.sheet_name, row_number=row.row_number, raw_data=row.raw_data,
                         raw_cells=(row.normalized_data or {}).get("raw_cells", {}),
                         values={col: request.values[field] for col, field in mapping.items()})
    parsed = normalize_row(config, raw)
    if row.purchase_review:
        row.purchase_review = {**deepcopy(row.purchase_review), "current": None}
    row.duplicate_order_id = None
    row.manual_revision += 1
    if parsed.status != "SUCCESS":
        row.normalized_data = None
        row.status, row.error_code = "INVALID_DATA", "INVALID_INPUT"
        row.error_message = "; ".join(",".join(e.fields) + ": " + e.message for e in parsed.errors)[:2000]
        row.matched_order_id = None
        session.flush()
        imports.refresh_statistics(session, task)
    else:
        row.normalized_data = parsed.data.model_dump(mode="json")
        row.status, row.error_code, row.error_message, row.matched_order_id = "PENDING", None, None, None
        session.flush()
        process_task_row(session, task_id, row.sheet_name, row.row_number)
    task.finished_at = func.current_timestamp()
    attempt = ManualCorrection(row_id=row.id, revision=row.manual_revision, request_key_digest=digest,
                               request_hash=payload_hash, submitted_values=request.values, before_data=before,
                               after_data=deepcopy(row.normalized_data), note=request.note.strip(),
                               result=RowDetail.model_validate(row).model_dump(mode="json"))
    session.add(attempt)
    session.flush()
    return {"row": RowDetail.model_validate(row), "revision": row.manual_revision, "replayed": False,
            "attempt": _attempt(attempt)}
