"""V2.0-A targeted HTTP/MySQL tests. All writes stay inside rollback fixture."""
import json
import os
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.api.errors import APIError
from app.models import SalesOrder, ImportTask, ImportTaskRow, ConfigurationVersion, MatchRule
from app.services.customer_management import suggest_mapping
from app.services.import_service import create_import_task, process_import_task
from app.services.matching_engine import match_purchase_row
from app.schemas.purchase_row import NormalizedPurchaseRow
from tests.shared_fixtures import legacy_api as api
from tests.test_api import upload, data, snapshot
from tests.test_excel_parser import workbook_bytes
from tests.test_order_operations import draft, edit_body
from tests.test_order_status import change
from tests.test_v2_batch_import import review as review_purchase

DB = pytest.mark.skipif(os.getenv('RUN_DB_TESTS') != '1', reason='Requires rollback-only MySQL')

def config_body(**overrides):
    return dict(expected_version=0, customer_name='D 演示客户', sheet_name='Sheet1', header_row=1,
                field_mapping={'采购单号':'customer_order_no','客户料号':'customer_material_code','数量':'quantity'},
                match_fields=['customer_order_no','customer_material_code'], historical_quantity=None, enabled=True, **overrides)

def setup_customer(client, cid='D', **overrides):
    data(client.post('/api/v1/customers',json={'customer_id':cid,'customer_name':cid+' 客户'}),201)
    body=config_body();body.update(overrides)
    return data(client.put('/api/v1/customers/'+cid+'/config',json=body))

def content(order='PO-01', material='MAT-01', qty=10):
    return workbook_bytes(['采购单号','客户料号','数量'], [[order,material,qty]])

def first_row(client, task):
    return data(client.get(f"/api/v1/imports/{task['task_id']}/rows"))['items'][0]

def test_suggest_aliases_types_unknown_and_true_model():
    result=suggest_mapping(workbook_bytes(['单号','机型','客户/型号名称','数量','备注'],[['PO','MODEL','DISPLAY',10,'x']]),'sample.xlsx')
    mapping={c['column']:c['recommended_field'] for c in result['columns']}
    assert mapping=={'单号':'customer_order_no','机型':'customer_model','客户/型号名称':None,'数量':'quantity','备注':None}
    assert next(t for t in result['templates'] if t['id']=='B')['recommended']

@pytest.mark.parametrize('values', [[12,20],['x',20],[12.5,20],['=1+1',20]])
def test_suggest_duplicate_targets_and_uncertain_quantity(values):
    result=suggest_mapping(workbook_bytes(['数量','qty'],[values]),'sample.xlsx')
    if values[0] == 12:
        assert all(c['recommended_field'] is None for c in result['columns'])
    else:
        assert result['columns'][0]['recommended_field'] is None

@pytest.mark.parametrize('headers',[['数量','数量'],['数量',None],[' 数量','单号']])
def test_suggest_preserves_unmappable_headers_with_positions(headers):
    if headers[-1] is None: headers=[None,'数量']
    result=suggest_mapping(workbook_bytes(headers,[[10,20]]),'sample.xlsx')
    assert [c['column'] for c in result['columns']]==headers
    assert [c['index'] for c in result['columns']]==[1,2]
    assert not result['columns'][0]['mappable'] and result['columns'][0]['recommended_field'] is None

@DB
def test_new_customer_without_history_preview_save_reuse_and_versions(api):
    client,conn=api
    created=data(client.post('/api/v1/customers',json={'customer_id':'D','customer_name':'D 客户'}),201)
    assert created['config_version']==0 and not created['enabled'] and not created['configured']
    assert client.post('/api/v1/customers',json={'customer_id':'D','customer_name':'重复'}).status_code==409
    assert client.post('/api/v1/customers',json={'customer_id':'D','customer_name':'  '}).status_code==422
    before=snapshot(conn)
    suggested=data(client.post('/api/v1/customers/field-mapping/preview',files={'file':('sample.xlsx',content())}))
    assert suggested['columns'][0]['recommended_field']=='customer_order_no' and snapshot(conn)==before
    preview=data(client.post('/api/v1/customers/D/config/preview',data={'configuration':json.dumps(config_body())},files={'file':('sample.xlsx',content())}))
    assert preview['valid_rows']==1 and snapshot(conn)==before
    c=data(client.put('/api/v1/customers/D/config',json=config_body()))
    assert c['enabled'] and c['config_version']==1 and len(c['versions'])==1
    assert conn.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.customer_id=='D'))==0
    t=data(upload(client,'D',content()),201); row=first_row(client,t)
    assert row['status']=='NOT_MATCHED' and t['config_version']==1
    stored=conn.execute(select(ImportTask.__table__).where(ImportTask.id==t['task_id'])).mappings().one()
    assert stored['config_snapshot']['customer']['config_version']==1
    second=data(upload(client,'D',content('PO-02')),201)
    assert second['config_version']==1
    c=data(client.patch('/api/v1/customers/D',json={'expected_version':1,'customer_name':'D 新名称'}))
    assert c['customer_name']=='D 新名称' and c['config_version']==2
    assert c['versions'][1]['task_count']==2
    assert client.put('/api/v1/customers/D/config',json=config_body()).status_code==409
    assert data(client.get('/api/v1/customers/D/config'))['config_version']==2
    assert any(c['customer_id']=='D' for c in data(client.get('/api/v1/customers')))
    assert len(data(client.get('/api/v1/match-rule-templates'))['templates'])==3

@DB
@pytest.mark.parametrize('change',[
    {'field_mapping':{'数量':'quantity'}}, {'field_mapping':{'数量':'quantity','数量2':'quantity','单号':'customer_order_no','料号':'customer_material_code'}},
    {'match_fields':[]}, {'match_fields':['customer_order_no','customer_order_no']}, {'match_fields':['product_code']},
    {'historical_quantity':{'operator':'sql','value':'500'}}, {'historical_quantity':{'operator':'gt','value':'-1'}},
    {'historical_quantity':{'operator':'gte','value':'1.5'}}, {'historical_quantity':{'operator':'eq','value':'NaN'}},
    {'filter_conditions':{'customer_scope':'all'}}, {'sort_config':[{'field':'updated_at','direction':'DESC'}]},
    {'sql':'1=1'}, {'enabled':True,'historical_quantity':{'operator':'eq','value':'0 OR 1=1'}},
])
def test_invalid_configuration_does_not_write(api,change):
    client,conn=api;data(client.post('/api/v1/customers',json={'customer_id':'D','customer_name':'D'}),201)
    before=snapshot(conn);body=config_body();body.update(change)
    assert client.put('/api/v1/customers/D/config',json=body).status_code==422
    assert snapshot(conn)==before

@DB
def test_versions_preserve_processing_correction_manual_edit_and_status(api):
    client,conn=api;setup_customer(client)
    with Session(bind=conn,join_transaction_mode='create_savepoint') as db:
        pending=create_import_task(db,'D',content(),file_name='snapshot.xlsx');db.commit()
    t=data(upload(client,'D',content('BAD',qty=-1)),201);row=first_row(client,t)
    manual_task=data(upload(client,'D',content('MANUAL')),201);manual_row=first_row(client,manual_task)
    # Change template and required mapped fields. Old task must still use v1.
    body=config_body();body.update(expected_version=1,field_mapping={'批次':'customer_batch','数量':'quantity'},match_fields=['customer_batch'])
    newer=data(client.put('/api/v1/customers/D/config',json=body));assert newer['config_version']==2
    with Session(bind=conn,join_transaction_mode='create_savepoint') as db:
        result=process_import_task(db,pending);assert result.rows[0].status=='NOT_MATCHED';db.commit()
    url=f"/api/v1/imports/{t['task_id']}/rows/{row['id']}/correction"
    correction={'request_key':str(uuid4()),'expected_revision':0,'note':'按原配置补录','values':{'customer_order_no':'FIXED','customer_material_code':'MAT-01','quantity':'12'}}
    assert data(client.post(url,json=correction))['row']['status']=='NOT_MATCHED'
    manual_url=f"/api/v1/imports/{manual_task['task_id']}/rows/{manual_row['id']}/manual-order"
    order=data(client.post(manual_url,json=draft()))['order']
    edited=data(client.patch(f"/api/v1/orders/{order['id']}",json=edit_body(order)))['order']
    assert data(change(client,edited))['order']['status']=='VALID'
    new=data(upload(client,'D',workbook_bytes(['批次','数量'],[['NEW',3]])),201)
    assert new['config_version']==2
    assert client.get(url).status_code==200
    assert first_row(client,manual_task)['created_order_id']==order['id']

@DB
@pytest.mark.parametrize('operator,threshold,expected',[('gt','500','NOT_MATCHED'),('gte','500','SUCCESS'),('eq','500','SUCCESS'),('eq','501','NOT_MATCHED')])
def test_historical_quantity_and_confirmation_use_same_operator(api,operator,threshold,expected):
    client,conn=api;setup_customer(client,historical_quantity={'operator':operator,'value':threshold})
    base=SalesOrder(order_no='TEST-'+uuid4().hex,customer_id='D',customer_order_no='PO-01',customer_material_code='MAT-01',product_code='PROD',product_name='产品',unit_price=Decimal('2.5'),quantity=Decimal(500),amount=Decimal(1250),status='VALID',source_type='SEED')
    with Session(bind=conn,join_transaction_mode='create_savepoint') as db:db.add(base);db.commit()
    t=data(upload(client,'D',content(qty=12)),201);row=first_row(client,t);assert row['status']==expected
    if expected=='SUCCESS':
        order=data(client.get('/api/v1/orders/'+str(row['created_order_id'])))
        assert order['amount']=='30.00'
        assert data(change(client,order))['order']['status']=='VALID'

@DB
def test_new_customer_manual_confirm_and_three_generation_reorder(api):
    client,conn=api;setup_customer(client,'E')
    t=data(upload(client,'E',content(qty=10)),201);row=first_row(client,t)
    order=data(client.post(f"/api/v1/imports/{t['task_id']}/rows/{row['id']}/manual-order",json=draft()))['order']
    def next_purchase(qty,note):
        sample=workbook_bytes(['采购单号','客户料号','数量','独立需求说明'],[['PO-01','MAT-01',qty,note]])
        task=data(upload(client,'E',sample),201);r=first_row(client,task)
        if r['status']=='REVIEW_REQUIRED':r=data(review_purchase(client,task,r))['row']
        return r
    assert next_purchase(3,'隔离：原订单未确认时的新需求')['status']=='NOT_MATCHED'
    before=conn.scalar(select(func.count()).select_from(SalesOrder));data(change(client,order))
    assert conn.scalar(select(func.count()).select_from(SalesOrder))==before
    second=next_purchase(3,'隔离：第二笔真实采购');assert second['status']=='SUCCESS'
    o2=data(client.get('/api/v1/orders/'+str(second['created_order_id'])));assert o2['source_order_id']==order['id'] and o2['amount']=='3.02'
    data(change(client,o2))
    third=next_purchase(7,'隔离：第三笔真实采购');assert third['matched_order_id']==o2['id']
    assert len(data(client.get('/api/v1/orders',params={'customer_id':'E'}))['items'])==3

@DB
@pytest.mark.parametrize('qty',[0,-1,1.25,'NaN'])
def test_new_import_quantity_must_be_positive_integer(api,qty):
    client,conn=api;setup_customer(client);task=data(upload(client,'D',content(qty=qty)),201)
    row=first_row(client,task);assert row['status']=='INVALID_DATA' and row['created_order_id'] is None

@DB
def test_latest_created_at_tie_break_product_conflict_and_invalid_price(api):
    client,conn=api;setup_customer(client)
    with Session(bind=conn,join_transaction_mode='create_savepoint') as db:
        ids=[]
        for created,updated,price,code in [(datetime(2020,1,1),datetime(2030,1,1),'2','P'),(datetime(2021,1,1),datetime(2021,1,1),'3','P'),(datetime(2021,1,1),datetime(2021,1,1),'4','P')]:
            order=SalesOrder(order_no=uuid4().hex,customer_id='D',customer_order_no='PO-01',customer_material_code='MAT-01',product_code=code,product_name='产品',unit_price=Decimal(price),quantity=Decimal(10),amount=Decimal(price)*10,status='VALID',source_type='SEED',created_at=created,updated_at=updated)
            db.add(order);db.flush();ids.append(order.id)
        db.commit()
    def next_purchase(note):
        sample=workbook_bytes(['采购单号','客户料号','数量','独立需求说明'],[['PO-01','MAT-01',10,note]])
        task=data(upload(client,'D',sample),201);r=first_row(client,task)
        if r['status']=='REVIEW_REQUIRED':r=data(review_purchase(client,task,r))['row']
        return r
    row=next_purchase('隔离：首笔采购');assert row['matched_order_id']==ids[-1]
    conn.execute(SalesOrder.__table__.update().where(SalesOrder.id==ids[0]).values(product_code='OTHER'))
    row=next_purchase('隔离：存在产品冲突的新需求');assert row['status']=='AMBIGUOUS' and row['created_order_id'] is None
    conn.execute(SalesOrder.__table__.update().where(SalesOrder.id==ids[0]).values(product_code='P'))
    conn.execute(SalesOrder.__table__.update().where(SalesOrder.id==ids[-1]).values(unit_price=0,amount=0))
    row=next_purchase('隔离：存在无效单价的新需求');assert row['status']=='INVALID_DATA'

@DB
@pytest.mark.parametrize('cid',['A','B','C'])
def test_abc_can_explicitly_save_one_field_as_a_new_version(api,cid):
    client,conn=api;body=config_body();body.update(expected_version=1,match_fields=['customer_order_no'])
    body['source_columns']=[{'index':i,'name':name} for i,name in enumerate(body['field_mapping'],1)]
    saved=data(client.put('/api/v1/customers/'+cid+'/config',json=body))
    assert saved['config_version']==2
    assert saved['configuration']['rule']['match_fields']==['customer_order_no']
    assert saved['configuration']['rule']['validation_config']=={'extra_required_fields':[],'consistency_fields':[]}
    assert saved['configuration']['rule']['filter_conditions']['historical_quantity'] is None
    assert saved['versions'][1]['snapshot']['customer']['config_version']==1


@pytest.mark.parametrize('value',[123.5,1234567890123456,True])
def test_unsafe_code_sample_not_auto_recommended(value):
    result=suggest_mapping(workbook_bytes(['客户料号','数量'],[[value,12]]),'sample.xlsx')
    assert result['columns'][0]['recommended_field'] is None

@DB
def test_paused_config_only_blocks_new_import_old_task_remains_usable(api):
    client,conn=api;setup_customer(client)
    task=data(upload(client,'D',content()),201);row=first_row(client,task)
    body=config_body();body.update(expected_version=1,enabled=False)
    saved=data(client.put('/api/v1/customers/D/config',json=body));assert not saved['enabled']
    assert upload(client,'D',content('NEW')).status_code==422
    assert client.get(f"/api/v1/imports/{task['task_id']}/rows/{row['id']}/correction").status_code==200
    order=data(client.post(f"/api/v1/imports/{task['task_id']}/rows/{row['id']}/manual-order",json=draft()))['order']
    assert data(change(client,order))['order']['status']=='VALID'

@DB
def test_sample_missing_column_and_bad_row_preview_do_not_write(api):
    client,conn=api;setup_customer(client);before=snapshot(conn)
    url='/api/v1/customers/D/config/preview';body=config_body();body['expected_version']=1
    missing=data(client.post(url,data={'configuration':json.dumps(body)},files={'file':('test.xlsx',workbook_bytes(['数量'],[[12]]))}))
    assert missing['file_error'] and missing['total_rows']==0
    invalid=data(client.post(url,data={'configuration':json.dumps(body)},files={'file':('test.xlsx',content(qty=1.5))}))
    assert invalid['invalid_rows']==1 and invalid['valid_rows']==0
    assert snapshot(conn)==before

@DB
def test_legacy_config_save_keeps_original_semantics_and_old_tasks(api):
    client,conn=api
    for cid in ['A','B','C']:
        original=data(client.get('/api/v1/customers/'+cid));cfg=original['configuration'];rule=cfg['rule'];customer=cfg['customer']
        payload=config_body();payload.update(expected_version=1,customer_name=original['customer_name'],sheet_name=customer['sheet_name'],header_row=customer['header_row'],field_mapping=customer['field_mapping'],match_fields=rule['match_fields']+rule['validation_config']['consistency_fields'],historical_quantity=rule['filter_conditions']['historical_quantity'])
        changed=data(client.put('/api/v1/customers/'+cid+'/config',json=payload));after=changed['configuration']['rule']
        assert after['match_fields']==list(dict.fromkeys(rule['match_fields']+rule['validation_config']['consistency_fields']))
        assert after['validation_config']=={'extra_required_fields':[],'consistency_fields':[]}
        assert changed['versions'][1]['snapshot']['rule']==rule
        assert after['filter_conditions']==rule['filter_conditions']
        assert changed['config_version']==2
