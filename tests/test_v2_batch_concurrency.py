"""Committed competition runs only in a disposable isolated MySQL schema."""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,text,select,func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError,SQLAlchemyError
from app.core.database import get_engine,get_db
from app.main import app
from app.models import Base,SalesOrder,ImportTask,ImportTaskRow
from app.repositories.customer_configuration import initialize_configurations
from app.services.customer_configuration import read_configuration_file
from tests.test_v2_batch_import import excel,meta,batch,data

pytestmark=pytest.mark.skipif(os.getenv('RUN_DB_TESTS')!='1',reason='Isolated MySQL tests')

from tests.concurrency_fixtures import isolated_engine, client


def content(extra=None):
 from tests.test_excel_parser import workbook_bytes
 headers=['采购订单号','物料/品项编码','订单数量'];row=['PO-01','MAT',12]
 if extra:headers+=['备注'];row+=[extra]
 return workbook_bytes(headers,[row])

def compete(c,requests):
 barrier=Barrier(2)
 def run(options):barrier.wait(timeout=10);return data(batch(c,**options))
 with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(run,requests))

def test_real_same_request_competition_reuses_one_task(client):
 c,engine=client;bid=str(uuid4());items=[meta()];options={'contents':[content()],'metadata':items,'batchid':bid}
 result=compete(c,[options,options]);tasks=[r['items'][0]['task'] for r in result]
 assert all(tasks),result
 assert tasks[0]['task_id']==tasks[1]['task_id'] and sum(t['idempotency_replayed'] for t in tasks)==1
 with engine.connect() as db:
  assert db.scalar(select(func.count()).select_from(ImportTask))==1
  assert db.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.source_type=='AUTO'))==1

def test_real_different_tasks_same_source_claim_creates_one_order(client):
 c,engine=client;blob=content()
 result=compete(c,[{'contents':[blob],'metadata':[meta(duplicate_confirmed=True)]},{'contents':[blob],'metadata':[meta(duplicate_confirmed=True)]}])
 assert all(r['items'][0]['task'] for r in result),result
 with engine.connect() as db:
  assert db.scalar(select(func.count()).select_from(ImportTask))==2
  assert db.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.source_type=='AUTO'))==1
  assert sorted(db.scalars(select(ImportTaskRow.status)).all())==['REVIEW_REQUIRED','SUCCESS']

def test_real_cross_file_business_scope_is_serialized(client):
 c,engine=client
 result=compete(c,[{'contents':[content('first export')],'metadata':[meta()]},{'contents':[content('second export')],'metadata':[meta()]}])
 assert all(r['items'][0]['task'] for r in result),result
 with engine.connect() as db:
  assert db.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.source_type=='AUTO'))==1
  assert sorted(db.scalars(select(ImportTaskRow.status)).all())==['REVIEW_REQUIRED','SUCCESS']

def test_source_and_reference_unique_constraints_are_enforced(client):
 c,engine=client;result=data(batch(c,[content()]))
 assert result['items'][0]['task']['success_rows']==1
 with Session(engine) as db,db.begin():
  original=db.scalar(select(SalesOrder).where(SalesOrder.source_type=='AUTO'))
  original.purchase_reference_digest='a'*64;db.flush()
  values={col.name:getattr(original,col.name) for col in SalesOrder.__table__.c if col.name!='id'}
  for key in ['purchase_source_digest','purchase_reference_digest']:
   clone={**values,'order_no':uuid4().hex,'import_task_id':None,'source_sheet_name':None,'source_row_number':None,'purchase_source_digest':uuid4().hex*2,'purchase_reference_digest':uuid4().hex*2};clone[key]=values[key]
   with pytest.raises(IntegrityError):
    with db.begin_nested():db.add(SalesOrder(**clone));db.flush()
