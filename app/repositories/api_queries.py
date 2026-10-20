"""Read-only API queries: bounded pages, stable order, no business updates."""
from sqlalchemy import select, func
from app.models import ImportTask, ImportTaskRow, SalesOrder
from app.schemas.api import TaskSummary, RowDetail, OrderDetail, Page
from app.api.errors import APIError


def get_task(session, task_id):
    task = session.get(ImportTask, task_id)
    if task is None:
        raise APIError(404, "TASK_NOT_FOUND", "导入任务不存在")
    return TaskSummary.model_validate(task)


def get_rows(session, task_id, page, page_size):
    get_task(session, task_id)
    condition = ImportTaskRow.task_id == task_id
    total = session.scalar(select(func.count()).select_from(ImportTaskRow).where(condition))
    rows = session.scalars(select(ImportTaskRow).where(condition)
                           .order_by(ImportTaskRow.sheet_name, ImportTaskRow.row_number, ImportTaskRow.id)
                           .offset((page - 1) * page_size).limit(page_size))
    return Page[RowDetail](total=total, page=page, page_size=page_size,
                           items=[RowDetail.model_validate(row) for row in rows])


def _order_details(session, orders):
    # Follow ancestry without guessing from product names; keep manual provenance through AUTO chains.
    ancestry={o.id:(o.source_type,o.is_simulated_manual,o.source_order_id) for o in orders}
    frontier={o.source_order_id for o in orders if o.source_order_id is not None}
    while frontier:
        missing=frontier-set(ancestry)
        if not missing:break
        ancestors=session.execute(select(SalesOrder.id,SalesOrder.source_type,SalesOrder.is_simulated_manual,SalesOrder.source_order_id).where(SalesOrder.id.in_(missing))).all()
        if not ancestors:break
        ancestry.update({r.id:(r.source_type,r.is_simulated_manual,r.source_order_id) for r in ancestors})
        frontier={r.source_order_id for r in ancestors if r.source_order_id is not None}
    def simulated(order):
        current=order.id;seen=set()
        while current is not None and current in ancestry and current not in seen:
            seen.add(current);kind,manual,parent=ancestry[current]
            if kind=='SEED' or manual:return True
            current=parent
        return False
    return [OrderDetail.model_validate(order).model_copy(update={
        "is_simulated_reference": simulated(order)}) for order in orders]


def get_order(session, order_id):
    order = session.get(SalesOrder, order_id)
    if order is None:
        raise APIError(404, "ORDER_NOT_FOUND", "销售订单不存在")
    return _order_details(session, [order])[0]


def order_conditions(customer_id=None,source_type=None,status=None,date_from=None,date_to=None):
    from datetime import datetime,time
    conditions = []
    if customer_id is not None:
        conditions.append(SalesOrder.customer_id == customer_id)
    if source_type is not None:
        conditions.append(SalesOrder.source_type == source_type)
    if status is not None:
        conditions.append(SalesOrder.status==status)
    else:conditions.append(SalesOrder.status!='VOIDED')
    if date_from:conditions.append(SalesOrder.created_at>=datetime.combine(date_from,time.min))
    if date_to:conditions.append(SalesOrder.created_at<=datetime.combine(date_to,time.max))
    if date_from and date_to and date_from>date_to:raise APIError(422,'INVALID_DATE_RANGE','开始日期不得晚于结束日期')
    return conditions

def get_orders(session, page, page_size, customer_id=None, source_type=None,status=None,date_from=None,date_to=None):
    conditions=order_conditions(customer_id,source_type,status,date_from,date_to)
    total = session.scalar(select(func.count()).select_from(SalesOrder).where(*conditions))
    orders = list(session.scalars(select(SalesOrder).where(*conditions)
                                 .order_by(SalesOrder.created_at.desc(), SalesOrder.id.desc())
                                 .offset((page - 1) * page_size).limit(page_size)))
    return Page[OrderDetail](total=total, page=page, page_size=page_size, items=_order_details(session, orders))
