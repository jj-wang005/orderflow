"""Human procurement evidence; cannot disable product/price/customer safety."""
from copy import deepcopy
from datetime import datetime
from sqlalchemy import select
from app.models import ImportTask,SalesOrder
from app.repositories import import_repository as imports
from app.services.manual_correction import _row
from app.services.purchase_guard import candidates, exact_existing, input_key, digest, compatible_fields, reference_key, candidate_snapshot
from app.services.import_service import process_task_row
from app.schemas.api import RowDetail
from app.api.errors import APIError

def context(session,task_id,row_id):
    task=session.get(ImportTask,task_id)
    if task is None:raise APIError(404,"TASK_NOT_FOUND","任务不存在")
    row=_row(session,task_id,row_id)
    return {"row":RowDetail.model_validate(row),"revision":row.manual_revision,
            "candidates":[{**candidate_snapshot(o),"quantity":str(o.quantity),"created_at":o.created_at} for o in candidates(session,task,row) if compatible_fields(o,row)],
            "history":(row.purchase_review or {}).get("history",[])}

def review_purchase(session,task_id,row_id,body):
    if session.get(ImportTask,task_id) is None:raise APIError(404,"TASK_NOT_FOUND","任务不存在")
    task=imports.lock_task(session,task_id);row=_row(session,task_id,row_id,locked=True)
    payload=body.model_dump(mode="json");fingerprint=digest(payload)
    history=deepcopy((row.purchase_review or {}).get("history",[]))
    old=next((r for r in history if r["request_key"]==str(body.request_key)),None)
    if old:
        if old["request_digest"]!=fingerprint:raise APIError(409,"REVIEW_KEY_CONFLICT","同一核实请求标识不能用于不同内容")
        return {"row":RowDetail.model_validate(row),"replayed":True}
    if row.status not in {"REVIEW_REQUIRED","NOT_MATCHED"} or row.created_order_id or task.status=="PROCESSING":
        raise APIError(409,"REVIEW_NOT_ALLOWED","此行不能再次进行采购核实")
    if row.manual_revision!=body.expected_revision:raise APIError(409,"REVISION_CONFLICT","采购行已变化，请重新打开核实")
    if not row.normalized_data:raise APIError(422,"INVALID_INPUT","请先修正采购字段")
    exact=exact_existing(session,task,row)
    if exact is not None:raise APIError(409,"SOURCE_ALREADY_CREATED","该来源行已有销售订单，禁止作为新采购再次创建")
    possible=candidates(session,task,row)
    if body.decision=="EXISTING":
        allowed={o.id for o in possible if compatible_fields(o,row)}
        if body.existing_order_id not in allowed:raise APIError(422,"INVALID_EXISTING_ORDER","只能关联同客户且采购字段相符的候选订单")
    elif body.purchase_reference:
        claim=session.scalar(select(SalesOrder).where(SalesOrder.purchase_reference_digest==reference_key(task.customer_id,body.purchase_reference)).with_for_update())
        if claim is not None:raise APIError(409,'PURCHASE_REFERENCE_EXISTS','该稳定采购明细编号已经建单，请核实是否为已处理采购；不会自动把新采购决定改为关联旧订单')
    record={**payload,"request_digest":fingerprint,"input_digest":input_key(row),"confirmed_at":datetime.now().isoformat(),"actor_label":"本地销管核实（未鉴权）",
            "risk_snapshot":deepcopy((row.purchase_review or {}).get('risk')),"candidate_snapshot":[candidate_snapshot(o) for o in possible]}
    history.append(record);row.purchase_review={**deepcopy(row.purchase_review or {}),"history":deepcopy(history),"current":deepcopy(record)};row.manual_revision+=1
    if body.decision=="EXISTING":
        row.status,row.error_code,row.error_message="DUPLICATE","DUPLICATE","销管已核实为已有采购，未重复建单"
        row.duplicate_order_id=body.existing_order_id
        imports.refresh_statistics(session,task)
    else:
        row.status,row.error_code,row.error_message="PENDING",None,None
        session.flush();process_task_row(session,task_id,row.sheet_name,row.row_number)
    record['result']={'status':row.status,'created_order_id':row.created_order_id,'duplicate_order_id':row.duplicate_order_id,'error_message':row.error_message}
    history[-1]=record
    row.purchase_review={**deepcopy(row.purchase_review or {}),'history':deepcopy(history),'current':deepcopy(record)}
    session.flush()
    return {"row":RowDetail.model_validate(row),"replayed":False}
