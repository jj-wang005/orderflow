"""Real HTTP + committed races in disposable schemas only; no business DB writes."""
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from decimal import Decimal
import pytest
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from app.models import SalesOrder,ImportTask,ImportTaskRow
from app.services.purchase_guard import digest,source_key
from app.services.import_service import process_task_row
from tests.shared_fixtures import configured_abc as isolated
from tests.test_a_multi_material_guard import send,count_auto,CONFIGS
from tests.test_v2_batch_import import upload,data,rows
from tests.test_excel_parser import workbook_bytes

pytestmark=pytest.mark.skipif(os.getenv('RUN_DB_TESTS')!='1',reason='Disposable MySQL only')

def confirm(c,t,r,decision='NEW',existing=None,reference=None,key=None,**changes):
    body={'request_key':key or str(uuid4()),'expected_revision':r['manual_revision'],'decision':decision,
          'existing_order_id':existing,'purchase_reference':reference,'note':'隔离测试：已依据客户确认记录核实本次需求'}
    body.update(changes)
    return c.post(f"/api/v1/imports/{t['task_id']}/rows/{r['id']}/purchase-review",json=body)

def sample(cid):return Path(f'sample_files/{cid}客户采购订单模板.xlsx').read_bytes()

def blob(cid='C',note='first',second=False):
    cfg=CONFIGS[cid].customer
    a={'customer_order_no':'SAME-PO','customer_material_code':'MAT-1','customer_model':'MODEL-1','customer_batch':'SAME-BATCH','quantity':12}
    b={**a,'customer_material_code':'MAT-2'} if cid!='B' else {**a,'customer_model':'MODEL-2'}
    lines=[a,b] if second else [a]
    return workbook_bytes([*cfg.field_mapping,'测试需求说明'],[[*[v[f] for f in cfg.field_mapping.values()],note] for v in lines],sheet=cfg.sheet_name,header_row=cfg.header_row)

def history(e,cid='C',second=False,qty=800,status='VALID',price='2.5'):
    with Session(e) as s,s.begin():
        for i in range(2 if second else 1):
            unit=Decimal(price)
            s.add(SalesOrder(order_no=uuid4().hex,customer_id=cid,customer_order_no='SAME-PO',
                customer_material_code='MAT-'+str(i+1) if cid!='B' else 'MAT-1',customer_model='MODEL-'+str(i+1) if cid=='B' else 'MODEL-1',
                customer_batch='SAME-BATCH',product_code=f'SIM-{cid}-{i}',product_name='隔离模拟产品',
                quantity=Decimal(qty),unit_price=unit,amount=unit*qty,status=status,source_type='SEED'))

@pytest.mark.parametrize('cid,success,unmatched',[('A',8,1),('B',4,1),('C',7,1)])
def test_abc_first_then_same_file_requires_review_and_explicit_existing(isolated,cid,success,unmatched):
    c,e=isolated;content=sample(cid);first=send(c,content,cid=cid);original=rows(c,first)
    assert (first['success_rows'],first['unmatched_rows'],first['review_rows'])==(success,unmatched,0)
    again=send(c,content,cid=cid,confirm=True);new_rows=rows(c,again)
    assert (again['success_rows'],again['unmatched_rows'],again['review_rows'],again['duplicate_rows'])==(0,unmatched,success,0)
    for old,new in zip(original,new_rows):
        if old['status']!='SUCCESS':continue
        assert new['created_order_id'] is None and new['duplicate_order_id'] is None
        risk=new['purchase_review']['risk'];assert risk['candidates'] and old['created_order_id'] in [o['id'] for o in risk['candidates']]
        ctx=data(c.get(f"/api/v1/imports/{again['task_id']}/rows/{new['id']}/purchase-review"))
        assert {o['import_task_id'] for o in ctx['candidates']}=={first['task_id']}
        linked=data(confirm(c,again,new,'EXISTING',existing=old['created_order_id']))['row']
        assert linked['status']=='DUPLICATE' and linked['duplicate_order_id']==old['created_order_id']
        record=linked['purchase_review']['history'][-1]
        assert record['risk_snapshot'] and record['candidate_snapshot'] and record['result']['status']=='DUPLICATE'
        assert record['candidate_snapshot'][0]['quantity'] and record['candidate_snapshot'][0]['created_at']
    assert count_auto(e)==success and rows(c,first)==original

@pytest.mark.parametrize('cid',['A','B','C'])
def test_same_file_new_without_fabricated_reference_creates_independent_order(isolated,cid):
    c,e=isolated;history(e,cid);content=blob(cid)
    first=send(c,content,cid=cid);second=send(c,content,cid=cid,confirm=True);r=rows(c,second)[0]
    assert r['status']=='REVIEW_REQUIRED'
    key=str(uuid4());result=data(confirm(c,second,r,key=key))
    assert result['row']['status']=='SUCCESS' and not result['replayed']
    replay=data(confirm(c,second,r,key=key));assert replay['replayed'] and replay['row']['created_order_id']==result['row']['created_order_id']
    assert confirm(c,second,r).status_code==409
    with Session(e) as s:
        orders=s.scalars(select(SalesOrder).where(SalesOrder.source_type=='AUTO').order_by(SalesOrder.id)).all()
        assert len(orders)==2 and orders[0].purchase_source_digest!=orders[1].purchase_source_digest
        assert all(o.purchase_reference_digest is None and o.status=='CREATED' and o.source_order_id for o in orders)
        assert all(o.amount==Decimal('30') for o in orders)
        for o in orders:
            t=s.get(ImportTask,o.import_task_id);row=s.scalar(select(ImportTaskRow).where(ImportTaskRow.created_order_id==o.id))
            assert o.purchase_source_digest==source_key(t,row)
            if o.import_task_id==second['task_id']:
                assert row.purchase_review['current']['result']['created_order_id']==o.id
                assert row.purchase_review['history'][-1]['note'] and row.purchase_review['current']['candidate_snapshot']
        assert result['row']['purchase_review']['current']['purchase_reference'] is None
        assert result['row']['purchase_review']['current']['result']['created_order_id']==orders[-1].id
        with s.begin_nested():
            process_task_row(s,second['task_id'],r['sheet_name'],r['row_number'])
            assert s.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.source_type=='AUTO'))==2

@pytest.mark.parametrize('cid',['A','B','C'])
def test_business_combinations_allow_multiple_products_in_same_po_or_batch(isolated,cid):
    c,e=isolated;history(e,cid,second=True)
    t=send(c,blob(cid,second=True),cid=cid)
    assert (t['success_rows'],t['review_rows'])==(2,0) and count_auto(e)==2
    with Session(e) as s:assert len(set(s.scalars(select(SalesOrder.product_code).where(SalesOrder.source_type=='AUTO'))))==2
    next_task=send(c,blob(cid,note='another channel',second=True),cid=cid)
    assert next_task['review_rows']==2 and next_task['duplicate_rows']==0
    for old,new in zip(rows(c,t),rows(c,next_task)):
        ctx=data(c.get(f"/api/v1/imports/{next_task['task_id']}/rows/{new['id']}/purchase-review"))
        assert [o['id'] for o in ctx['candidates']]==[old['created_order_id']]

@pytest.mark.parametrize('kind',['v1','null'])
def test_legacy_source_keys_unchanged_but_new_task_can_be_verified_new(isolated,kind):
    c,e=isolated;history(e);content=blob();first=send(c,content,cid='C');oid=rows(c,first)[0]['created_order_id']
    with Session(e) as s,s.begin():
        t=s.get(ImportTask,first['task_id']);o=s.get(SalesOrder,oid)
        o.purchase_source_digest=digest(['C',t.file_hash,o.source_sheet_name,o.source_row_number]) if kind=='v1' else None
        legacy=o.purchase_source_digest
    again=send(c,content,cid='C',confirm=True);r=rows(c,again)[0]
    assert r['status']=='REVIEW_REQUIRED' and data(confirm(c,again,r))['row']['status']=='SUCCESS'
    with Session(e) as s:assert s.get(SalesOrder,oid).purchase_source_digest==legacy

@pytest.mark.parametrize('qty,expected',[(499,'NOT_MATCHED'),(800,'SUCCESS')])
def test_c_uses_history_quantity_and_seed_is_not_processed_purchase(isolated,qty,expected):
    c,e=isolated;history(e,qty=qty)
    t=send(c,blob(),cid='C');assert rows(c,t)[0]['status']==expected and t['review_rows']==0

@pytest.mark.parametrize('invalid',['CREATED','INVALID','ZERO','CONFLICT'])
def test_new_review_cannot_bypass_history_safety(isolated,invalid):
    c,e=isolated;history(e);send(c,blob(),cid='C')
    t=send(c,blob(note='later demand'),cid='C');r=rows(c,t)[0]
    with Session(e) as s,s.begin():
        base=s.scalar(select(SalesOrder).where(SalesOrder.customer_id=='C',SalesOrder.customer_batch=='SAME-BATCH',SalesOrder.source_type=='SEED'))
        if invalid in {'CREATED','INVALID'}:base.status=invalid
        elif invalid=='ZERO':base.unit_price=0;base.amount=0
        else:
            s.add(SalesOrder(order_no=uuid4().hex,customer_id='C',customer_batch='SAME-BATCH',customer_material_code='MAT-1',
                product_code='OTHER',product_name='隔离冲突产品',quantity=800,unit_price=3,amount=2400,status='VALID',source_type='SEED'))
    result=data(confirm(c,t,r))['row']
    assert result['status'] in {'NOT_MATCHED','INVALID_DATA','AMBIGUOUS'} and result['created_order_id'] is None and count_auto(e)==1

def test_stable_optional_reference_is_unique_and_never_silently_changes_new_to_existing(isolated):
    c,e=isolated;history(e);send(c,blob(),cid='C')
    two=send(c,blob(note='second'),cid='C');r=rows(c,two)[0]
    assert data(confirm(c,two,r,reference='CUSTOMER-REAL-LINE-002'))['row']['status']=='SUCCESS'
    three=send(c,blob(note='third'),cid='C');r3=rows(c,three)[0]
    response=confirm(c,three,r3,reference='CUSTOMER-REAL-LINE-002')
    assert response.status_code==409 and response.json()['code']=='PURCHASE_REFERENCE_EXISTS'
    assert rows(c,three)[0]['status']=='REVIEW_REQUIRED' and rows(c,three)[0]['duplicate_order_id'] is None and count_auto(e)==2

def test_existing_rejects_seed_other_customer_and_wrong_fields(isolated):
    c,e=isolated;history(e);history(e,'B');first=send(c,blob(),cid='C');t=send(c,blob(note='second'),cid='C');r=rows(c,t)[0]
    with Session(e) as s:seed_id=s.scalar(select(SalesOrder.id).where(SalesOrder.customer_id=='C',SalesOrder.source_type=='SEED'))
    assert confirm(c,t,r,'EXISTING',existing=seed_id).status_code==422
    wrong=send(c,blob('B'),cid='B');assert confirm(c,t,r,'EXISTING',existing=rows(c,wrong)[0]['created_order_id']).status_code==422
    assert count_auto(e)==2

@pytest.mark.parametrize('cid,field',[('B','customer_model'),('C','customer_material_code')])
def test_missing_required_legacy_key_is_risk_but_not_selectable_proof(isolated,cid,field):
    c,e=isolated;history(e,cid);first=send(c,blob(cid),cid=cid);oid=rows(c,first)[0]['created_order_id']
    with Session(e) as s,s.begin():setattr(s.get(SalesOrder,oid),field,None)
    t=send(c,blob(cid,note='legacy incomplete record'),cid=cid);r=rows(c,t)[0]
    assert r['status']=='REVIEW_REQUIRED'
    ctx=data(c.get(f"/api/v1/imports/{t['task_id']}/rows/{r['id']}/purchase-review"))
    assert ctx['candidates']==[] and confirm(c,t,r,'EXISTING',existing=oid).status_code==422
    assert count_auto(e)==1

def test_different_confirmation_keys_cannot_race_to_create_same_task_row_twice(isolated):
    c,e=isolated;history(e);send(c,blob(),cid='C');t=send(c,blob(),cid='C',confirm=True);r=rows(c,t)[0];barrier=Barrier(2)
    def run(i):
        barrier.wait(timeout=20)
        return confirm(c,t,r,key=str(uuid4())).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(run,[0,1]))
    assert sorted(codes)==[200,409] and count_auto(e)==2

@pytest.mark.parametrize('cid,field',[('B','customer_model'),('C','customer_material_code')])
def test_order_edit_does_not_hide_original_combination_or_mutate_snapshot(isolated,cid,field):
    c,e=isolated;history(e,cid);first=send(c,blob(cid),cid=cid);r=rows(c,first)[0]
    with Session(e) as s,s.begin():
        order=s.get(SalesOrder,r['created_order_id']);setattr(order,field,'EDITED-OTHER');order.edit_revision+=1
    t=send(c,blob(cid,note='original procurement fields'),cid=cid)
    assert rows(c,t)[0]['status']=='REVIEW_REQUIRED' and count_auto(e)==1
    assert rows(c,first)[0]['normalized_data']==r['normalized_data']

@pytest.mark.parametrize('case',['same_request','same_file_new_task','different_file_new_task','same_review'])
def test_committed_races_preserve_customer_lock_and_confirmation_idempotency(isolated,case):
    c,e=isolated;history(e);barrier=Barrier(2);key=uuid4().hex
    if case=='same_review':
        send(c,blob(),cid='C');t=send(c,blob(),cid='C',confirm=True);r=rows(c,t)[0]
    def run(i):
        barrier.wait(timeout=20)
        if case=='same_review':return data(confirm(c,t,r,key=key))
        content=blob(note='other file') if i and case=='different_file_new_task' else blob()
        response=upload(c,content,cid='C',confirm=True,key=key if case=='same_request' else uuid4().hex)
        assert response.status_code in (200,201),response.text
        return response.json()['data']
    with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(run,[0,1]))
    if case=='same_review':
        assert result[0]['row']['created_order_id']==result[1]['row']['created_order_id'] and sum(x['replayed'] for x in result)==1 and count_auto(e)==2
    elif case=='same_request':assert result[0]['task_id']==result[1]['task_id'] and count_auto(e)==1
    else:
        assert result[0]['task_id']!=result[1]['task_id'] and count_auto(e)==1
        assert sorted(rows(c,t)[0]['status'] for t in result)==['REVIEW_REQUIRED','SUCCESS']
