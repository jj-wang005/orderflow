import time
import base64
from typing import Annotated
from uuid import UUID
from pathlib import PureWindowsPath
from urllib.parse import quote
from fastapi import APIRouter, Depends, File, Form, UploadFile, Query, Response
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from pydantic import BaseModel,Field
from app.api.errors import APIError
from app.core.config import get_settings
from app.core.database import get_db
from app.models import AIExtraction
from app.schemas.api import Envelope
from app.schemas.ai_extraction import DraftEdit,DraftConfirm
from app.services import ai_drafts
from app.services.ai_extraction import AIExtractionService,get_extraction_service
from app.services.ai_input import source_name,prepare_input

router=APIRouter(prefix='/api/v1/ai-extractions',tags=['智能采购解析'])
DB=Annotated[Session,Depends(get_db)]
Service=Annotated[AIExtractionService,Depends(get_extraction_service)]


def external_gate(authorized):
    settings=get_settings()
    if settings.ai_provider=='mimo' and (not settings.mimo_external_enabled or not authorized):
        raise APIError(403,'EXTERNAL_AUTHORIZATION_REQUIRED','启用外部 MiMo 后，还须确认本次输入已获对外发送授权；未授权请使用 Mock 或标准 Excel')


def run(session,draft,service):
    draft_id,revision=draft.id,draft.revision;start=time.monotonic()
    try:
        sources,result,metrics=ai_drafts.extract_saved(draft,get_settings().ai_max_pdf_pages,service)
        with session.begin():draft=ai_drafts.finish(session,draft_id,revision,sources,result,metrics)
    except APIError as error:
        with session.begin():draft=ai_drafts.finish(session,draft_id,revision,error=error,metrics={'duration_ms':round((time.monotonic()-start)*1000)})
    except Exception:
        with session.begin():draft=ai_drafts.finish(session,draft_id,revision,error=APIError(502,'AI_EXTRACTION_FAILED','解析处理失败，未创建销售订单；请在原草稿重试'))
    return Envelope(data=ai_drafts.view(draft))


@router.get('/capabilities')
def capabilities():
    s=get_settings()
    return Envelope(data={'provider':s.ai_provider,'model':'mimo-v2.6-flash' if s.ai_provider=='mimo' else None,'key_configured':bool(s.mimo_api_key.get_secret_value()),'external_enabled':s.mimo_external_enabled,'max_bytes':10*1024*1024,'max_pdf_pages':s.ai_max_pdf_pages,'max_rows':100})


@router.post('',status_code=201)
def create(session:DB,service:Service,customer_id:Annotated[str,Form(min_length=1,max_length=64)],request_key:Annotated[UUID,Form()],
           text:Annotated[str|None,Form(max_length=30000)]=None,file:Annotated[UploadFile|None,File()]=None,external_authorized:Annotated[bool,Form()]=False):
    external_gate(external_authorized)
    try:
        if (file is not None)==bool(text and text.strip()):raise APIError(422,'ONE_INPUT_REQUIRED','请选择一份文件或粘贴一段文字，不能同时提交')
        if file:
            name=source_name(file.filename);suffix=PureWindowsPath(name).suffix.lower()
            kind={'.xlsx':'XLSX','.pdf':'PDF','.jpg':'IMAGE','.jpeg':'IMAGE','.png':'IMAGE'}.get(suffix)
            if kind is None:raise APIError(422,'UNSUPPORTED_INPUT','仅支持 XLSX、PDF、JPG、PNG 或粘贴文字')
            content=file.file.read(10*1024*1024+1)
        else:name='采购文字.txt';kind='TEXT';content=(text or '').encode('utf-8')
        if not content or len(content)>10*1024*1024:raise APIError(413,'INPUT_TOO_LARGE','输入为空或超过 10 MiB')
        with session.begin():draft,new=ai_drafts.start(session,customer_id,content,name,kind,str(request_key),get_settings().ai_provider,external_authorized)
        if not new:return Envelope(data=ai_drafts.view(draft))
        return run(session,draft,service)
    finally:
        if file:file.file.close()


@router.get('')
def listing(session:DB,page:Annotated[int,Query(ge=1)]=1,page_size:Annotated[int,Query(ge=1,le=50)]=20,customer_id:str|None=None):
    clauses=[] if customer_id is None else [AIExtraction.customer_id==customer_id]
    total=session.scalar(select(func.count()).select_from(AIExtraction).where(*clauses))
    items=session.scalars(select(AIExtraction).where(*clauses).order_by(AIExtraction.id.desc()).offset((page-1)*page_size).limit(page_size))
    return Envelope(data={'total':total,'page':page,'page_size':page_size,'items':[ai_drafts.view(d,False) for d in items]})


@router.get('/by-task/{task_id}')
def by_task(task_id:int,session:DB):
    draft=session.scalar(select(AIExtraction).where(AIExtraction.import_task_id==task_id))
    return Envelope(data=ai_drafts.view(draft,False) if draft else None)


@router.get('/{draft_id}')
def detail(draft_id:int,session:DB):return Envelope(data=ai_drafts.view(ai_drafts.get_draft(session,draft_id)))


@router.get('/{draft_id}/source')
def original(draft_id:int,session:DB):
    draft=ai_drafts.get_draft(session,draft_id)
    # Always download original bytes, never execute customer HTML/formulas inside this origin.
    return Response(content=draft.original_content,media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename=source_"+str(draft_id)+"; filename*=UTF-8''"+quote(draft.file_name,safe=''),'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})


@router.get('/{draft_id}/preview')
def visual_preview(draft_id:int,session:DB,source_id:Annotated[str,Query(max_length=128)]):
    draft=ai_drafts.get_draft(session,draft_id)
    document=prepare_input(draft.source_kind,draft.original_content,draft.file_name,get_settings().ai_max_pdf_pages)
    image=document.images.get(source_id)
    if image is None:raise APIError(404,'IMAGE_NOT_FOUND','该来源没有可预览图像')
    return Response(content=base64.b64decode(image.split(',',1)[1]),media_type='image/jpeg',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


@router.put('/{draft_id}')
def edit(draft_id:int,body:DraftEdit,session:DB):
    with session.begin():draft=ai_drafts.edit(session,draft_id,body)
    return Envelope(data=ai_drafts.view(draft))


class RetryBody(BaseModel):
    expected_revision:int=Field(ge=0)
    external_authorized:bool=False


@router.post('/{draft_id}/retry')
def retry(draft_id:int,body:RetryBody,session:DB,service:Service):
    external_gate(body.external_authorized)
    with session.begin():draft=ai_drafts.retry(session,draft_id,body.expected_revision,body.external_authorized,get_settings().ai_provider)
    return run(session,draft,service)


@router.post('/{draft_id}/confirm')
def confirm(draft_id:int,body:DraftConfirm,session:DB):
    with session.begin():draft,replayed=ai_drafts.confirm(session,draft_id,body)
    return Envelope(data={**ai_drafts.view(draft),'replayed':replayed})
