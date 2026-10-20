"""Supplemental C identity and complete-header tests; only disposable MySQL schemas."""
import json
import os
from pathlib import Path
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import SalesOrder, ImportTask, ConfigurationVersion
from app.repositories.customer_configuration import load_configuration
from app.services.customer_management import suggest_mapping
from app.services.excel_parser import parse_excel
from tests.shared_fixtures import configured_abc as isolated
from tests.test_a_multi_material_guard import send, count_auto, CONFIGS
from tests.test_v2_batch_import import data, rows, review, upload, configure
from tests.test_excel_parser import workbook_bytes

pytestmark=pytest.mark.skipif(os.getenv('RUN_DB_TESTS')!='1',reason='Disposable MySQL only')

def c_blob(qty=1000, note='first purchase', material='950084562', batch='GDGXA52H7'):
    config=CONFIGS['C'].customer
    values={'customer_batch':batch,'customer_material_code':material,'quantity':qty}
    return workbook_bytes([*config.field_mapping,'核对备注'],
        [[*[values[f] for f in config.field_mapping.values()],note]],
        sheet=config.sheet_name,header_row=config.header_row)

def test_c_same_request_and_same_source_do_not_create_again(isolated):
    c,e=isolated;blob=c_blob();key=uuid4().hex
    first=send(c,blob,cid='C',key=key)
    replay=data(upload(c,blob,cid='C',key=key),200)
    assert replay['task_id']==first['task_id'] and count_auto(e)==1
    assert upload(c,blob,cid='C').status_code==409
    again=send(c,blob,cid='C',confirm=True);r=rows(c,again)[0]
    assert r['status']=='REVIEW_REQUIRED' and r['duplicate_order_id'] is None
    assert data(review(c,again,r,decision='EXISTING',existing=rows(c,first)[0]['created_order_id']))['row']['status']=='DUPLICATE' and count_auto(e)==1

def test_full_c_sample_reupload_keeps_seven_original_claims(isolated):
    c,e=isolated;blob=Path('sample_files/C客户采购订单模板.xlsx').read_bytes()
    first=send(c,blob,cid='C');before=rows(c,first)
    assert (first['success_rows'],first['unmatched_rows'],first['review_rows'])==(7,1,0)
    again=send(c,blob,cid='C',confirm=True)
    assert (again['duplicate_rows'],again['unmatched_rows'],again['review_rows'])==(0,1,7)
    assert rows(c,first)==before and count_auto(e)==7

def test_c_optional_po_and_order_edit_cannot_hide_original_batch(isolated):
    from app.services.purchase_guard import check_purchase, PurchaseBlocked, digest
    c,e=isolated;first=send(c,c_blob(),cid='C');r=rows(c,first)[0]
    with Session(e) as s,s.begin():
        order=s.get(SalesOrder,r['created_order_id'])
        order.customer_batch='EDITED-BATCH';order.customer_order_no='OLD-CHANNEL-PO';order.edit_revision+=1
        order.purchase_group_digest=digest(['C','purchase_order','OLD-CHANNEL-PO']);s.flush()
        incoming=SimpleNamespace(normalized_data={**r['normalized_data'],'customer_order_no':'NEW-CHANNEL-PO'},
            purchase_review=None,sheet_name=r['sheet_name'],row_number=r['row_number'])
        task=SimpleNamespace(id=-1,customer_id='C',file_hash=uuid4().hex)
        with pytest.raises(PurchaseBlocked,match='疑似重复采购') as blocked:check_purchase(s,task,incoming)
        assert blocked.value.status=='REVIEW_REQUIRED'
    assert count_auto(e)==1

def test_c_similar_cross_task_requires_decision_not_automatic_duplicate(isolated):
    c,e=isolated;one=send(c,c_blob(),cid='C');oid=rows(c,one)[0]['created_order_id']
    two=send(c,c_blob(note='customer confirms another purchase'),cid='C');r=rows(c,two)[0]
    assert r['status']=='REVIEW_REQUIRED' and '疑似重复采购' in r['error_message']
    assert count_auto(e)==1
    confirmed=data(review(c,two,r,reference='CUSTOMER-CONFIRM-NEW-002',note='隔离样例：依据客户需求确认记录确认再次采购'))['row']
    assert confirmed['status']=='SUCCESS' and count_auto(e)==2
    ctx=data(c.get(f"/api/v1/imports/{two['task_id']}/rows/{r['id']}/purchase-review"))
    assert ctx['history'][0]['decision']=='NEW' and ctx['history'][0]['note']
    third=send(c,c_blob(note='repeat export from another channel'),cid='C');r3=rows(c,third)[0]
    linked=data(review(c,third,r3,decision='EXISTING',existing=oid))['row']
    assert linked['status']=='DUPLICATE' and linked['duplicate_order_id']==oid and count_auto(e)==2

def test_c_within_task_similar_row_requires_review(isolated):
    c,e=isolated;cfg=CONFIGS['C'].customer
    one=['GDGXA52H7' if f=='customer_batch' else '950084562' if f=='customer_material_code' else 12 for f in cfg.field_mapping.values()]
    task=send(c,workbook_bytes(list(cfg.field_mapping),[one,one],sheet=cfg.sheet_name,header_row=cfg.header_row),cid='C')
    assert (task['success_rows'],task['review_rows'])==(1,1) and count_auto(e)==1

def test_incompatible_existing_order_is_not_selectable(isolated):
    c,e=isolated;first=send(c,c_blob(),cid='C');oid=rows(c,first)[0]['created_order_id']
    task=send(c,c_blob(material='ANOTHER-MATERIAL',note='different material'),cid='C');r=rows(c,task)[0]
    assert r['status']=='NOT_MATCHED'
    context=data(c.get(f"/api/v1/imports/{task['task_id']}/rows/{r['id']}/purchase-review"))
    assert context['candidates']==[]
    assert review(c,task,r,decision='EXISTING',existing=oid).status_code==422

@pytest.mark.parametrize('historical_qty,expected',[(499,'NOT_MATCHED'),(500,'NOT_MATCHED'),(501,'SUCCESS')])
def test_c_threshold_uses_history_not_input_quantity(isolated,historical_qty,expected):
    c,e=isolated
    with Session(e) as s,s.begin():
        o=s.scalar(select(SalesOrder).where(SalesOrder.customer_id=='C',SalesOrder.customer_batch=='GDGXA52H7'))
        o.quantity=historical_qty;o.amount=o.unit_price*historical_qty
    task=send(c,c_blob(qty=3),cid='C')
    assert rows(c,task)[0]['status']==expected and task['review_rows']==0

@pytest.mark.parametrize('case',['same_request','same_source','similar_source'])
def test_c_real_commit_races_serialize_and_keep_unique_sources(isolated,case):
    c,e=isolated;barrier=Barrier(2);key=uuid4().hex
    blobs=[c_blob(),c_blob(note='other channel') if case=='similar_source' else c_blob()]
    # Replays can legitimately return 200, initial requests return 201.
    def run(i):
        barrier.wait(timeout=15)
        response=upload(c,blobs[i],cid='C',key=key if case=='same_request' else uuid4().hex,confirm=True)
        assert response.status_code in (200,201),response.text
        return response.json()['data']
    with ThreadPoolExecutor(max_workers=2) as pool:tasks=list(pool.map(run,[0,1]))
    assert count_auto(e)==1
    if case=='same_request':assert tasks[0]['task_id']==tasks[1]['task_id']
    else:assert sorted(rows(c,t)[0]['status'] for t in tasks)==sorted(['SUCCESS','REVIEW_REQUIRED'])

def test_manual_new_reference_is_unique_even_under_concurrency(isolated):
    c,e=isolated;send(c,c_blob(),cid='C')
    tasks=[send(c,c_blob(note='new request '+str(i)),cid='C') for i in range(2)]
    pending=[rows(c,t)[0] for t in tasks];barrier=Barrier(2)
    def run(i):
        barrier.wait(timeout=15)
        response=review(c,tasks[i],pending[i],reference='VERIFIED-C-NEW-REQUEST-002')
        return response.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:states=list(pool.map(run,[0,1]))
    assert sorted(states)==[200,409] and count_auto(e)==2

def test_no_po_no_batch_still_requests_real_identity_evidence(isolated):
    c,e=isolated;configure(c,field_mapping={'料号':'customer_material_code','数量':'quantity'},match_fields=['customer_material_code'])
    task=send(c,workbook_bytes(['料号','数量'],[['MAT',10]]),cid='D')
    r=rows(c,task)[0]
    assert r['status']=='REVIEW_REQUIRED' and '缺少可靠身份依据' in r['error_message'] and count_auto(e)==0

def mapping_body(version=0):
    return {'expected_version':version,'customer_name':'D 完整表头测试','sheet_name':'采购','header_row':2,
        'field_mapping':{'单号':'customer_order_no','料号':'customer_material_code','数量':'quantity'},
        'match_fields':['customer_order_no','customer_material_code'],'historical_quantity':None,'enabled':True,
        'source_columns':[{'index':i,'name':n} for i,n in enumerate(['单号','料号','数量','机型','备注'],1)]}

def test_full_headers_survive_versions_basic_edit_preview_and_task_snapshot(isolated):
    c,e=isolated;data(c.post('/api/v1/customers',json={'customer_id':'D','customer_name':'D'}),201)
    body=mapping_body();saved=data(c.put('/api/v1/customers/D/config',json=body))
    assert len(saved['configuration']['source_columns'])==5
    blob=workbook_bytes(['单号','料号','数量','机型','备注'],[['PO','MAT',12,'MODEL','note']],sheet='采购',header_row=2)
    task=send(c,blob,cid='D');old=data(c.get('/api/v1/imports/'+str(task['task_id'])))
    body=mapping_body(1);body['field_mapping']['机型']='customer_model'
    preview=data(c.post('/api/v1/customers/D/config/preview',data={'configuration':json.dumps(body)},files={'file':('test.xlsx',blob)}))
    assert preview['valid_rows']==1 and preview['rows'][0]['data']['customer_model']=='MODEL' and count_auto(e)==0
    second=data(c.put('/api/v1/customers/D/config',json=body))
    assert second['config_version']==2 and len(second['configuration']['source_columns'])==5
    assert '机型' not in second['versions'][1]['snapshot']['customer']['field_mapping']
    body=mapping_body(2);body['field_mapping'].pop('料号');body['match_fields']=['customer_order_no']
    third=data(c.put('/api/v1/customers/D/config',json=body));assert len(third['configuration']['source_columns'])==5
    edited=data(c.patch('/api/v1/customers/D',json={'expected_version':3,'customer_name':'D 改名称'}))
    assert edited['configuration']['source_columns']==body['source_columns']
    assert data(c.get('/api/v1/imports/'+str(task['task_id'])))==old
    with Session(e) as s:
        original=s.get(ImportTask,task['task_id']).config_snapshot
        assert len(original['source_columns'])==5 and '机型' not in original['customer']['field_mapping']
        assert len(load_configuration(s,'D').source_columns)==5

def test_old_headers_recover_only_from_proven_original_template(isolated):
    c,e=isolated
    for cid in 'ABC':
        before=data(c.get('/api/v1/customers/'+cid))
        assert before['header_source']=='original_template'
        assert len(before['configuration']['source_columns'])>len(before['configuration']['customer']['field_mapping'])
    data(c.post('/api/v1/customers',json={'customer_id':'D','customer_name':'D'}),201)
    body=mapping_body();body.pop('source_columns');data(c.put('/api/v1/customers/D/config',json=body))
    detail=data(c.get('/api/v1/customers/D'))
    assert detail['header_source']=='upload_required' and detail['configuration']['source_columns']==[]
    with Session(e) as s:
        assert 'source_columns' not in s.scalar(select(ConfigurationVersion).where(ConfigurationVersion.customer_id=='C')).snapshot or not s.scalar(select(ConfigurationVersion).where(ConfigurationVersion.customer_id=='C')).snapshot['source_columns']

def test_unavailable_original_header_source_requests_upload_without_breaking_import(isolated,monkeypatch):
    import app.services.source_columns as metadata
    c,e=isolated
    monkeypatch.setattr(metadata,'load_workbook',lambda *a,**kw: (_ for _ in ()).throw(OSError('unavailable test template')))
    detail=data(c.get('/api/v1/customers/C'))
    assert detail['header_source']=='upload_required' and detail['configuration']['source_columns']==[]
    task=send(c,Path('sample_files/C客户采购订单模板.xlsx').read_bytes(),cid='C')
    assert (task['success_rows'],task['unmatched_rows'],task['review_rows'])==(7,1,0)

def test_duplicate_blank_headers_keep_positions_and_unmapped_values(isolated):
    c,e=isolated;data(c.post('/api/v1/customers',json={'customer_id':'D','customer_name':'D'}),201)
    names=['单号',None,'料号','数量','备注','备注',None]
    blob=workbook_bytes(names,[['PO','gap','MAT',12,'left','right','trailing']],sheet='采购',header_row=2)
    suggested=suggest_mapping(blob,'sample.xlsx','采购',2)
    assert [col['column'] for col in suggested['columns']]==names
    assert [col['index'] for col in suggested['columns']]==list(range(1,8))
    assert [col['mappable'] for col in suggested['columns']]==[True,False,True,True,False,False,False]
    body=mapping_body();body['source_columns']=[{'index':i,'name':n} for i,n in enumerate(names,1)]
    data(c.put('/api/v1/customers/D/config',json=body))
    with Session(e) as s:result=parse_excel(load_configuration(s,'D'),blob,file_name='sample.xlsx')
    # Unlabelled data retains the existing safety warning; it never shifts mapped values.
    assert result.file_error is None and result.invalid_rows==1
    assert {f for error in result.rows[0].errors for f in error.fields}=={'__column_B__','__column_G__'}
    assert result.rows[0].raw_data['料号']=='MAT' and result.rows[0].raw_data['数量']==12
    assert result.rows[0].raw_data['__column_E__']=='left' and result.rows[0].raw_data['__column_F__']=='right'
    body['expected_version']=1;body['field_mapping']['备注']='customer_model'
    assert c.put('/api/v1/customers/D/config',json=body).status_code==422
    body['field_mapping'].pop('备注');body['source_columns'][0]['index']=2
    assert c.put('/api/v1/customers/D/config',json=body).status_code==422

def test_header_location_change_requires_new_sample_and_preview_is_read_only(isolated):
    c,e=isolated;data(c.post('/api/v1/customers',json={'customer_id':'D','customer_name':'D'}),201)
    body=mapping_body();data(c.put('/api/v1/customers/D/config',json=body));body['expected_version']=1
    body.pop('source_columns');body['header_row']=3
    assert c.put('/api/v1/customers/D/config',json=body).status_code==422
    body=mapping_body(1)
    invalid=workbook_bytes(['单号','料号','数量','机型','备注'],[['PO',None,-1,'M','n']],sheet='采购',header_row=2)
    result=data(c.post('/api/v1/customers/D/config/preview',data={'configuration':json.dumps(body)},files={'file':('sample.xlsx',invalid)}))
    assert result['invalid_rows']==1 and count_auto(e)==0
    assert data(c.get('/api/v1/customers/D'))['config_version']==1
