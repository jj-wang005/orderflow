"""Real HTTP/MySQL, using the existing rollback-only fixture."""
import os
from uuid import uuid4
from decimal import Decimal, ROUND_HALF_UP
import pytest
from sqlalchemy import select,func,event
from tests.test_api import upload,data,snapshot
from tests.order_edit_isolation import api,operation_engine
from app.models import SalesOrder,ImportTaskRow,OrderChange

pytestmark=pytest.mark.skipif(os.getenv('RUN_DB_TESTS')!='1',reason='Rollback-only MySQL')

def draft():
    return dict(request_key=str(uuid4()),expected_revision=0,product={'product_code':'SIM-MANUAL-TEST','product_name':'模拟人工核对测试产品','unit_price':'1.0050'},note='隔离测试：人工核对后新增',verification_basis='测试价格表 TEST-01；非真实 OMS',simulation_confirmed=True)

def row_setup(client,cid='A',status='NOT_MATCHED'):
    t=data(upload(client,cid),201)
    rows=data(client.get(f"/api/v1/imports/{t['task_id']}/rows?page_size=100"))['items']
    row=next(r for r in rows if r['status']==status)
    return t,row,f"/api/v1/imports/{t['task_id']}/rows/{row['id']}/manual-order"

def edit_body(order):
    return {**draft(),'expected_revision':order['edit_revision'],'product':{k:order[k] for k in ['product_code','product_name','unit_price']},'values':{k:order[k] for k in ['customer_order_no','customer_material_code','customer_model','customer_batch','quantity']}}

@pytest.mark.parametrize('cid',['A','B','C'])
def test_manual_creation_atomic_links_and_exact_retries(api,cid):
    client,conn=api;t,row,url=row_setup(client,cid);body=draft();before_raw=row['raw_data']
    first=data(client.post(url,json=body));o=first['order']
    assert o['source_type']=='AUTO' and o['status']=='CREATED' and o['source_order_id'] is None
    assert o['is_simulated_manual'] and '模拟' in o['price_description']
    assert first['row']['matched_order_id'] is None and first['row']['raw_data']==before_raw and first['row']['status']=='SUCCESS'
    qty=Decimal(row['normalized_data']['quantity']);assert Decimal(o['quantity'])==qty
    assert Decimal(o['amount'])==(qty*Decimal('1.0050')).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    assert o['import_task_id']==t['task_id'] and first['row']['created_order_id']==o['id']
    snap=snapshot(conn);replay=data(client.post(url,json=body));assert replay['replayed'] and replay['order']['id']==o['id'] and snapshot(conn)==snap
    assert client.post(url,json={**body,'request_key':str(uuid4())}).status_code==409
    assert client.post(url,json={**body,'note':'changed'}).status_code==409
    # Original task workflow can reuse manual SUCCESS without treating NULL source as invalid.
    from sqlalchemy.orm import Session
    from app.services.import_service import process_import_task
    with Session(bind=conn,join_transaction_mode='create_savepoint') as s:
        result=process_import_task(s,t['task_id']);assert any(r.created_order_id==o['id'] for r in result.rows)

@pytest.mark.parametrize('status',['SUCCESS','AMBIGUOUS','INVALID_DATA'])
def test_non_unmatched_rows_refused(api,status):
    client,conn=api;t,row,url=row_setup(client,status=status);before=snapshot(conn)
    assert client.post(url,json=draft()).status_code==409 and snapshot(conn)==before

def test_stale_not_matched_cannot_bypass_available_history(api):
    client,conn=api;t,row,url=row_setup(client,status='SUCCESS')
    # Simulate an obsolete NOT_MATCHED row with a matching valid purchase, inside rollback.
    conn.execute(ImportTaskRow.__table__.update().where(ImportTaskRow.id==row['id']).values(status='NOT_MATCHED',created_order_id=None,matched_order_id=None))
    conn.execute(SalesOrder.__table__.delete().where(SalesOrder.id==row['created_order_id']))
    assert client.post(url,json=draft()).status_code==409

@pytest.mark.parametrize('price',['0','-1','NaN','text','1.00001',12])
def test_invalid_manual_price(api,price):
    client,conn=api;t,row,url=row_setup(client);body=draft();body['product']['unit_price']=price
    before=snapshot(conn);assert client.post(url,json=body).status_code==422 and snapshot(conn)==before

@pytest.mark.parametrize('field,value',[('amount','1'),('source_order_id',211),('customer_id','B'),('status','VALID')])
def test_system_fields_never_accepted(api,field,value):
    client,conn=api;t,row,url=row_setup(client);body={**draft(),field:value}
    assert client.post(url,json=body).status_code==422

def test_manual_order_edit_recomputes_audits_and_preserves_procurement(api):
    client,conn=api;t,row,url=row_setup(client);o=data(client.post(url,json=draft()))['order']
    body=edit_body(o);body['values']['quantity']='12';body['product']['unit_price']='2.5000'
    changed=data(client.patch(f"/api/v1/orders/{o['id']}",json=body));assert changed['order']['amount']=='30.00' and changed['order']['edit_revision']==1
    assert changed['order']['source_order_id'] is None and changed['order']['import_task_id']==t['task_id']
    original=conn.execute(select(ImportTaskRow.__table__).where(ImportTaskRow.id==row['id'])).mappings().one()
    assert original['raw_data']==row['raw_data'] and original['normalized_data']==row['normalized_data']
    ctx=data(client.get(f"/api/v1/orders/{o['id']}/edit"));assert len(ctx['history'])==2
    assert Decimal(ctx['history'][0]['before_data']['quantity'])==Decimal(o['quantity']) and ctx['history'][0]['after_data']['amount']=='30.00'
    before=snapshot(conn);assert data(client.patch(f"/api/v1/orders/{o['id']}",json=body))['replayed'] and snapshot(conn)==before
    assert client.patch(f"/api/v1/orders/{o['id']}",json={**body,'request_key':str(uuid4())}).status_code==409

@pytest.mark.parametrize('change',['price','product','material','quantity'])
def test_historical_order_edit_enforces_reference(api,change):
    client,conn=api;t,row,url=row_setup(client,status='SUCCESS');o=data(client.get(f"/api/v1/orders/{row['created_order_id']}"));body=edit_body(o)
    if change=='price':body['product']['unit_price']='1'
    elif change=='product':body['product']['product_code']='SIM-WRONG'
    elif change=='material':body['values']['customer_material_code']='WRONG'
    else:body['values']['quantity']='12'
    before=snapshot(conn);r=client.patch(f"/api/v1/orders/{o['id']}",json=body)
    if change=='quantity':
        result=data(r);assert Decimal(result['order']['amount'])==(Decimal(o['unit_price'])*12).quantize(Decimal('.01'));assert result['order']['source_order_id']==o['source_order_id']
    elif change=='price':
        result=data(r);assert Decimal(result['order']['unit_price'])==1
    else:assert r.status_code==409 and snapshot(conn)==before

@pytest.mark.parametrize('status',['VALID','PROCESSING','SHIPPED','COMPLETED'])
def test_backend_rejects_confirmed_or_fulfillment_orders(api,status):
    client,conn=api;t,row,url=row_setup(client,status='SUCCESS');o=data(client.get(f"/api/v1/orders/{row['created_order_id']}"));body=edit_body(o)
    conn.execute(SalesOrder.__table__.update().where(SalesOrder.id==o['id']).values(status=status))
    assert client.patch(f"/api/v1/orders/{o['id']}",json=body).status_code==409

def test_seed_edit_and_unknown_order_refused(api):
    client,conn=api;seed_id=conn.scalar(select(SalesOrder.id).where(SalesOrder.source_type=='SEED'))
    o=data(client.get(f'/api/v1/orders/{seed_id}'));assert not data(client.get(f'/api/v1/orders/{seed_id}/edit'))['editable']
    assert client.patch(f'/api/v1/orders/{seed_id}',json=edit_body(o)).status_code==409
    assert client.get('/api/v1/orders/99999999/edit').status_code==404

@pytest.mark.parametrize('mode',['create','edit'])
def test_audit_failure_rolls_back_atomic_write(api,mode):
    client,conn=api;t,row,url=row_setup(client);body=draft()
    if mode=='edit':
        o=data(client.post(url,json=body))['order'];body=edit_body(o);body['values']['quantity']='12';url=f"/api/v1/orders/{o['id']}"
    before=snapshot(conn)
    def fail(*args):raise RuntimeError('audit failure')
    event.listen(OrderChange,'before_insert',fail)
    try:r=client.post(url,json=body) if mode=='create' else client.patch(url,json=body)
    finally:event.remove(OrderChange,'before_insert',fail)
    assert r.status_code==500 and snapshot(conn)==before

def test_schema_rejects_bad_demo_seed():
    from scripts.seed_data import load_seed_orders,SeedOrder
    from tests.reference_faults import FAULTS
    from pydantic import ValidationError
    assert len(load_seed_orders())==28 and all(o.unit_price>0 for o in load_seed_orders())
    for f in FAULTS:
        with pytest.raises(ValidationError):SeedOrder.model_validate(f)

@pytest.mark.parametrize('quantity',['0','-1','NaN','1.0001'])
def test_edit_invalid_quantity_rolls_back(api,quantity):
    client,conn=api;t,row,url=row_setup(client);o=data(client.post(url,json=draft()))['order'];body=edit_body(o);body['values']['quantity']=quantity
    before=snapshot(conn);assert client.patch(f"/api/v1/orders/{o['id']}",json=body).status_code==422 and snapshot(conn)==before

def test_edit_amount_overflow_and_system_fields(api):
    client,conn=api;t,row,url=row_setup(client);o=data(client.post(url,json=draft()))['order'];body=edit_body(o)
    body['values']['quantity']='999999999999999.999';body['product']['unit_price']='99999999999999.9999'
    before=snapshot(conn);assert client.patch(f"/api/v1/orders/{o['id']}",json=body).status_code==422 and snapshot(conn)==before
    assert client.patch(f"/api/v1/orders/{o['id']}",json={**edit_body(o),'source_type':'SEED'}).status_code==422

def test_real_two_connections_serialize_without_committing_business_data(operation_engine):
    """Prove second writer blocks on shared customer lock; both transactions roll back.

    This intentionally does NOT claim a committed-winner HTTP replay test.
    """
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy.orm import Session
    from app.schemas.order_operations import ManualOrderRequest
    from app.services.order_operations import manual_create
    engine=operation_engine;first_ready=Event();second_lock=Event();release=Event();finished=Event()
    # Setup commits only into this test's disposable schema.
    from app.services.http_import import submit_import
    from app.services.excel_parser import ParseLimits
    from tests.test_api import ROOT
    with Session(engine) as db,db.begin():
        submit_import(db,'A',next((ROOT/'sample_files').glob('A*.xlsx')).read_bytes(),'test.xlsx',None,ParseLimits())
    with engine.connect() as conn:
        before=snapshot(conn)
        row=conn.execute(select(ImportTaskRow.__table__).where(ImportTaskRow.status=='NOT_MATCHED').order_by(ImportTaskRow.id)).mappings().first()
        assert row is not None
        task_id,row_id,revision=row['task_id'],row['id'],row['manual_revision']
    def watch(conn,cursor,sql,params,context,many):
        if conn.info.get('task2_worker')=='second' and 'FOR UPDATE' in sql and 'FROM customer_config' in sql:second_lock.set()
    event.listen(engine,'before_cursor_execute',watch)
    def run(worker):
        try:
            with engine.connect() as conn:
                outer=conn.begin();conn.info['task2_worker']=worker
                try:
                    with Session(bind=conn,join_transaction_mode='create_savepoint',expire_on_commit=False) as db:
                        body=ManualOrderRequest.model_validate({**draft(),'expected_revision':revision})
                        result=manual_create(db,task_id,row_id,body)
                        if worker=='first':first_ready.set();assert release.wait(10)
                        return result['order'].id
                finally:
                    outer.rollback();conn.info.pop('task2_worker',None)
        finally:
            if worker=='second':finished.set()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(run,'first');assert first_ready.wait(10)
            second=pool.submit(run,'second')
            try:assert second_lock.wait(10) and not finished.wait(.2)
            finally:release.set()
            assert first.result(timeout=15)!=second.result(timeout=15)
    finally:
        release.set();event.remove(engine,'before_cursor_execute',watch)
    with engine.connect() as conn:assert snapshot(conn)==before

@pytest.mark.parametrize('basis',['omitted',None,'','   ','报价资料 PRICE-01'])
def test_optional_basis_and_no_checkbox_can_create_and_replay(api,basis):
    client,conn=api;t,row,url=row_setup(client);body=draft();body.pop('simulation_confirmed')
    body['product']={'product_code':'P-001','product_name':'连接器','unit_price':'2.5000'}
    if basis=='omitted':body.pop('verification_basis')
    else:body['verification_basis']=basis
    result=data(client.post(url,json=body));o=result['order']
    assert o['status']=='CREATED' and o['source_order_id'] is None and o['is_simulated_manual']
    assert '未经过真实 OMS' in o['price_description']
    assert o['product_code']=='P-001' and o['product_name']=='连接器'
    assert Decimal(o['amount'])==(Decimal(row['normalized_data']['quantity'])*Decimal('2.5')).quantize(Decimal('.01'))
    ctx=data(client.get(f"/api/v1/orders/{o['id']}/edit"))
    expected=None if basis in ('omitted',None) else basis.strip()
    assert ctx['history'][0]['payload']['verification_basis']==expected
    before=snapshot(conn);assert data(client.post(url,json=body))['replayed'] and snapshot(conn)==before

@pytest.mark.parametrize('confirmation',[True,False,None])
def test_legacy_confirmation_is_optional_not_provenance(api,confirmation):
    client,conn=api;t,row,url=row_setup(client);body=draft();body['simulation_confirmed']=confirmation
    result=data(client.post(url,json=body));assert result['order']['is_simulated_manual']
    assert data(client.post(url,json=body))['replayed']

def test_edit_without_basis_or_confirmation(api):
    client,conn=api;t,row,url=row_setup(client);body=draft();body.pop('simulation_confirmed');body.pop('verification_basis')
    o=data(client.post(url,json=body))['order'];edited=edit_body(o);edited.pop('verification_basis');edited.pop('simulation_confirmed')
    edited['values']['quantity']='12'
    result=data(client.patch(f"/api/v1/orders/{o['id']}",json=edited));assert result['order']['amount']=='12.06'
    assert result['order']['is_simulated_manual'] and result['order']['source_order_id'] is None

@pytest.mark.parametrize('field,value',[('product_code',''),('product_code','   '),('product_name',''),('product_name','   '),('unit_price','0'),('unit_price','-1')])
def test_optional_basis_does_not_weaken_core_fields(api,field,value):
    client,conn=api;t,row,url=row_setup(client);body=draft();body.pop('verification_basis');body.pop('simulation_confirmed');body['product'][field]=value
    before=snapshot(conn);assert client.post(url,json=body).status_code==422 and snapshot(conn)==before

def test_optional_basis_still_bounded_and_legacy_hash_compatible(api):
    import hashlib,json
    client,conn=api;t,row,url=row_setup(client);body=draft()
    bad={**body,'verification_basis':'x'*1001};assert client.post(url,json=bad).status_code==422
    o=data(client.post(url,json=body))['order']
    audit=conn.execute(select(OrderChange.__table__).where(OrderChange.order_id==o['id'])).mappings().one()
    legacy_payload={k:v for k,v in body.items() if k!='request_key'}
    assert audit['request_hash']==hashlib.sha256(json.dumps(legacy_payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    assert data(client.post(url,json=body))['replayed']

@pytest.mark.parametrize('quantity',['0','-1','NaN'])
def test_manual_create_rejects_invalid_stored_purchase_quantity_without_basis(api,quantity):
    client,conn=api;t,row,url=row_setup(client);body=draft();body.pop('verification_basis');body.pop('simulation_confirmed')
    corrupted={**row['normalized_data'],'quantity':quantity}
    conn.execute(ImportTaskRow.__table__.update().where(ImportTaskRow.id==row['id']).values(normalized_data=corrupted))
    before=snapshot(conn)
    assert client.post(url,json=body).status_code==422 and snapshot(conn)==before
