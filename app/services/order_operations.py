"""Atomic, versioned row creation/order editing; no lifecycle transitions."""
import hashlib
import json
from decimal import Decimal
from sqlalchemy import select, func
from pydantic import ValidationError
from app.models import SalesOrder, ImportTask, ImportTaskRow, OrderChange
from app.schemas.purchase_row import NormalizedPurchaseRow, RawPurchaseRow
from app.schemas.api import OrderDetail, RowDetail
from app.repositories import import_repository as imports, order_repository as orders, api_queries
from app.services.manual_correction import _row, _config
from app.services.data_normalizer import normalize_row, normalize_code, normalize_quantity
from app.services.matching_engine import match_purchase_row
from app.schemas.matching import HistoricalOrderReference
from app.services.order_service import calculate_amount, generate_order_no, OrderValidationError
from app.api.errors import APIError
from app.services.purchase_guard import PurchaseBlocked

FIELDS=("customer_order_no","customer_material_code","customer_model","customer_batch","quantity")

def _identity(request):
    payload=request.model_dump(mode="json",exclude={"request_key"})
    return hashlib.sha256(str(request.request_key).encode()).hexdigest(),hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),payload

def _replay(session,target,digest,fingerprint):
    old=session.scalar(select(OrderChange).where(OrderChange.target==target,OrderChange.request_digest==digest).with_for_update())
    if old and old.request_hash!=fingerprint:raise APIError(409,"OPERATION_KEY_CONFLICT","同一操作标识不能用于不同请求内容")
    return old

def _snapshot(session,order):return api_queries.get_order(session,order.id).model_dump(mode="json")

def _log(session,order,target,digest,fingerprint,payload,before):
    session.add(OrderChange(order_id=order.id,target=target,request_digest=digest,request_hash=fingerprint,
                            payload=payload,before_data=before,after_data=_snapshot(session,order),note=payload['note']))
    session.flush()

def _amount(price,quantity):
    try:return calculate_amount(price,quantity)
    except OrderValidationError as e:raise APIError(422,e.code,e.message) from None


def has_audited_price_change(session,order):
    """Human evidence recorded by this edit workflow, never an ERP verification."""
    logs=session.scalars(select(OrderChange).where(OrderChange.order_id==order.id,
                         OrderChange.target==f"order:{order.id}").order_by(OrderChange.id.desc()))
    for log in logs:
        before=log.before_data or {};after=log.after_data
        if before.get('unit_price') is None:continue
        if Decimal(before['unit_price'])!=Decimal(after['unit_price']):
            return (Decimal(after['unit_price'])==order.unit_price and
                    bool((log.payload.get('verification_basis') or '').strip()) and
                    after.get('source_order_id')==order.source_order_id and
                    (after.get('product_code'),after.get('product_name'))==(order.product_code,order.product_name))
    return False

def manual_create(session,task_id,row_id,request):
    if session.get(ImportTask,task_id) is None:raise APIError(404,"TASK_NOT_FOUND","任务不存在")
    task=imports.lock_task(session,task_id);row=_row(session,task_id,row_id,locked=True)
    digest,fingerprint,payload=_identity(request);target=f"row:{row_id}"
    previous=_replay(session,target,digest,fingerprint)
    if previous:return {"order":api_queries.get_order(session,previous.order_id),"row":RowDetail.model_validate(row),"replayed":True}
    if row.created_order_id or row.status!='NOT_MATCHED' or task.status=='PROCESSING':raise APIError(409,"ROW_NOT_MANUAL_CREATABLE","仅未建单的 NOT_MATCHED 明细可以手动新增")
    if session.scalar(select(SalesOrder.id).where(SalesOrder.import_task_id==task_id,SalesOrder.source_sheet_name==row.sheet_name,SalesOrder.source_row_number==row.row_number).with_for_update()):raise APIError(409,"ROW_ALREADY_CREATED","此采购位置已有订单")
    if row.manual_revision!=request.expected_revision:raise APIError(409,"REVISION_CONFLICT","明细版本已变化，请刷新核对")
    config=_config(session,task)
    try:
        purchase=NormalizedPurchaseRow.model_validate_json(json.dumps(row.normalized_data))
        config.validate_required_values(purchase.model_dump())
    except (ValidationError,ValueError):raise APIError(422,"INVALID_INPUT","采购信息不合法，请先补录修正") from None
    if (purchase.customer_id,purchase.sheet_name,purchase.row_number)!=(task.customer_id,row.sheet_name,row.row_number):raise APIError(409,"INVALID_LINK","采购来源关联不一致")
    match=match_purchase_row(session,purchase,config=config)
    if match.status!='NOT_MATCHED':raise APIError(503 if match.status=='MATCH_ERROR' else 409,"HISTORICAL_MATCH_REQUIRED","重新匹配结果为 "+match.status+"，请先通过补录匹配流程处理，禁止绕过历史校验")
    # Provenance is set by the server, independent of product naming or legacy checkbox.
    order=SalesOrder(order_no=generate_order_no(),customer_id=task.customer_id,**{f:getattr(purchase,f) for f in FIELDS},
                     **request.product.model_dump(),amount=_amount(request.product.unit_price,purchase.quantity),
                     source_type='AUTO',status='CREATED',source_order_id=None,import_task_id=task_id,
                     source_sheet_name=row.sheet_name,source_row_number=row.row_number,is_simulated_manual=True)
    try:
        orders.insert_order(session,order)
    except PurchaseBlocked as exc:
        raise APIError(409,"PURCHASE_REVIEW_REQUIRED",exc.message+"；请在采购明细中进行采购核实") from None
    row.created_order_id=order.id;row.matched_order_id=None;row.status='SUCCESS';row.error_code=None;row.error_message=None;row.manual_revision+=1
    imports.refresh_statistics(session,task);task.finished_at=func.current_timestamp()
    _log(session,order,target,digest,fingerprint,payload,None)
    return {"order":api_queries.get_order(session,order.id),"row":RowDetail.model_validate(row),"replayed":False}

def edit_context(session,order_id):
    order=session.get(SalesOrder,order_id)
    if order is None:raise APIError(404,"ORDER_NOT_FOUND","销售订单不存在")
    logs=list(session.scalars(select(OrderChange).where(OrderChange.order_id==order_id).order_by(OrderChange.id.desc()).limit(100)))
    reference=session.get(SalesOrder,order.source_order_id) if order.source_order_id is not None else None
    row=session.scalar(select(ImportTaskRow).where(ImportTaskRow.task_id==order.import_task_id,ImportTaskRow.created_order_id==order_id)) if order.import_task_id else None
    editable=(order.source_type=='AUTO' and order.status=='CREATED' and row is not None and row.status=='SUCCESS'
              and (row.sheet_name,row.row_number,row.matched_order_id)==(order.source_sheet_name,order.source_row_number,order.source_order_id))
    return {"order":api_queries.get_order(session,order_id),"editable":editable,
            "historical_unit_price":reference.unit_price if reference else None,
            "history":[{k:getattr(a,k) for k in ('id','target','before_data','after_data','note','actor_label','created_at','payload')} for a in logs]}

def edit_order(session,order_id,request):
    # Do not lock order before task: all row workflows share task->row->order.
    task_id=session.scalar(select(SalesOrder.import_task_id).where(SalesOrder.id==order_id))
    if task_id is None:
        if session.get(SalesOrder,order_id) is None:raise APIError(404,"ORDER_NOT_FOUND","订单不存在")
        raise APIError(409,"ORDER_READ_ONLY","无导入关联或历史订单不可编辑")
    task=imports.lock_task(session,task_id)
    row=session.scalar(select(ImportTaskRow).where(ImportTaskRow.task_id==task_id,ImportTaskRow.created_order_id==order_id).with_for_update().execution_options(populate_existing=True))
    order=session.scalar(select(SalesOrder).where(SalesOrder.id==order_id).with_for_update().execution_options(populate_existing=True))
    digest,fingerprint,payload=_identity(request);target=f"order:{order_id}"
    previous=_replay(session,target,digest,fingerprint)
    if previous:return {"order":api_queries.get_order(session,order_id),"replayed":True}
    if order.source_type!='AUTO' or order.status!='CREATED':raise APIError(409,"ORDER_READ_ONLY","仅 AUTO 且 CREATED 订单可编辑")
    if not row or row.status!='SUCCESS' or order.customer_id!=task.customer_id or (row.sheet_name,row.row_number,row.matched_order_id)!=(order.source_sheet_name,order.source_row_number,order.source_order_id):raise APIError(409,"INVALID_LINK","订单与成功明细关联不一致")
    if order.edit_revision!=request.expected_revision:raise APIError(409,"REVISION_CONFLICT","订单已被修改，请重新加载")
    if (request.product.product_code,request.product.product_name)!=(order.product_code,order.product_name):
        raise APIError(409,"PRODUCT_READ_ONLY","我司产品编码和名称不可修改")
    if request.product.unit_price!=order.unit_price and not (request.verification_basis or '').strip():
        raise APIError(422,"PRICE_BASIS_REQUIRED","修改单价必须填写产品与价格核对依据（人工说明，不代表 OMS/ERP 验证）")
    if set(request.values)!=set(FIELDS):raise APIError(422,"INVALID_INPUT","请提交全部客户字段和数量")
    config=_config(session,task)
    parsed=normalize_row(config,RawPurchaseRow(sheet_name=row.sheet_name,row_number=row.row_number,raw_data=row.raw_data,
                         raw_cells=(row.normalized_data or {}).get('raw_cells',{}),values={c:request.values[f] for c,f in config.customer.field_mapping.items()}))
    if parsed.data is None:raise APIError(422,"INVALID_INPUT","；".join(e.message for e in parsed.errors))
    try:
        fields={f:normalize_code(request.values[f]) for f in FIELDS[:-1]};fields['quantity']=normalize_quantity(request.values['quantity'])
        purchase=NormalizedPurchaseRow.model_validate({**parsed.data.model_dump(),**fields})
    except ValueError:raise APIError(422,"INVALID_INPUT","客户字段或采购数量不合法") from None
    if order.source_order_id is not None:
        matched=match_purchase_row(session,purchase,config=config);current=orders.lock_reference(session,order.source_order_id)
        try:locked=HistoricalOrderReference.model_validate(current)
        except ValidationError:raise APIError(409,"REFERENCE_INVALID","原历史来源不再合法") from None
        if matched.status!='MATCHED' or matched.matched_order!=locked or (request.product.product_code,request.product.product_name)!=(locked.product_code,locked.product_name):raise APIError(409,"REFERENCE_CONFLICT","无法保持原历史匹配及产品，禁止修改")
    else:
        if not order.is_simulated_manual:raise APIError(409,"UNVERIFIED_MANUAL_ORIGIN","无法确认手动订单来源")
    before=_snapshot(session,order)
    for f in FIELDS:setattr(order,f,getattr(purchase,f))
    for f,v in request.product.model_dump().items():setattr(order,f,v)
    order.amount=_amount(order.unit_price,order.quantity);order.edit_revision+=1
    # Initial purchase snapshot and Excel immutable; edits have separate audit.
    session.flush();_log(session,order,target,digest,fingerprint,payload,before)
    return {"order":api_queries.get_order(session,order_id),"replayed":False}
