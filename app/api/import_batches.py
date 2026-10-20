"""Synchronous bounded batch, one transaction per file, independently retryable slots."""
import hashlib
from datetime import date, datetime, time, timedelta
from pathlib import PureWindowsPath
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, File, Form, UploadFile, Query
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.config import get_settings
from app.models import ImportTask,AIExtraction
from app.schemas.api import Envelope, TaskSummary, Page
from app.schemas.import_task import TaskStatus
from app.schemas.import_batch import BatchItem
from app.schemas.purchase_review import PurchaseReviewRequest
from app.services.http_import import submit_import
from app.services.excel_parser import ParseLimits
from app.services.import_service import ImportRejected
from app.services import purchase_review
from app.api.errors import APIError

router=APIRouter(prefix="/api/v1",tags=["批量导入与任务管理"])
DB=Annotated[Session,Depends(get_db)]

def latest_duplicate(session,customer_id,file_hash):
    task=session.scalar(select(ImportTask).where(ImportTask.customer_id==customer_id,ImportTask.file_hash==file_hash).order_by(ImportTask.id.desc()).limit(1))
    return TaskSummary.model_validate(task) if task else None

@router.post("/imports/duplicate-check")
def duplicate_check(session:DB,file:Annotated[UploadFile,File()],customer_id:Annotated[str,Form(min_length=1,max_length=64)]):
    try:
        content=file.file.read(get_settings().upload_max_bytes+1)
        if len(content)>get_settings().upload_max_bytes:raise APIError(413,"FILE_TOO_LARGE","文件超过允许大小")
        return Envelope(data={"file_hash":hashlib.sha256(content).hexdigest(),"previous":latest_duplicate(session,customer_id,hashlib.sha256(content).hexdigest())})
    finally:file.file.close()

@router.get("/imports",response_model=Envelope[Page[TaskSummary]])
def task_list(session:DB,page:Annotated[int,Query(ge=1)]=1,page_size:Annotated[int,Query(ge=1,le=100)]=20,
              customer_id:Annotated[str|None,Query(max_length=64)]=None,file_name:Annotated[str|None,Query(max_length=255)]=None,
              status:TaskStatus|None=None,date_from:date|None=None,date_to:date|None=None,batch_id:UUID|None=None):
    if date_from and date_to and date_from>date_to:raise APIError(422,"INVALID_DATE_RANGE","开始日期不能晚于结束日期")
    if date_to and date_to==date.max:raise APIError(422,"INVALID_DATE_RANGE","结束日期超出支持范围")
    conditions=[]
    if customer_id is not None:conditions.append(ImportTask.customer_id==customer_id)
    if file_name:conditions.append(ImportTask.file_name.contains(file_name,autoescape=True))
    if status:conditions.append(ImportTask.status==status)
    if date_from:conditions.append(ImportTask.created_at>=datetime.combine(date_from,time.min))
    if date_to:conditions.append(ImportTask.created_at<datetime.combine(date_to+timedelta(days=1),time.min))
    if batch_id:conditions.append(ImportTask.batch_id==str(batch_id))
    total=session.scalar(select(func.count()).select_from(ImportTask).where(*conditions))
    tasks=session.scalars(select(ImportTask).where(*conditions).order_by(ImportTask.created_at.desc(),ImportTask.id.desc()).offset((page-1)*page_size).limit(page_size))
    return Envelope(data=Page[TaskSummary](total=total,page=page,page_size=page_size,items=[TaskSummary.model_validate(t) for t in tasks]))

@router.get("/imports/{task_id}/related")
def related_tasks(task_id:int,session:DB):
    task=session.get(ImportTask,task_id)
    if task is None:raise APIError(404,"TASK_NOT_FOUND","任务不存在")
    parent=session.get(ImportTask,task.retry_of_task_id) if task.retry_of_task_id else None
    children=list(session.scalars(select(ImportTask).where(ImportTask.retry_of_task_id==task_id).order_by(ImportTask.id)))
    extraction_id=session.scalar(select(AIExtraction.id).where(AIExtraction.import_task_id==task_id))
    return Envelope(data={"parent":TaskSummary.model_validate(parent) if parent else None,"retries":[TaskSummary.model_validate(t) for t in children],"ai_extraction_id":extraction_id})

@router.post("/import-batches")
def import_batch(session:DB,batch_id:Annotated[UUID,Form()],items:Annotated[str,Form()],files:Annotated[list[UploadFile],File()]):
    try:
        try:metadata=TypeAdapter(list[BatchItem]).validate_json(items)
        except ValidationError:raise APIError(422,"INVALID_BATCH","请为每份文件指定客户及稳定的文件操作标识") from None
        if not 1<=len(files)<=20 or len(metadata)!=len(files) or len({m.slot_id for m in metadata})!=len(files) or len({m.request_key for m in metadata})!=len(files):
            raise APIError(422,"INVALID_BATCH","每批 1～20 份文件；文件、客户和不重复的操作标识必须一一对应")
        results=[];max_bytes=get_settings().upload_max_bytes
        # Input preflight precedes all writes; each individual read remains bounded.
        contents=[file.file.read(max_bytes+1) for file in files]
        if sum(map(len,contents))>100*1024*1024:raise APIError(413,"BATCH_TOO_LARGE","每批总文件大小不能超过 100 MiB，请拆分批次")
        for file,body,content in zip(files,metadata,contents):
            try:
                name=PureWindowsPath(file.filename or "").name
                if not name or len(name)>255 or any(ord(c)<32 for c in name):raise APIError(422,"INVALID_FILE","文件名不合法")
                if len(content)>max_bytes:raise APIError(413,"FILE_TOO_LARGE","该文件超过允许大小，未创建任务")
                with session.begin():
                    result=submit_import(session,body.customer_id,content,name,body.request_key,ParseLimits(max_size_bytes=max_bytes),duplicate_confirmed=body.duplicate_confirmed,retry_of_task_id=body.retry_of_task_id,batch_id=str(batch_id),batch_slot=str(body.slot_id))
                results.append({"slot_id":str(body.slot_id),"file_name":name,"customer_id":body.customer_id,"task":result,"error":None})
            except (APIError,ImportRejected) as exc:
                results.append({"slot_id":str(body.slot_id),"file_name":file.filename,"customer_id":body.customer_id,"task":None,"error":{"code":exc.code,"message":exc.message}})
            except Exception:
                # A file transaction has rolled back. Never expose DB/SQL/credentials.
                results.append({"slot_id":str(body.slot_id),"file_name":file.filename,"customer_id":body.customer_id,"task":None,"error":{"code":"FILE_TRANSACTION_FAILED","message":"该文件处理事务已回滚，其他文件继续；请保留原操作标识重试"}})
        tasks=[r["task"] for r in results if r["task"] is not None]
        return Envelope(data={"batch_id":str(batch_id),"items":results,"summary":{"file_count":len(results),"completed_files":len(tasks),"file_error_count":sum(bool(t.error_message and t.total_rows==0) for t in tasks)+len(results)-len(tasks),
            "success_rows":sum(t.success_rows for t in tasks),"unmatched_rows":sum(t.unmatched_rows for t in tasks),"failed_rows":sum(t.failed_rows for t in tasks),"duplicate_rows":sum(t.duplicate_rows for t in tasks),"review_rows":sum(t.review_rows for t in tasks)}})
    finally:
        for file in files:file.file.close()

@router.get("/imports/{task_id}/rows/{row_id}/purchase-review")
def get_purchase_review(task_id:int,row_id:int,session:DB):
    return Envelope(data=purchase_review.context(session,task_id,row_id))

@router.post("/imports/{task_id}/rows/{row_id}/purchase-review")
def post_purchase_review(task_id:int,row_id:int,body:PurchaseReviewRequest,session:DB):
    with session.begin():result=purchase_review.review_purchase(session,task_id,row_id,body)
    return Envelope(data=result)
