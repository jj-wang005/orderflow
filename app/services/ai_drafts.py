"""Draft lifecycle; only confirm() may create a standard import task."""
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy import select, func
from pydantic import ValidationError
from app.api.errors import APIError
from app.models import AIExtraction, CustomerConfig, ImportTask, ImportTaskRow
from app.repositories.customer_configuration import load_configuration, ConfigurationError
from app.schemas.customer_configuration import CustomerConfiguration
from app.schemas.ai_extraction import ExtractionResult,DraftResult
from app.schemas.purchase_row import NormalizedPurchaseRow
from app.services.ai_input import prepare_input
from app.services.import_service import process_import_task
from app.services.purchase_guard import digest
from app.services.data_normalizer import normalize_code, normalize_quantity


def get_draft(session,draft_id):
    draft=session.get(AIExtraction,draft_id)
    if draft is None:raise APIError(404,'DRAFT_NOT_FOUND','解析草稿不存在')
    return draft


def lock_draft(session,draft_id):
    draft=get_draft(session,draft_id)
    session.scalar(select(CustomerConfig.id).where(CustomerConfig.customer_id==draft.customer_id).with_for_update())
    return session.scalar(select(AIExtraction).where(AIExtraction.id==draft_id).with_for_update().execution_options(populate_existing=True))


def start(session,cid,content,name,kind,key,provider,authorized):
    if session.scalar(select(CustomerConfig.id).where(CustomerConfig.customer_id==cid).with_for_update()) is None:
        raise APIError(422,'CUSTOMER_NOT_FOUND','请先选择已配置的客户')
    request_digest=digest(['ai-extraction',key]);source_hash=hashlib.sha256(content).hexdigest()
    fingerprint=digest([cid,source_hash,name,kind,provider,authorized])
    old=session.scalar(select(AIExtraction).where(AIExtraction.request_digest==request_digest))
    if old:
        if old.request_hash!=fingerprint:raise APIError(409,'DRAFT_KEY_CONFLICT','原解析操作标识不能用于不同客户或输入')
        return old,False
    try:config=load_configuration(session,cid,for_update=True)
    except (ConfigurationError,ValidationError):raise APIError(422,'CONFIG_ERROR','请先保存并启用合法客户配置') from None
    draft=AIExtraction(customer_id=cid,request_digest=request_digest,request_hash=fingerprint,source_hash=source_hash,
        source_kind=kind,file_name=name,original_content=content,config_snapshot=config.model_dump(mode='json'),provider=provider,
        status='PARSING',audit=[{'action':'CREATE','at':datetime.now().isoformat(),'external_authorized':authorized}],metrics={})
    session.add(draft);session.flush()
    return draft,True


def extract_saved(draft,max_pages,service):
    """Runs outside a DB transaction; only saved source/configuration is used."""
    doc=prepare_input(draft.source_kind,draft.original_content,draft.file_name,max_pages)
    result,metrics=service.extract(doc)
    return doc.sources,result,metrics


def finish(session,draft_id,revision,sources=None,result=None,metrics=None,error=None):
    draft=lock_draft(session,draft_id)
    # A timed-out worker can never overwrite a later retry or a human edit.
    if draft.revision!=revision or draft.status!='PARSING':return draft
    draft.source_manifest=sources or []
    if error:
        draft.status='FAILED';draft.error_code=error.code;draft.error_message=error.message
        draft.metrics={**(metrics or {}),'error_code':error.code}
    else:
        draft.original_result=result.model_dump(mode='json');draft.edited_result=draft.original_result
        draft.metrics=metrics;draft.status='DRAFT';draft.error_code=None;draft.error_message=None
    draft.audit=[*draft.audit,{'action':'EXTRACT_FAILED' if error else 'EXTRACTED','revision':revision,'at':datetime.now().isoformat(),'code':draft.error_code}]
    session.flush();return draft


def retry(session,draft_id,expected_revision,authorized,provider):
    draft=lock_draft(session,draft_id)
    stale=draft.status=='PARSING' and draft.updated_at<datetime.now()-timedelta(seconds=180)
    if draft.status!='FAILED' and not stale:raise APIError(409,'DRAFT_NOT_RETRYABLE','仅失败或超时失联的草稿可以重新解析')
    if draft.revision!=expected_revision:raise APIError(409,'REVISION_CONFLICT','草稿已变化，请刷新后重试')
    draft.revision+=1;draft.status='PARSING';draft.provider=provider;draft.error_code=None;draft.error_message=None
    draft.audit=[*draft.audit,{'action':'RETRY','revision':draft.revision,'at':datetime.now().isoformat(),'external_authorized':authorized}]
    session.flush();return draft


def edit(session,draft_id,body):
    draft=lock_draft(session,draft_id);payload=body.model_dump(mode='json');key=str(body.request_key)
    previous=next((x for x in draft.audit if x.get('request_key')==key),None)
    if previous:
        if previous.get('hash')!=digest(payload):raise APIError(409,'EDIT_KEY_CONFLICT','修改请求标识与原内容不同')
        return draft
    if draft.status!='DRAFT':raise APIError(409,'DRAFT_READ_ONLY','仅待确认草稿可以编辑，已导入结果不能重新执行')
    if draft.revision!=body.expected_revision:raise APIError(409,'REVISION_CONFLICT','草稿已被修改，请重新加载')
    valid={s['id'] for s in draft.source_manifest}
    if any(row.source_id not in valid for row in [*body.result.rows,*body.result.removed_rows]):raise APIError(422,'INVALID_SOURCE','明细来源位置不存在')
    draft.audit=[*draft.audit,{'action':'EDIT','request_key':key,'hash':digest(payload),'at':datetime.now().isoformat(),'note':body.note,'before':draft.edited_result,'after':body.result.model_dump(mode='json'),'actor':'本地销管（未鉴权）'}]
    draft.edited_result=body.result.model_dump(mode='json');draft.revision+=1;session.flush();return draft


def confirm(session,draft_id,body):
    draft=lock_draft(session,draft_id);fingerprint=digest(body.model_dump(mode='json'));key=digest(['ai-confirm',str(body.request_key)])
    if draft.status=='CONFIRMED':
        if draft.confirm_digest==key and draft.confirm_hash==fingerprint:return draft,True
        raise APIError(409,'DRAFT_ALREADY_CONFIRMED','该草稿已经确认，请查看关联任务，不能再次建单')
    if draft.status!='DRAFT' or not body.confirmed:raise APIError(409,'CONFIRMATION_REQUIRED','解析未完成或尚未明确确认，禁止创建任务或销售订单')
    if draft.revision!=body.expected_revision:raise APIError(409,'REVISION_CONFLICT','草稿版本已变化，请重新核对')
    # Same original input cannot be reordered/re-extracted into new artificial source positions.
    # Locking read is required under MySQL REPEATABLE READ: get_draft() may have
    # established a snapshot before waiting for the customer lock.
    previous=session.scalar(select(AIExtraction).where(AIExtraction.customer_id==draft.customer_id,AIExtraction.source_hash==draft.source_hash,AIExtraction.status=='CONFIRMED',AIExtraction.id!=draft.id).order_by(AIExtraction.id.desc()).limit(1).with_for_update())
    if previous:raise APIError(409,'AI_SOURCE_ALREADY_IMPORTED',f'同客户该原始输入已经确认导入，请查看任务 #{previous.import_task_id}；需要修正时处理原任务')
    config=CustomerConfiguration.model_validate(draft.config_snapshot)
    result=DraftResult.model_validate(draft.edited_result)
    if not result.rows:raise APIError(422,'NO_PURCHASE_ROWS','没有采购明细，请人工补充或重新提供输入')
    purchases=[]
    for index,row in enumerate(result.rows,1):
        if row.uncertain_fields:raise APIError(422,'UNVERIFIED_FIELDS',f'第 {index} 行仍有待核实字段，请逐行核对')
        fields={k:normalize_code(getattr(row,k)) for k in ('customer_order_no','customer_material_code','customer_model','customer_batch')}
        try:
            quantity=normalize_quantity(row.quantity);config.validate_required_values({**fields,'quantity':quantity})
            purchase=NormalizedPurchaseRow(customer_id=draft.customer_id,**fields,quantity=quantity,
                sheet_name='智能解析',row_number=index,
                raw_data={'extraction_id':draft.id,'source_id':row.source_id,'source_excerpt':row.source_excerpt,'extraction_warnings':row.extraction_warnings,'confirmed_values':row.model_dump(mode='json')})
        except (ValueError,ValidationError):raise APIError(422,'INCOMPLETE_PURCHASE',f'第 {index} 行缺少客户匹配必填字段，或数量不是合法正整数，请补全后确认') from None
        purchases.append(purchase)
    task=ImportTask(customer_id=draft.customer_id,file_name=draft.file_name,file_hash=draft.source_hash,status='PROCESSING',started_at=func.current_timestamp(),
        config_snapshot=draft.config_snapshot,config_version=config.customer.config_version,rule_version=config.rule.rule_version,
        idempotency_key_digest=digest(['ai-task',draft.id]))
    session.add(task);session.flush()
    for purchase in purchases:
        session.add(ImportTaskRow(task_id=task.id,sheet_name=purchase.sheet_name,row_number=purchase.row_number,raw_data=purchase.raw_data,normalized_data=purchase.model_dump(mode='json'),status='PENDING'))
    session.flush()
    draft.confirm_digest=key;draft.confirm_hash=fingerprint;draft.confirmed_at=datetime.now();draft.import_task_id=task.id;draft.status='CONFIRMED'
    draft.audit=[*draft.audit,{'action':'CONFIRM','revision':draft.revision,'at':draft.confirmed_at.isoformat(),'request_key':str(body.request_key),'note':body.note,'actor':'本地销管（未鉴权）','task_id':task.id,'result':draft.edited_result}]
    session.flush()
    process_import_task(session,task.id)  # Existing B guard, A snapshot, V1.1 matching/order workflow.
    return draft,False


def view(draft,detail=True):
    result={k:getattr(draft,k) for k in ('id','customer_id','file_name','source_kind','source_hash','status','provider','revision','error_code','error_message','metrics','import_task_id','created_at','confirmed_at')}
    config=draft.config_snapshot
    result['config_version']=config['customer']['config_version'];result['rule_version']=config['rule']['rule_version']
    if detail:result.update(source_manifest=draft.source_manifest,original_result=draft.original_result,edited_result=draft.edited_result,audit=draft.audit)
    result['required_fields']=CustomerConfiguration.model_validate(config).rule.required_fields
    return result
