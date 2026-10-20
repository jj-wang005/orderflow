"""Request-level deduplication around the unchanged module 06 workflow.

Caller owns the outer transaction: key binding, rows and AUTO orders commit atomically.
Never commit a PROCESSING reservation separately from the final business outcome.
"""
import hashlib
from sqlalchemy import select, case
from sqlalchemy.exc import IntegrityError
from app.models import ImportTask, CustomerConfig
from app.services.import_service import create_import_task, process_import_task
from app.repositories.customer_configuration import load_configuration, ConfigurationError
from pydantic import ValidationError
from app.schemas.api import TaskSummary
from app.api.errors import APIError


def _reuse(task, customer_id, file_hash, *, retry_of_task_id=None, batch_id=None, batch_slot=None):
    if (task.customer_id, task.file_hash, task.retry_of_task_id, task.batch_id, task.batch_slot) != (customer_id, file_hash, retry_of_task_id, batch_id, batch_slot):
        raise APIError(409, "IDEMPOTENCY_CONFLICT", "同一幂等键已用于不同客户或不同文件内容")
    if task.status == "PROCESSING":
        raise APIError(409, "IDEMPOTENCY_IN_PROGRESS", "原任务尚在处理中，请稍后使用原幂等键重试")
    return TaskSummary.model_validate(task).model_copy(update={"idempotency_replayed": True})


def submit_import(session, customer_id, content, file_name, key, limits, *, duplicate_confirmed=False,
                  retry_of_task_id=None, batch_id=None, batch_slot=None):
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest() if key is not None else None
    file_hash = hashlib.sha256(content).hexdigest()
    if digest is not None:
        # Nonlocking first read avoids gap locks for absent keys. Reload by ID using
        # a current locking read if present; never trust a REPEATABLE READ snapshot.
        existing_id = session.scalar(select(ImportTask.id).where(ImportTask.idempotency_key_digest == digest))
        if existing_id is not None:
            task = session.scalar(select(ImportTask).where(ImportTask.id == existing_id)
                                  .with_for_update().execution_options(populate_existing=True))
            return _reuse(task, customer_id, file_hash, retry_of_task_id=retry_of_task_id, batch_id=batch_id, batch_slot=batch_slot)
    # Shared lock order for import/correction/manual/status: customer -> task -> row -> orders.
    customer = session.scalar(select(CustomerConfig).where(CustomerConfig.customer_id==customer_id).with_for_update())
    if customer is None:raise APIError(422,"CONFIG_ERROR","客户不存在")
    # The initial nonlocking key read can predate a competing winner's commit.
    # This existing current read runs after the customer lock and sees that winner.
    # Reuse its key before file/retry validation; do not add an absent-key locking
    # lookup (gap locks would introduce cross-customer key contention).
    duplicate_query = select(ImportTask).where(ImportTask.customer_id==customer_id, ImportTask.file_hash==file_hash)
    if digest is not None:
        # A newer explicit reimport must not hide this key's older committed task.
        duplicate_query = duplicate_query.order_by(case((ImportTask.idempotency_key_digest==digest, 1), else_=0).desc())
    duplicate=session.scalar(duplicate_query.order_by(ImportTask.id.desc()).limit(1).with_for_update().execution_options(populate_existing=True))
    if digest is not None and duplicate is not None and duplicate.idempotency_key_digest == digest:
        return _reuse(duplicate, customer_id, file_hash, retry_of_task_id=retry_of_task_id, batch_id=batch_id, batch_slot=batch_slot)
    if batch_id is not None:
        existing=session.scalar(select(ImportTask).where(ImportTask.batch_id==batch_id,ImportTask.batch_slot==batch_slot).with_for_update())
        if existing:
            if existing.idempotency_key_digest!=digest:raise APIError(409,"BATCH_SLOT_CONFLICT","该批次文件位置已使用其他请求标识")
            return _reuse(existing,customer_id,file_hash,retry_of_task_id=retry_of_task_id,batch_id=batch_id,batch_slot=batch_slot)
    if retry_of_task_id is not None:
        original=session.scalar(select(ImportTask).where(ImportTask.id==retry_of_task_id).with_for_update())
        if original is None or original.customer_id!=customer_id or original.status!='FAILED' or not original.error_message or original.total_rows!=0:
            raise APIError(422,"INVALID_RETRY_SOURCE","只能关联同客户的文件级失败任务，请对行级异常使用原明细处理入口")
    if duplicate is not None and not duplicate_confirmed:
        raise APIError(409,"DUPLICATE_FILE",f"该客户已导入相同内容文件，请查看任务 #{duplicate.id}，或明确确认再次导入")
    try:
        load_configuration(session, customer_id, for_update=True)
    except (ConfigurationError, ValidationError):
        raise APIError(422, "CONFIG_ERROR", "客户不存在、未启用或客户匹配配置无效") from None

    try:
        # Acquire the unique key with the task INSERT, before persisting rows or
        # taking their range locks. Loser may parse, but cannot create task rows
        # or orders. The unique index waits for winner commit/rollback.
        with session.begin_nested():
            task_id = create_import_task(session, customer_id, content, file_name=file_name, limits=limits,
                                         idempotency_key_digest=digest, retry_of_task_id=retry_of_task_id, batch_id=batch_id, batch_slot=batch_slot)
            task = session.get(ImportTask, task_id)
            if task.error_message:
                return TaskSummary.model_validate(task)  # Persist the failed file, with no invented rows.
    except IntegrityError as exc:
        if digest is None or getattr(exc.orig, "args", (None,))[0] != 1062:
            raise
        # Current read after unique-key wait sees winner even with an old snapshot.
        task = session.scalar(select(ImportTask).where(ImportTask.idempotency_key_digest == digest)
                              .with_for_update().execution_options(populate_existing=True))
        if task is None:
            raise
        return _reuse(task, customer_id, file_hash, retry_of_task_id=retry_of_task_id, batch_id=batch_id, batch_slot=batch_slot)

    result = process_import_task(session, task_id)
    return TaskSummary.model_validate(result)
