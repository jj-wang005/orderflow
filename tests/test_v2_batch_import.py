"""V2.0-B: real HTTP + rollback-only MySQL, no modifications to #211 or baseline data."""
import json
import os
from datetime import date,datetime,timedelta
from decimal import Decimal
from uuid import uuid4
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from app.main import app
from app.core.database import get_engine,get_db
from app.models import Base,SalesOrder,ImportTask,ImportTaskRow,CustomerConfig
from app.services.import_service import create_import_task,process_import_task
from app.services.http_import import submit_import
from app.services.excel_parser import ParseLimits
from tests.test_excel_parser import workbook_bytes
from tests.test_order_operations import draft,edit_body
from tests.test_order_status import change

pytestmark=pytest.mark.skipif(os.getenv('RUN_DB_TESTS')!='1',reason='Rollback-only MySQL')

def snapshot(c):
 return {name:[tuple(r) for r in c.execute(select(table).order_by(table.c.id))] for name,table in Base.metadata.tables.items()}

from tests.shared_fixtures import empty_api as api


def data(r,code=200):
 assert r.status_code==code,r.text
 return r.json()['data']

def configure(client,cid='D',**override):
 data(client.post('/api/v1/customers',json={'customer_id':cid,'customer_name':cid+' 隔离演示'}),201)
 body={'expected_version':0,'customer_name':cid+' 隔离演示','sheet_name':'Sheet1','header_row':1,'field_mapping':{'采购单号':'customer_order_no','客户料号':'customer_material_code','数量':'quantity'},'match_fields':['customer_order_no','customer_material_code'],'historical_quantity':None,'enabled':True};body.update(override)
 return data(client.put('/api/v1/customers/'+cid+'/config',json=body))

def excel(po='PO-01',quantity=12,material='MAT',*,extra=None):
 headers=['采购单号','客户料号','数量'];row=[po,material,quantity]
 if extra:headers+=['备注'];row+=[extra]
 return workbook_bytes(headers,[row])

def seed(conn,cid='D',po='PO-01',material='MAT',price='2.5',quantity=800):
 with Session(bind=conn,join_transaction_mode='create_savepoint',expire_on_commit=False) as s:
  order=SalesOrder(order_no='B-TEST-'+uuid4().hex,customer_id=cid,customer_order_no=po,customer_material_code=material,product_code='P-'+material,product_name='隔离模拟产品',unit_price=Decimal(price),quantity=Decimal(quantity),amount=Decimal(price)*quantity,status='VALID',source_type='SEED',created_at=datetime(2020,1,1))
  s.add(order);s.commit();return order.id

def upload(client,content=None,cid='D',key=None,confirm=False,filename='test.xlsx',retry=None):
 form={'customer_id':cid,'duplicate_confirmed':str(confirm).lower()}
 if retry:form['retry_of_task_id']=str(retry)
 return client.post('/api/v1/imports',data=form,files={'file':(filename,content if content is not None else excel())},headers={'Idempotency-Key':key} if key else {})

def rows(client,task):return data(client.get(f"/api/v1/imports/{task['task_id']}/rows"))['items']
def order(client,row):return data(client.get('/api/v1/orders/'+str(row['created_order_id'])))

def meta(cid='D',**kw):return {'slot_id':str(uuid4()),'customer_id':cid,'request_key':uuid4().hex,**kw}
def batch(client,contents,metadata=None,batchid=None,names=None):
 return client.post('/api/v1/import-batches',data={'batch_id':batchid or str(uuid4()),'items':json.dumps(metadata or [meta() for _ in contents])},files=[('files',((names or ['test.xlsx']*len(contents))[i],value)) for i,value in enumerate(contents)])

def review(client,task,row,decision='NEW',reference=None,existing=None,**kw):
 body={'request_key':str(uuid4()),'expected_revision':row.get('manual_revision',0),'decision':decision,'note':'测试隔离：销管核实采购归属','purchase_reference':reference or ('PROCUREMENT-'+uuid4().hex) if decision=='NEW' else None,'existing_order_id':existing};body.update(kw)
 return client.post(f"/api/v1/imports/{task['task_id']}/rows/{row['id']}/purchase-review",json=body)

def test_mixed_files_partial_success_and_parse_failure_isolation(api):
 c,conn=api;configure(c);seed(conn)
 mixed=workbook_bytes(['采购单号','客户料号','数量'],[['PO-01','MAT',12],['PO-02','MAT',12],['PO-03','MAT',-1]])
 result=data(batch(c,[mixed,b'not an xlsx',excel('PO-04')]))
 assert len(result['items'])==3 and all(i['task'] for i in result['items'])
 a,b,d=[i['task'] for i in result['items']]
 assert (a['success_rows'],a['unmatched_rows'],a['failed_rows'])==(1,1,1)
 assert b['status']=='FAILED' and b['error_message'] and b['total_rows']==0 and rows(c,b)==[]
 assert d['unmatched_rows']==1
 assert result['summary']['success_rows']==1 and result['summary']['file_error_count']==1
 assert (a['config_version'],b['config_version'])==(1,1)
 assert conn.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.customer_id=='D',SalesOrder.source_type=='AUTO'))==1

def test_batch_customer_required_and_no_filename_guessing(api):
 c,conn=api;configure(c);configure(c,'E');seed(conn);seed(conn,'E')
 before=snapshot(conn)
 r=batch(c,[excel()],[{'slot_id':str(uuid4()),'request_key':'key'}],names=['D.xlsx']);assert r.status_code==422 and snapshot(conn)==before
 result=data(batch(c,[excel(),excel()],[meta('D'),meta('E')],names=['E.xlsx','D.xlsx']))
 assert [r['task']['customer_id'] for r in result['items']]==['D','E']
 assert all(r['task']['success_rows']==1 for r in result['items'])

def test_invalid_customer_does_not_abort_next_file(api):
 c,conn=api;configure(c);seed(conn)
 result=data(batch(c,[excel(),excel()],[meta('DOES_NOT_EXIST'),meta('D')]))
 assert result['items'][0]['task'] is None and result['items'][0]['error']['code']=='CONFIG_ERROR'
 assert result['items'][1]['task']['success_rows']==1

def test_file_hash_precheck_filename_and_customer_scope(api):
 c,conn=api;configure(c);configure(c,'E');blob=excel();task=data(upload(c,blob,filename='one.xlsx'),201);before=snapshot(conn)
 def check(cid,content,name):return data(c.post('/api/v1/imports/duplicate-check',data={'customer_id':cid},files={'file':(name,content)}))
 assert check('D',blob,'renamed.xlsx')['previous']['task_id']==task['task_id']
 assert check('D',excel('PO-02'),'one.xlsx')['previous'] is None
 assert check('E',blob,'one.xlsx')['previous'] is None and snapshot(conn)==before
 assert upload(c,blob).status_code==409
 duplicate=data(upload(c,blob,confirm=True),201);assert duplicate['task_id']!=task['task_id']

def test_batch_request_replay_and_slot_binding(api):
 c,conn=api;configure(c);seed(conn);blob=excel();items=[meta()];bid=str(uuid4())
 first=data(batch(c,[blob],items,bid))['items'][0]['task'];before=snapshot(conn)
 replay=data(batch(c,[blob],items,bid))['items'][0]['task'];assert replay['task_id']==first['task_id'] and replay['idempotency_replayed'] and snapshot(conn)==before
 bad=data(batch(c,[excel('CHANGED')],items,bid));assert bad['items'][0]['error']['code']=='IDEMPOTENCY_CONFLICT'
 changed=[{**items[0],'request_key':uuid4().hex}];assert data(batch(c,[blob],changed,bid))['items'][0]['error']['code']=='BATCH_SLOT_CONFLICT'
 assert snapshot(conn)==before

def test_single_request_replay_works_before_duplicate_check(api):
 c,conn=api;configure(c);seed(conn);blob=excel();key=uuid4().hex
 first=data(upload(c,blob,key=key),201);before=snapshot(conn)
 replay=data(upload(c,blob,key=key));assert replay['task_id']==first['task_id'] and replay['idempotency_replayed']
 assert snapshot(conn)==before and upload(c,excel('DIFFERENT'),key=key).status_code==409

def test_same_source_row_cross_task_cannot_create_again(api):
 c,conn=api;configure(c);seed(conn);blob=excel();task=data(upload(c,blob),201);original=order(c,rows(c,task)[0])
 again=data(upload(c,blob,confirm=True),201);row=rows(c,again)[0]
 assert again['success_rows']==0 and again['review_rows']==1 and row['status']=='REVIEW_REQUIRED' and row['duplicate_order_id'] is None and row['created_order_id'] is None
 assert c.post(f"/api/v1/imports/{again['task_id']}/rows/{row['id']}/manual-order",json=draft()).status_code==409
 assert data(review(c,again,row,decision='EXISTING',existing=original['id']))['row']['status']=='DUPLICATE'

def test_legacy_source_null_keys_still_protect_baseline_orders(abc_api):
 c,conn=abc_api
 seed(conn,cid='A',po='ROLLBACK-LEGACY',material='LEGACY-MAT')
 blob=workbook_bytes(['采购订单号','物料/品项编码','订单数量'],[['ROLLBACK-LEGACY','LEGACY-MAT',12]])
 original=data(upload(c,blob,cid='A'),201);assert original['success_rows']==1
 from sqlalchemy import update
 conn.execute(update(SalesOrder).where(SalesOrder.import_task_id==original['task_id']).values(purchase_source_digest=None,purchase_reference_digest=None,purchase_group_digest=None))
 task=data(upload(c,blob,cid='A',confirm=True),201)
 assert task['review_rows']>=1 and task['duplicate_rows']==0
 assert all(r['created_order_id'] is None and r['duplicate_order_id'] is None for r in rows(c,task))

def test_cross_file_possible_duplicate_is_reviewed_not_guessed(api):
 c,conn=api;configure(c);seed(conn)
 first=data(upload(c,excel()),201);o=order(c,rows(c,first)[0])
 # Different bytes/extra column/quantity, same PO: never identify a line from qty or filename.
 second=data(upload(c,excel(quantity=3,extra='re-export')),201);row=rows(c,second)[0]
 assert row['status']=='REVIEW_REQUIRED' and second['review_rows']==1 and row['created_order_id'] is None
 ctx=data(c.get(f"/api/v1/imports/{second['task_id']}/rows/{row['id']}/purchase-review"));assert o['id'] in [x['id'] for x in ctx['candidates']]
 old=data(review(c,second,row,decision='EXISTING',existing=o['id']))['row'];assert old['status']=='DUPLICATE' and old['duplicate_order_id']==o['id']

def test_same_material_quantity_different_purchase_order_is_legal(api):
 c,conn=api;configure(c);seed(conn,po='PO-01');seed(conn,po='PO-02')
 a=data(upload(c,excel('PO-01')),201);b=data(upload(c,excel('PO-02')),201)
 assert a['success_rows']==b['success_rows']==1
 assert order(c,rows(c,a)[0])['id']!=order(c,rows(c,b)[0])['id']

def test_legal_new_procurement_same_keys_and_reference_uniqueness(api):
 c,conn=api;configure(c);seed(conn)
 first=data(upload(c,excel()),201);parent=order(c,rows(c,first)[0]);data(change(c,parent))
 task=data(upload(c,excel(quantity=7,extra='new procurement')),201);row=rows(c,task)[0];ref='VERIFIED-PROCUREMENT-002'
 new=data(review(c,task,row,reference=ref))['row'];assert new['status']=='SUCCESS'
 child=data(c.get('/api/v1/orders/'+str(new['created_order_id'])));assert child['source_order_id']==parent['id'] and child['amount']=='17.50'
 third=data(upload(c,excel(quantity=8,extra='other export')),201);r3=rows(c,third)[0]
 assert review(c,third,r3,reference=ref).status_code==409
 duplicate=data(review(c,third,r3,decision='EXISTING',existing=child['id']))['row'];assert duplicate['status']=='DUPLICATE' and duplicate['duplicate_order_id']==child['id']

def test_configured_batch_without_po_can_reach_matching_and_manual_completion(api):
 c,conn=api
 configure(c,field_mapping={'批次':'customer_batch','料号':'customer_material_code','数量':'quantity'},match_fields=['customer_batch','customer_material_code'])
 blob=workbook_bytes(['批次','料号','数量'],[['BATCH','MAT',12]])
 task=data(upload(c,blob),201);row=rows(c,task)[0];assert row['status']=='NOT_MATCHED'
 manual=data(c.post(f"/api/v1/imports/{task['task_id']}/rows/{row['id']}/manual-order",json={**draft(),'expected_revision':row['manual_revision']}))['order']
 assert manual['status']=='CREATED' and Decimal(manual['quantity'])==12

def test_retry_file_failure_links_and_preserves_original(api):
 c,conn=api;configure(c);seed(conn)
 failed=data(upload(c,b'broken',key=uuid4().hex),201);assert failed['total_rows']==0
 fixed=data(upload(c,excel(),retry=failed['task_id']),201)
 assert fixed['retry_of_task_id']==failed['task_id'] and fixed['success_rows']==1
 assert data(c.get('/api/v1/imports/'+str(failed['task_id'])))['error_message']==failed['error_message']
 assert data(c.get(f"/api/v1/imports/{fixed['task_id']}/related"))['parent']['task_id']==failed['task_id']
 assert data(c.get(f"/api/v1/imports/{failed['task_id']}/related"))['retries'][0]['task_id']==fixed['task_id']
 assert upload(c,excel('NEXT'),retry=fixed['task_id']).status_code==422

def test_pagination_filters_date_range_and_literal_filename(api):
 c,conn=api;configure(c);configure(c,'E')
 one=data(upload(c,excel(),filename='report%_literal.xlsx'),201)
 two=data(upload(c,excel('OTHER'),filename='report-other.xlsx'),201)
 data(upload(c,excel(),cid='E'),201)
 page=data(c.get('/api/v1/imports',params={'customer_id':'D','page_size':1}));assert page['total']==2 and page['items'][0]['task_id']==two['task_id']
 second=data(c.get('/api/v1/imports',params={'customer_id':'D','page_size':1,'page':2}));assert second['items'][0]['task_id']==one['task_id']
 assert data(c.get('/api/v1/imports',params={'file_name':'%_literal'}))['total']==1
 today=date.today().isoformat();assert data(c.get('/api/v1/imports',params={'customer_id':'D','date_from':today,'date_to':today,'status':'FAILED'}))['total']==2
 assert data(c.get('/api/v1/imports',params={'date_to':'2000-01-01'}))['total']==0
 assert c.get('/api/v1/imports',params={'date_from':'2026-02-02','date_to':'2026-01-01'}).status_code==422
 assert c.get('/api/v1/imports?page=0').status_code==422

def test_pending_snapshot_remains_stable_when_config_changes(api):
 c,conn=api;configure(c);seed(conn)
 with Session(bind=conn,join_transaction_mode='create_savepoint') as s:
  task_id=create_import_task(s,'D',excel(),file_name='snapshot.xlsx');s.commit()
 detail=data(c.get('/api/v1/customers/D'));cfg=detail['configuration']
 body={'expected_version':1,'customer_name':'D 修改名称','sheet_name':'Other','header_row':2,'field_mapping':{'批次':'customer_batch','数量':'quantity'},'match_fields':['customer_batch'],'historical_quantity':None,'enabled':True}
 data(c.put('/api/v1/customers/D/config',json=body))
 with Session(bind=conn,join_transaction_mode='create_savepoint') as s:
  result=process_import_task(s,task_id);assert result.success_rows==1 and result.config_version==1;s.commit()

def test_correction_manual_edit_status_and_new_input_chain(api):
 c,conn=api;configure(c)
 first=data(upload(c,excel('MANUAL',quantity=-1)),201);row=rows(c,first)[0]
 url=f"/api/v1/imports/{first['task_id']}/rows/{row['id']}/correction"
 corrected=data(c.post(url,json={'request_key':str(uuid4()),'expected_revision':0,'values':{'customer_order_no':'MANUAL','customer_material_code':'MAT','quantity':'12'},'note':'隔离测试修正采购数量'}))['row'];assert corrected['status']=='NOT_MATCHED'
 o=data(c.post(f"/api/v1/imports/{first['task_id']}/rows/{row['id']}/manual-order",json={**draft(),'expected_revision':1}))['order']
 edited=data(c.patch('/api/v1/orders/'+str(o['id']),json=edit_body(o)))['order'];before=conn.scalar(select(func.count()).select_from(SalesOrder));data(change(c,edited));assert conn.scalar(select(func.count()).select_from(SalesOrder))==before
 next_task=data(upload(c,excel('MANUAL',quantity=3,extra='next new procurement')),201);r=rows(c,next_task)[0];assert r['status']=='REVIEW_REQUIRED'
 r=data(review(c,next_task,r))['row'];assert r['status']=='SUCCESS'
 o2=data(c.get('/api/v1/orders/'+str(r['created_order_id'])));assert o2['source_order_id']==o['id']

@pytest.mark.parametrize('mutate',[{'purchase_reference':'short'},{'decision':'SQL'},{'note':'x'},{'expected_revision':-1},{'sql':'select 1'}])
def test_review_validation_and_no_writes(api,mutate):
 c,conn=api;configure(c);seed(conn);data(upload(c,excel()),201);t=data(upload(c,excel(quantity=3)),201);row=rows(c,t)[0];before=snapshot(conn)
 assert review(c,t,row,**mutate).status_code==422 and snapshot(conn)==before

def test_review_idempotency_and_invalid_cross_customer_link(api):
 c,conn=api;configure(c);configure(c,'E');seed(conn);other=seed(conn,'E')
 data(upload(c,excel()),201);t=data(upload(c,excel(quantity=3)),201);r=rows(c,t)[0];assert review(c,t,r,'EXISTING',existing=other).status_code==422
 key=str(uuid4());ref='PROCUREMENT-REVIEW-KEY'
 first=data(review(c,t,r,request_key=key,reference=ref));before=snapshot(conn)
 replay=data(review(c,t,r,request_key=key,reference=ref));assert replay['replayed'] and snapshot(conn)==before
 assert review(c,t,r,request_key=key,reference=ref,note='不同的说明文字').status_code==409

def test_manual_new_cannot_bypass_racing_business_guard(api):
 c,conn=api;configure(c)
 t1=data(upload(c,excel()),201);r1=rows(c,t1)[0]
 t2=data(upload(c,excel(quantity=3)),201);r2=rows(c,t2)[0]
 data(c.post(f"/api/v1/imports/{t1['task_id']}/rows/{r1['id']}/manual-order",json=draft()))
 before=snapshot(conn)
 assert c.post(f"/api/v1/imports/{t2['task_id']}/rows/{r2['id']}/manual-order",json=draft()).status_code==409 and snapshot(conn)==before

def test_baseline_xiaomi_is_preserved_without_use(api):
 c,conn=api;before=snapshot(conn)
 response=c.get('/api/v1/customers/xiaomi')
 if response.status_code==404:
  from app.core.config import get_settings
  assert get_settings().app_env=='test'  # New isolated baseline intentionally has ABC only.
 else:
  customer=data(response);assert customer['customer_name']=='小米公司' and customer['config_version']==1
 assert snapshot(conn)==before


def test_one_transaction_error_rolls_back_file_and_continues(api,monkeypatch):
 import app.api.import_batches as endpoint
 c,conn=api;configure(c);seed(conn,po='PO-01');seed(conn,po='PO-02');real=endpoint.submit_import
 def fail_after_write(*args,**kwargs):
  result=real(*args,**kwargs)
  if args[3]=='crash.xlsx':raise RuntimeError('private internal SQL details must not escape')
  return result
 monkeypatch.setattr(endpoint,'submit_import',fail_after_write)
 result=data(batch(c,[excel('PO-01'),excel('PO-02')],names=['crash.xlsx','good.xlsx']))
 assert result['items'][0]['error']['code']=='FILE_TRANSACTION_FAILED' and result['items'][1]['task']['success_rows']==1
 assert 'private internal' not in str(result)
 assert conn.scalar(select(func.count()).select_from(ImportTask).where(ImportTask.customer_id=='D'))==1
 assert conn.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.customer_id=='D',SalesOrder.source_type=='AUTO'))==1

@pytest.mark.parametrize('cid',['A','B','C'])
def test_abc_matching_safety_and_confirmed_auto_chain(abc_api,cid):
 c,conn=abc_api;config=data(c.get('/api/v1/customers/'+cid))['configuration'];mapping=config['customer']['field_mapping'];token='B-ABC-'+uuid4().hex[:12]
 values={'customer_order_no':token,'customer_material_code':'MAT-'+token,'customer_model':'MODEL-'+token,'customer_batch':token,'quantity':'501' if cid=='C' else '12'}
 with Session(bind=conn,join_transaction_mode='create_savepoint') as db:
  for variant in ['valid','wrong-safety','created','invalid']:
   fields={f:values[f] for f in ('customer_order_no','customer_material_code','customer_model','customer_batch')}
   qty=501 if cid=='C' else 800
   if variant=='wrong-safety':
    if cid=='A':fields['customer_material_code']='WRONG'
    if cid=='B':fields['customer_model']='WRONG'
    if cid=='C':qty=500
   ref=SalesOrder(order_no=uuid4().hex,customer_id=cid,**fields,product_code='RIGHT' if variant=='valid' else 'WRONG',product_name='隔离兼容产品',unit_price=Decimal('2.5'),quantity=Decimal(qty),amount=Decimal('2.5')*qty,status={'created':'CREATED','invalid':'INVALID'}.get(variant,'VALID'),source_type='SEED',created_at=datetime(2020,1,1))
   db.add(ref)
  db.commit()
 parent=None
 for n in range(3):
  # New file/input, same historical keys: review each additional legitimate procurement.
  blob=workbook_bytes([*mapping,'隔离采购说明'],[[*[values[f] for f in mapping.values()],str(n)]])
  task=data(upload(c,blob,cid=cid),201);row=rows(c,task)[0]
  if row['status']=='REVIEW_REQUIRED':row=data(review(c,task,row))['row']
  assert row['status']=='SUCCESS';o=data(c.get('/api/v1/orders/'+str(row['created_order_id'])))
  assert o['product_code']=='RIGHT' and o['amount']==str((Decimal(values['quantity'])*Decimal('2.5')).quantize(Decimal('.01')))
  if parent:assert o['source_order_id']==parent
  before=conn.scalar(select(func.count()).select_from(SalesOrder));data(change(c,o));assert conn.scalar(select(func.count()).select_from(SalesOrder))==before
  parent=o['id']


def test_order_edit_cannot_hide_a_procurement_scope_from_guard(api):
 c,conn=api;configure(c)
 t=data(upload(c,excel('OLD-PO')),201);r=rows(c,t)[0]
 o=data(c.post(f"/api/v1/imports/{t['task_id']}/rows/{r['id']}/manual-order",json=draft()))['order']
 body=edit_body(o);body['values']['customer_order_no']='CORRECTED-PO'
 data(c.patch('/api/v1/orders/'+str(o['id']),json=body))
 edited_export=data(upload(c,excel('CORRECTED-PO',extra='after order edit')),201)
 assert rows(c,edited_export)[0]['status']=='REVIEW_REQUIRED'
 original_export=data(upload(c,excel('OLD-PO',extra='old procurement metadata')),201)
 assert rows(c,original_export)[0]['status']=='REVIEW_REQUIRED'

from tests.shared_fixtures import abc_api
