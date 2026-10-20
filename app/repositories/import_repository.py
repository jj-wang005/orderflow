from collections import Counter
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.models import CustomerConfig, ImportTask, ImportTaskRow, SalesOrder
from app.schemas.import_task import CreatedOrder, ImportRowResult, ImportTaskResult

class ImportInvariantError(ValueError):
    pass

def lock_task(session: Session, task_id: int) -> ImportTask:
    customer_id = session.scalar(select(ImportTask.customer_id).where(ImportTask.id == task_id))
    if customer_id is not None:
        session.scalar(select(CustomerConfig.id).where(CustomerConfig.customer_id == customer_id).with_for_update())
    task = session.scalar(select(ImportTask).where(ImportTask.id == task_id).with_for_update().execution_options(populate_existing=True))
    if task is None:
        raise ImportInvariantError("导入任务不存在")
    return task

def lock_row(session: Session, task_id: int, sheet_name: str, row_number: int) -> ImportTaskRow:
    row = session.scalar(select(ImportTaskRow).where(ImportTaskRow.task_id == task_id,
                         ImportTaskRow.sheet_name == sheet_name, ImportTaskRow.row_number == row_number)
                         .with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise ImportInvariantError("导入任务中不存在该工作表/行")
    return row

def row_result(session: Session, row: ImportTaskRow) -> ImportRowResult:
    result = ImportRowResult.model_validate(row)
    if row.status == "SUCCESS":
        order = session.scalar(select(SalesOrder).where(SalesOrder.id == row.created_order_id).with_for_update()
                               .execution_options(populate_existing=True)) if row.created_order_id else None
        if (order is None or order.import_task_id != row.task_id or order.source_sheet_name != row.sheet_name
            or order.source_row_number != row.row_number or order.source_order_id != row.matched_order_id):
            raise ImportInvariantError("成功行的订单关联不完整，拒绝重复建单")
        result.created_order = CreatedOrder.model_validate(order)
    elif row.created_order_id is not None:
        raise ImportInvariantError("非成功行已关联订单，拒绝不一致状态")
    return result

def refresh_statistics(session: Session, task: ImportTask) -> None:
    counts = Counter(session.scalars(select(ImportTaskRow.status).where(ImportTaskRow.task_id == task.id)
                                    .order_by(ImportTaskRow.id).with_for_update()).all())
    known = {"PENDING", "SUCCESS", "NOT_MATCHED", "AMBIGUOUS", "INVALID_DATA", "FAILED", "DUPLICATE", "REVIEW_REQUIRED"}
    if set(counts) - known:
        raise ImportInvariantError("未知导入行状态")
    task.total_rows = sum(counts.values())
    task.success_rows = counts["SUCCESS"]
    task.unmatched_rows = counts["NOT_MATCHED"]
    task.duplicate_rows = counts["DUPLICATE"]
    task.review_rows = counts["REVIEW_REQUIRED"]
    task.failed_rows = counts["AMBIGUOUS"] + counts["INVALID_DATA"] + counts["FAILED"]
    if counts["PENDING"]:
        task.status, task.finished_at = "PROCESSING", None
    else:
        task.status = ("SUCCESS" if task.success_rows + task.duplicate_rows == task.total_rows and task.total_rows > 0 else
                       "PARTIAL_SUCCESS" if task.success_rows + task.duplicate_rows > 0 else "FAILED")
        if task.finished_at is None:
            task.finished_at = func.current_timestamp()
    session.flush()

def task_result(session: Session, task: ImportTask) -> ImportTaskResult:
    rows = session.scalars(select(ImportTaskRow).where(ImportTaskRow.task_id == task.id).order_by(ImportTaskRow.sheet_name, ImportTaskRow.row_number).with_for_update()
                           .execution_options(populate_existing=True)).all()
    result = ImportTaskResult.model_validate({**{name: getattr(task,name) for name in ImportTaskResult.model_fields if name not in {"rows","pending_rows"}},
                                              "pending_rows": sum(r.status == "PENDING" for r in rows),
                                              "rows": [row_result(session,row) for row in rows]})
    return result
