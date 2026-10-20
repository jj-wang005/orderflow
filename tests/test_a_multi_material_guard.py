"""A-only review narrowing; every committed write is in a disposable schema."""
import os,json
from pathlib import Path
from decimal import Decimal
from uuid import uuid4
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from app.main import app
from app.core.database import get_db
from app.models import SalesOrder,ImportTask,ImportTaskRow
from scripts.demo_dataset import seed as seed_demo
from tests.db_isolation import temporary_schema_engine
from tests.test_excel_parser import workbook_bytes
from tests.test_v2_batch_import import upload,data,rows,review
from app.services.customer_configuration import read_configuration_file
from app.repositories.customer_configuration import initialize_configurations

pytestmark=pytest.mark.skipif(os.getenv('RUN_DB_TESTS')!='1',reason='Explicit disposable MySQL only')
CONFIGS={c.customer.customer_id:c for c in read_configuration_file()}

from tests.shared_fixtures import configured_abc as isolated


def blob(lines,cid='A',note=None):
 config=CONFIGS['A' if cid=='D' else cid].customer
 headers=list(config.field_mapping)
 data_rows=[[{'customer_order_no':po,'customer_material_code':mat,'customer_model':'MODEL','customer_batch':'BATCH','quantity':qty}[f] for f in config.field_mapping.values()] for po,mat,qty in lines]
 if note:
  headers+=['隔离验证备注']
  for row in data_rows:row.append(note)
 return workbook_bytes(headers,data_rows,sheet=config.sheet_name,header_row=config.header_row)

def references(engine,cid='A',po='SAFE-PO',materials=('MAT1','MAT2','MAT3')):
 with Session(engine) as s,s.begin():
  if cid=='D':
   config=CONFIGS['A'].model_copy(deep=True);config.customer.customer_id='D';config.customer.customer_name='D隔离对照'
   initialize_configurations(s,[config])
  for mat in materials:
   s.add(SalesOrder(order_no='A-FIX-'+uuid4().hex,customer_id=cid,customer_order_no=po,customer_material_code=mat,customer_model='MODEL',customer_batch='BATCH',
    product_code='SIM-'+mat if cid!='B' else 'SIM-B',product_name='隔离模拟产品',quantity=Decimal(800),unit_price=Decimal('2.5'),amount=Decimal(2000),status='VALID',source_type='SEED'))

def send(c,content,cid='A',**kw):return data(upload(c,content,cid=cid,**kw),201)
def count_auto(engine):
 with engine.connect() as db:return db.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.source_type=='AUTO'))

def test_original_a_first_import_is_8_success_1_unmatched_no_review(isolated, tmp_path):
 c,engine=isolated
 task=send(c,Path('sample_files/A客户采购订单模板.xlsx').read_bytes());rs=rows(c,task)
 assert (task['success_rows'],task['unmatched_rows'],task['review_rows'],task['failed_rows'])==(8,1,0,0)
 assert [r['row_number'] for r in rs if r['status']=='NOT_MATCHED']==[10] and count_auto(engine)==8
 receipt={'scope':'disposable schema; original Excel unchanged','success':8,'not_matched':1,'review_required':0,
  'rows':[{'excel_row':r['row_number'],'status':r['status'],'material':r['normalized_data']['customer_material_code']} for r in rs]}
 (tmp_path/'sample_result.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')

def test_a_same_po_different_materials_in_one_file_are_legal(isolated):
 c,e=isolated;references(e)
 task=send(c,blob([('SAFE-PO','MAT1',12),('SAFE-PO','MAT2',12)]))
 assert task['success_rows']==2 and task['review_rows']==0 and count_auto(e)==2

def test_a_same_po_same_material_in_one_file_needs_review_even_quantity_changes(isolated):
 c,e=isolated;references(e)
 task=send(c,blob([('SAFE-PO','MAT1',12),('SAFE-PO','MAT1',13)]))
 assert task['success_rows']==1 and task['review_rows']==1 and count_auto(e)==1

def test_a_file_duplicate_prompt_idempotency_and_exact_source_claim_unchanged(isolated):
 c,e=isolated;references(e);content=blob([('SAFE-PO','MAT1',12)]);key=uuid4().hex
 first=send(c,content,key=key)
 assert upload(c,content,cid='A').status_code==409
 replay=data(upload(c,content,cid='A',key=key),200);assert replay['task_id']==first['task_id'] and replay['idempotency_replayed']
 again=send(c,content,confirm=True);r=rows(c,again)[0]
 assert r['status']=='REVIEW_REQUIRED' and r['duplicate_order_id'] is None and count_auto(e)==1
 linked=data(review(c,again,r,decision='EXISTING',existing=rows(c,first)[0]['created_order_id']))['row']
 assert linked['status']=='DUPLICATE' and count_auto(e)==1

def test_a_cross_task_same_po_same_material_stays_review(isolated):
 c,e=isolated;references(e)
 send(c,blob([('SAFE-PO','MAT1',12)],note='first'))
 task=send(c,blob([('SAFE-PO','MAT1',20)],note='new file, quantity differs'))
 assert rows(c,task)[0]['status']=='REVIEW_REQUIRED' and count_auto(e)==1

def test_a_cross_task_different_material_and_candidate_list_are_narrowed(isolated):
 c,e=isolated;references(e)
 one=send(c,blob([('SAFE-PO','MAT1',12)]));two=send(c,blob([('SAFE-PO','MAT2',12)]))
 assert one['success_rows']==two['success_rows']==1
 third=send(c,blob([('SAFE-PO','MAT2',13)]));r=rows(c,third)[0]
 context=data(c.get(f"/api/v1/imports/{third['task_id']}/rows/{r['id']}/purchase-review"))
 assert [x['id'] for x in context['candidates']]==[rows(c,two)[0]['created_order_id']]
 assert review(c,third,r,decision='EXISTING',existing=rows(c,one)[0]['created_order_id']).status_code==422
 assert count_auto(e)==2

def test_partial_old_task_stays_unchanged_when_explicit_new_task_imports_remaining(isolated,monkeypatch):
 c,e=isolated;content=Path('sample_files/A客户采购订单模板.xlsx').read_bytes()
 import app.services.purchase_guard as guard
 # Reproduce the previous PO-wide behavior only in this disposable test process.
 with monkeypatch.context() as old:
  old.setattr(guard,'_a_material_may_duplicate',lambda *args:True)
  first=send(c,content)
 assert (first['success_rows'],first['review_rows'])==(5,4)
 old_rows=rows(c,first);old_task=data(c.get('/api/v1/imports/'+str(first['task_id'])))
 second=send(c,content,confirm=True)
 assert (second['duplicate_rows'],second['success_rows'],second['unmatched_rows'],second['review_rows'])==(0,3,1,5)
 assert rows(c,first)==old_rows and data(c.get('/api/v1/imports/'+str(first['task_id'])))==old_task
 assert count_auto(e)==8

@pytest.mark.parametrize('incoming',['MAT1','MAT2'])
def test_a_edit_cannot_hide_original_or_current_material(isolated,incoming):
 c,e=isolated;references(e);first=send(c,blob([('SAFE-PO','MAT1',12)]));oid=rows(c,first)[0]['created_order_id']
 with Session(e) as s,s.begin():
  o=s.get(SalesOrder,oid);o.customer_material_code='MAT2';o.edit_revision+=1
 task=send(c,blob([('SAFE-PO',incoming,13)],note='after edit'))
 assert rows(c,task)[0]['status']=='REVIEW_REQUIRED' and count_auto(e)==1

def test_a_legacy_null_group_retains_original_snapshot_protection(isolated):
 c,e=isolated;references(e);first=send(c,blob([('SAFE-PO','MAT1',12)]));oid=rows(c,first)[0]['created_order_id']
 with Session(e) as s,s.begin():s.get(SalesOrder,oid).purchase_group_digest=None
 different=send(c,blob([('SAFE-PO','MAT2',12)]));same=send(c,blob([('SAFE-PO','MAT1',13)]))
 assert different['success_rows']==1 and rows(c,same)[0]['status']=='REVIEW_REQUIRED' and count_auto(e)==2

def test_a_unknown_original_material_is_not_evidence_of_new_procurement(isolated):
 c,e=isolated;references(e);first=send(c,blob([('SAFE-PO','MAT1',12)]));r=rows(c,first)[0]
 with Session(e) as s,s.begin():
  row=s.get(ImportTaskRow,r['id']);row.normalized_data={**row.normalized_data,'customer_material_code':None}
 task=send(c,blob([('SAFE-PO','MAT2',12)]))
 assert rows(c,task)[0]['status']=='REVIEW_REQUIRED' and count_auto(e)==1

def test_a_void_does_not_release_source_or_same_material_review(isolated):
 c,e=isolated;references(e);content=blob([('SAFE-PO','MAT1',12)]);first=send(c,content);oid=rows(c,first)[0]['created_order_id']
 data(c.post(f'/api/v1/orders/{oid}/void',json={'request_key':str(uuid4()),'expected_revision':0,'confirmed':True,'note':'隔离作废验证'}))
 same=send(c,content,confirm=True);other=send(c,blob([('SAFE-PO','MAT1',13)]));different=send(c,blob([('SAFE-PO','MAT2',12)]))
 assert rows(c,same)[0]['status']=='REVIEW_REQUIRED' and rows(c,other)[0]['status']=='REVIEW_REQUIRED'
 assert different['success_rows']==1 and count_auto(e)==2

@pytest.mark.parametrize('cid',['B','D'])
def test_other_customers_keep_same_po_broad_review(isolated,cid):
 c,e=isolated;references(e,cid=cid)
 task=send(c,blob([('SAFE-PO','MAT1',12),('SAFE-PO','MAT2',12)],cid=cid),cid=cid)
 assert task['success_rows']==1 and task['review_rows']==1 and count_auto(e)==1

def test_c_missing_po_first_import_obeys_configured_batch_rule(isolated):
 c,e=isolated;task=send(c,Path('sample_files/C客户采购订单模板.xlsx').read_bytes(),cid='C')
 assert (task['success_rows'],task['unmatched_rows'],task['review_rows'],task['failed_rows'])==(7,1,0,0)
 assert rows(c,task)[-1]['status']=='NOT_MATCHED' and count_auto(e)==7

@pytest.mark.parametrize('case',['different_materials','same_material','same_source','same_request'])
def test_a_real_commit_races_keep_customer_lock_and_unique_claims(isolated,case):
 c,e=isolated;references(e);barrier=Barrier(2);key=uuid4().hex
 left=blob([('SAFE-PO','MAT1',12)],note='left')
 right=blob([('SAFE-PO','MAT2' if case=='different_materials' else 'MAT1',13)],note='right')
 if case in ('same_source','same_request'):right=left
 def run(index):
  barrier.wait(timeout=10)
  response=upload(c,[left,right][index],cid='A',confirm=True,key=key if case=='same_request' else uuid4().hex)
  assert response.status_code in (200,201),response.text
  return response.json()['data']
 with ThreadPoolExecutor(max_workers=2) as pool:tasks=list(pool.map(run,[0,1]))
 if case=='different_materials':assert count_auto(e)==2 and all(t['success_rows']==1 for t in tasks)
 elif case=='same_request':assert tasks[0]['task_id']==tasks[1]['task_id'] and count_auto(e)==1
 else:
  assert count_auto(e)==1
  states=[r['status'] for t in tasks for r in rows(c,t)]
  assert sorted(states)==sorted(['SUCCESS','REVIEW_REQUIRED'])
