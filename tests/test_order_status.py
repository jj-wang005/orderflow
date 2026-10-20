"""Task 3 HTTP lifecycle and three-generation reorders: every write is rolled back."""
import json
import os
from decimal import Decimal
from uuid import uuid4
from pathlib import Path
import pytest
from sqlalchemy import select, event
from sqlalchemy.orm import Session
from app.models import SalesOrder, OrderChange
from app.services.matching_engine import match_purchase_row
from app.schemas.purchase_row import NormalizedPurchaseRow
from tests.test_api import upload, data, snapshot
from tests.order_edit_isolation import api,operation_engine
from tests.test_excel_parser import workbook_bytes, CONFIGS
from tests.test_order_operations import draft, edit_body

pytestmark = pytest.mark.skipif(os.getenv('RUN_DB_TESTS') != '1', reason='Rollback-only MySQL')

@pytest.fixture
def concurrent_client():
    # Resolve at fixture runtime to avoid the B suite's existing change() import cycle.
    from contextlib import contextmanager
    from tests.concurrency_fixtures import isolated_engine,client
    with contextmanager(isolated_engine.__wrapped__)() as engine:
        yield from client.__wrapped__(engine)

def content(cid, key, quantity, **overrides):
    c=CONFIGS[cid].customer
    values=dict(customer_order_no=key,customer_material_code='MAT-'+key,customer_model='MODEL-'+key,customer_batch=key,quantity=quantity)
    values.update(overrides)
    return workbook_bytes(list(c.field_mapping),[[values[f] for f in c.field_mapping.values()]],sheet=c.sheet_name,header_row=c.header_row)

def purchase(client,cid,key,quantity,**overrides):
    task=data(client.post('/api/v1/imports',data={'customer_id':cid,'duplicate_confirmed':'true'},files={'file':('test.xlsx',content(cid,key,quantity,**overrides))}),201)
    row=data(client.get(f"/api/v1/imports/{task['task_id']}/rows"))['items'][0]
    if row['status']=='REVIEW_REQUIRED':
        from tests.test_v2_batch_import import review
        row=data(review(client,task,row))['row']
    return task,row

def new_order(client,cid='A',quantity='10'):
    key='T3-'+uuid4().hex[:18]
    task,row=purchase(client,cid,key,quantity)
    assert row['status']=='NOT_MATCHED'
    body=draft();body['product']={'product_code':'P-'+key,'product_name':'隔离测试产品','unit_price':'2.5000'}
    body['expected_revision']=row['manual_revision']
    body.pop('simulation_confirmed');body['verification_basis']=None
    o=data(client.post(f"/api/v1/imports/{task['task_id']}/rows/{row['id']}/manual-order",json=body))['order']
    return key,o

def status_body(order,target='VALID',**overrides):
    return dict(request_key=str(uuid4()),expected_revision=order['edit_revision'],status=target,**overrides)

def change(client,order,target='VALID',body=None):
    return client.patch(f"/api/v1/orders/{order['id']}/status",json=body or status_body(order,target))

def test_forward_lifecycle_versions_audit_and_filter(api):
    client,conn=api;key,o=new_order(client)
    for revision,(target,label) in enumerate([('VALID','已确认'),('PROCESSING','进行中'),('SHIPPED','已发货'),('COMPLETED','已完成')],1):
        old=o;o=data(change(client,o,target))['order']
        assert o['status']==target and o['status_description']==label and o['edit_revision']==revision
        assert o['source_order_id'] is None and o['amount']==old['amount']
        assert data(client.get(f"/api/v1/orders/{o['id']}"))['status']==target
        page=data(client.get('/api/v1/orders',params={'status':target,'customer_id':'A','page_size':100}))
        assert any(x['id']==o['id'] for x in page['items']) and all(x['status']==target for x in page['items'])
        assert client.patch(f"/api/v1/orders/{o['id']}",json=edit_body(o)).status_code==409
    history=data(client.get(f"/api/v1/orders/{o['id']}/edit"))['history']
    transitions=[(h['before_data']['status'],h['after_data']['status']) for h in history if h['before_data']]
    assert transitions==[('SHIPPED','COMPLETED'),('PROCESSING','SHIPPED'),('VALID','PROCESSING'),('CREATED','VALID')]
    assert all(h['actor_label']=='本地销管（未鉴权）' for h in history)

def test_replay_noop_key_conflict_and_stale_version(api):
    client,conn=api;key,o=new_order(client);body=status_body(o);confirmed=data(change(client,o,body=body))['order'];before=snapshot(conn)
    assert data(change(client,o,body=body))['replayed'] and snapshot(conn)==before
    assert data(change(client,o))['replayed'] and snapshot(conn)==before
    assert change(client,o,body={**body,'status':'PROCESSING'}).status_code==409
    assert change(client,o,'PROCESSING').status_code==409 and snapshot(conn)==before
    assert data(change(client,confirmed,'PROCESSING'))['order']['edit_revision']==2

@pytest.mark.parametrize('target',['SHIPPED','COMPLETED','PROCESSING'])
def test_skip_transition_refused(api,target):
    client,conn=api;_,o=new_order(client);before=snapshot(conn)
    assert change(client,o,target).status_code==409 and snapshot(conn)==before

@pytest.mark.parametrize('target',['INVALID','OTHER','valid','',None])
def test_invalid_status_refused(api,target):
    client,conn=api;_,o=new_order(client);before=snapshot(conn)
    assert change(client,o,target).status_code==422 and snapshot(conn)==before

def test_seed_unknown_and_backward_refused(api):
    client,conn=api;seed=conn.scalar(select(SalesOrder.id).where(SalesOrder.source_type=='SEED'))
    assert change(client,{'id':seed,'edit_revision':0}).status_code==409
    assert change(client,{'id':99999999,'edit_revision':0}).status_code==404
    _,o=new_order(client);o=data(change(client,o))['order'];assert change(client,o,'CREATED').status_code==409

@pytest.mark.parametrize('field,value',[('unit_price',0),('quantity',0),('product_name',''),('amount',999),('customer_material_code',None),('product_code',' P-001 '),('customer_order_no',' ORDER ')])
def test_confirmation_revalidates_order(api,field,value):
    client,conn=api;_,o=new_order(client)
    conn.execute(SalesOrder.__table__.update().where(SalesOrder.id==o['id']).values(**{field:value}));before=snapshot(conn)
    assert change(client,o).status_code==422 and snapshot(conn)==before

def test_status_audit_failure_rolls_back(api):
    client,conn=api;_,o=new_order(client);before=snapshot(conn)
    def fail(*args):raise RuntimeError('isolated audit failure')
    event.listen(OrderChange,'before_insert',fail)
    try:response=change(client,o)
    finally:event.remove(OrderChange,'before_insert',fail)
    assert response.status_code==500 and snapshot(conn)==before

def test_edit_and_status_share_one_revision(api):
    client,conn=api;_,o=new_order(client);old=status_body(o)
    edited=data(client.patch(f"/api/v1/orders/{o['id']}",json=edit_body(o)))['order']
    assert change(client,o,body=old).status_code==409
    assert data(change(client,edited))['order']['edit_revision']==2

def test_reference_changed_before_confirmation_is_rejected(api):
    client,conn=api;key,o=new_order(client);data(change(client,o))
    _,row=purchase(client,'A',key,'3');second=data(client.get(f"/api/v1/orders/{row['created_order_id']}"))
    conn.execute(SalesOrder.__table__.update().where(SalesOrder.id==o['id']).values(status='INVALID'))
    before=snapshot(conn);assert change(client,second).status_code==409 and snapshot(conn)==before

def test_two_connections_serialize_status_writers_with_full_rollback(concurrent_client):
    from app.core.database import get_engine
    from app.services.order_status import update_status
    from app.schemas.order_status import StatusRequest
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from tests.test_v2_batch_import import upload as upload_isolated,rows as task_rows
    from tests.test_v2_batch_concurrency import content as isolated_content
    client,engine=concurrent_client
    task=data(upload_isolated(client,isolated_content()),201);created_id=task_rows(client,task)[0]['created_order_id']
    ready=Event();waiting=Event();release=Event();finished=Event()
    with engine.connect() as c:
        before=snapshot(c)
        o=c.execute(select(SalesOrder.__table__).where(SalesOrder.id==created_id)).mappings().first()
        assert o is not None
    body=StatusRequest.model_validate(dict(request_key=str(uuid4()),expected_revision=o['edit_revision'],status='VALID'))
    def watch(c,cursor,sql,params,context,many):
        if c.info.get('task3_worker')=='second' and 'FOR UPDATE' in sql and 'FROM customer_config' in sql:waiting.set()
    def run(worker):
        try:
            with engine.connect() as c:
                outer=c.begin();c.info['task3_worker']=worker
                try:
                    with Session(bind=c,join_transaction_mode='create_savepoint') as s:
                        result=update_status(s,o['id'],body)
                        if worker=='first':ready.set();assert release.wait(10)
                        return result['order'].status
                finally:outer.rollback();c.info.pop('task3_worker',None)
        finally:
            if worker=='second':finished.set()
    event.listen(engine,'before_cursor_execute',watch)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(run,'first');assert ready.wait(10);second=pool.submit(run,'second')
            try:assert waiting.wait(10) and not finished.wait(.2)
            finally:release.set()
            assert first.result(timeout=15)==second.result(timeout=15)=='VALID'
    finally:release.set();event.remove(engine,'before_cursor_execute',watch)
    with engine.connect() as c:assert snapshot(c)==before

@pytest.mark.parametrize('source',['SEED','AUTO'])
@pytest.mark.parametrize('state',['CREATED','VALID','PROCESSING','SHIPPED','COMPLETED','INVALID'])
def test_history_admission_by_status_and_source(api,source,state):
    client,conn=api;_,o=new_order(client)
    conn.execute(SalesOrder.__table__.update().where(SalesOrder.id==o['id']).values(status=state,source_type=source))
    with Session(bind=conn,join_transaction_mode='create_savepoint') as s:
        p=NormalizedPurchaseRow(customer_id='A',customer_order_no=o['customer_order_no'],customer_material_code=o['customer_material_code'],quantity=Decimal('3'),raw_data={},sheet_name='Sheet1',row_number=2)
        result=match_purchase_row(s,p)
        assert result.status==('NOT_MATCHED' if state in ['CREATED','INVALID'] else 'MATCHED')
        if result.status=='MATCHED':assert result.matched_order.id==o['id']

def test_three_generation_full_http_abc_and_safety(api, tmp_path):
    client,conn=api;receipt=[]
    for cid,quantities in [('A',['10','3','7']),('B',['20','4','9']),('C',['800','501','12'])]:
        key,first=new_order(client,cid,quantities[0]);assert first['source_order_id'] is None and first['status']=='CREATED'
        _,before_confirmation=purchase(client,cid,key,quantities[1]);assert before_confirmation['status']=='NOT_MATCHED'
        first=data(change(client,first))['order']
        # A safety material, B actual model, C batch/material, and cross-customer isolation.
        overrides={'customer_material_code':'WRONG'} if cid in ['A','C'] else {'customer_model':'WRONG'}
        _,wrong=purchase(client,cid,key,'12',**overrides);assert wrong['status']=='NOT_MATCHED'
        _,cross=purchase(client,{'A':'B','B':'A','C':'A'}[cid],key,'12');assert cross['status']=='NOT_MATCHED'
        task,row=purchase(client,cid,key,quantities[1]);assert row['status']=='SUCCESS'
        second=data(client.get(f"/api/v1/orders/{row['created_order_id']}"));assert second['source_order_id']==first['id']
        second=data(change(client,second))['order']
        task,row=purchase(client,cid,key,quantities[2]);assert row['status']=='SUCCESS'
        third=data(client.get(f"/api/v1/orders/{row['created_order_id']}"));assert third['source_order_id']==second['id']
        for o,qty in zip([first,second,third],quantities):
            assert o['product_code']==first['product_code'] and o['unit_price']==first['unit_price'] and Decimal(o['quantity'])==Decimal(qty)
            assert Decimal(o['amount'])==Decimal(qty)*Decimal('2.5')
        assert third['is_simulated_reference']  # Provenance persists through AUTO → AUTO → manual.
        # Different products under an identical valid key must not be chosen merely by recency.
        conn.execute(SalesOrder.__table__.update().where(SalesOrder.id==first['id']).values(product_code='OTHER-PRODUCT'))
        _,ambiguous=purchase(client,cid,key,'13');assert ambiguous['status']=='AMBIGUOUS' and ambiguous['created_order_id'] is None
        receipt.append({'customer_id':cid,'orders':[{'id':o['id'],'status':o['status'],'source_type':o['source_type'],'source_order_id':o['source_order_id'],'quantity':o['quantity'],'unit_price':o['unit_price'],'amount':o['amount'],'product_code':o['product_code']} for o in [first,second,third]],'test_scope':'outer rollback; IDs do not persist'})
    (tmp_path/'chain_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')

@pytest.mark.parametrize('quantity,expected',[('500','NOT_MATCHED'),('501','SUCCESS')])
def test_confirmed_auto_c_historical_boundary(api,quantity,expected):
    client,conn=api;key,o=new_order(client,'C',quantity);data(change(client,o))
    _,row=purchase(client,'C',key,'12');assert row['status']==expected

@pytest.mark.parametrize('field,value',[('unit_price',0),('amount',999),('product_name','')])
def test_bad_confirmed_auto_reference_never_creates_order(api,field,value):
    client,conn=api;key,o=new_order(client);data(change(client,o))
    conn.execute(SalesOrder.__table__.update().where(SalesOrder.id==o['id']).values(**{field:value}))
    _,row=purchase(client,'A',key,'3');assert row['status']=='INVALID_DATA' and row['created_order_id'] is None

def test_policy_upgrade_is_idempotent_and_preserves_customer_rules(api):
    from scripts.upgrade_history_policy import upgrade_policy
    from app.models import MatchRule
    client,conn=api
    with Session(bind=conn,join_transaction_mode='create_savepoint') as s:
        before=snapshot(conn)
        from app.core.config import get_settings
        if get_settings().db_name!='order_automation':
            with pytest.raises(RuntimeError,match='restricted'):upgrade_policy(s)
            assert snapshot(conn)==before
            return
        assert upgrade_policy(s)==[] and snapshot(conn)==before
        rules=list(s.scalars(select(MatchRule).order_by(MatchRule.id)))
        original={r.id:(r.match_fields,r.validation_config,r.sort_config,r.rule_version,r.filter_conditions['historical_quantity']) for r in rules}
        for r in rules:
            r.filter_conditions={'customer_scope':'same_customer','status':'VALID','excluded_source_types':['AUTO'],'historical_quantity':r.filter_conditions['historical_quantity']}
        s.flush();changes=upgrade_policy(s);assert len(changes)==3
        for r in rules:
            assert (r.match_fields,r.validation_config,r.sort_config,r.rule_version,r.filter_conditions['historical_quantity'])==original[r.id]
            assert r.filter_conditions['eligible_statuses']==['VALID','PROCESSING','SHIPPED','COMPLETED']
            assert r.filter_conditions['allowed_source_types']==['SEED','AUTO']
        assert upgrade_policy(s)==[]
