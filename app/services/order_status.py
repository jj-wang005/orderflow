"""Normal forward-only lifecycle with shared order audit and optimistic version."""
from sqlalchemy import select
from app.models import SalesOrder, ImportTaskRow
from app.api.errors import APIError
from app.repositories import import_repository as imports, api_queries
from app.services.manual_correction import _config
from app.services.order_operations import _identity,_replay,_log,_snapshot,_amount,FIELDS,has_audited_price_change
from app.schemas.order_status import NEXT_STATUS,HISTORICAL_STATUSES,HISTORICAL_SOURCES
from app.schemas.order_operations import ProductInput
from app.schemas.purchase_row import NormalizedPurchaseRow
from pydantic import ValidationError
from app.schemas.matching import HistoricalOrderReference
from app.repositories.order_repository import lock_reference

def void_order(session,order_id,request):
    parent=session.execute(select(SalesOrder.import_task_id,SalesOrder.source_type).where(SalesOrder.id==order_id)).first()
    if parent is None:raise APIError(404,'ORDER_NOT_FOUND','销售订单不存在')
    if parent.source_type!='AUTO' or parent.import_task_id is None:raise APIError(409,'ORDER_READ_ONLY','历史订单或无采购来源订单不可作废')
    task=imports.lock_task(session,parent.import_task_id)
    row=session.scalar(select(ImportTaskRow).where(ImportTaskRow.task_id==task.id,ImportTaskRow.created_order_id==order_id).with_for_update().execution_options(populate_existing=True))
    order=session.scalar(select(SalesOrder).where(SalesOrder.id==order_id).with_for_update().execution_options(populate_existing=True))
    digest,fingerprint,payload=_identity(request);target=f'void:{order_id}'
    if _replay(session,target,digest,fingerprint):return {'order':api_queries.get_order(session,order_id),'replayed':True}
    if order.status!='CREATED':raise APIError(409,'ORDER_NOT_VOIDABLE','仅已创建订单可以作废；已作废订单不可恢复')
    if order.edit_revision!=request.expected_revision:raise APIError(409,'REVISION_CONFLICT','订单版本已变化，请刷新核对')
    if not row or row.status!='SUCCESS' or order.customer_id!=task.customer_id or (row.sheet_name,row.row_number,row.matched_order_id)!=(order.source_sheet_name,order.source_row_number,order.source_order_id):raise APIError(409,'INVALID_LINK','订单与成功采购行关联不完整，禁止作废')
    before=_snapshot(session,order)
    order.status='VOIDED';order.edit_revision+=1;session.flush()
    _log(session,order,target,digest,fingerprint,payload,before)
    # Never clear row links or purchase digests: void is a terminal state, not deletion.
    return {'order':api_queries.get_order(session,order_id),'replayed':False}

def update_status(session,order_id,request):
    parent=session.execute(select(SalesOrder.import_task_id,SalesOrder.source_type).where(SalesOrder.id==order_id)).first()
    if parent is None:raise APIError(404,'ORDER_NOT_FOUND','销售订单不存在')
    if parent.source_type!='AUTO' or parent.import_task_id is None:raise APIError(409,'ORDER_READ_ONLY','历史订单不通过日常履约接口修改')
    task=imports.lock_task(session,parent.import_task_id)
    row=session.scalar(select(ImportTaskRow).where(ImportTaskRow.task_id==task.id,ImportTaskRow.created_order_id==order_id).with_for_update().execution_options(populate_existing=True))
    order=session.scalar(select(SalesOrder).where(SalesOrder.id==order_id).with_for_update().execution_options(populate_existing=True))
    if not row or row.status!='SUCCESS' or order.source_type!='AUTO' or order.customer_id!=task.customer_id or (row.sheet_name,row.row_number,row.matched_order_id)!=(order.source_sheet_name,order.source_row_number,order.source_order_id):
        raise APIError(409,'INVALID_LINK','订单与成功采购明细关联不完整，禁止状态变更')
    digest,fingerprint,payload=_identity(request);target=f'status:{order_id}'
    if _replay(session,target,digest,fingerprint):return {'order':api_queries.get_order(session,order_id),'replayed':True}
    if order.status==request.status:
        return {'order':api_queries.get_order(session,order_id),'replayed':True}  # No-op: no version/log write.
    if order.edit_revision!=request.expected_revision:raise APIError(409,'REVISION_CONFLICT','订单版本已变化，请刷新后重新选择状态')
    if NEXT_STATUS.get(order.status)!=request.status:raise APIError(409,'INVALID_TRANSITION','订单仅允许按已创建→已确认→进行中→已发货→已完成顺序流转')
    config=_config(session,task)
    try:
        product=ProductInput.model_validate({'product_code':order.product_code,'product_name':order.product_name,'unit_price':str(order.unit_price)})
        if (product.product_code,product.product_name)!=(order.product_code,order.product_name):raise ValueError('unnormalized product')
        purchase=NormalizedPurchaseRow(customer_id=order.customer_id,**{f:getattr(order,f) for f in FIELDS},raw_data=row.raw_data,sheet_name=row.sheet_name,row_number=row.row_number)
        config.validate_required_values(purchase.model_dump())
        if any(isinstance(getattr(purchase,f),str) and getattr(purchase,f)!=getattr(purchase,f).strip() for f in FIELDS[:-1]):raise ValueError('unnormalized customer identifier')
        if _amount(order.unit_price,order.quantity)!=order.amount:raise ValueError('amount')
    except (ValidationError,ValueError):raise APIError(422,'INVALID_ORDER','产品、价格、数量、客户字段或金额不合法，不能确认或推进订单') from None
    if order.source_order_id is not None:
        try:
            reference=HistoricalOrderReference.model_validate(lock_reference(session,order.source_order_id))
            if _amount(reference.unit_price,reference.quantity)!=reference.amount:raise ValueError('reference amount')
            threshold=config.rule.filter_conditions.historical_quantity
            if threshold and not {'gt':reference.quantity>threshold.value,'gte':reference.quantity>=threshold.value,'eq':reference.quantity==threshold.value}[threshold.operator]:raise ValueError('historical quantity')
        except (ValueError,ValidationError):raise APIError(409,'REFERENCE_INVALID','原历史产品、价格、数量或金额不合法') from None
        source=session.scalar(select(SalesOrder).where(SalesOrder.id==order.source_order_id).with_for_update())
        if (source is None or source.status not in HISTORICAL_STATUSES or source.source_type not in HISTORICAL_SOURCES or
            source.customer_id!=order.customer_id or
            (source.product_code,source.product_name)!=(order.product_code,order.product_name) or
            (source.unit_price!=order.unit_price and not has_audited_price_change(session,order)) or
            any(getattr(source,f)!=getattr(order,f) for f in config.rule.match_fields+config.rule.validation_config.consistency_fields)):
            raise APIError(409,'REFERENCE_INVALID','原历史来源已不可靠，禁止确认或推进')
    before=_snapshot(session,order);order.status=request.status;order.edit_revision+=1;session.flush()
    _log(session,order,target,digest,fingerprint,payload,before)
    return {'order':api_queries.get_order(session,order_id),'replayed':False}
