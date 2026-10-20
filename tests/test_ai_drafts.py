"""HTTP lifecycle; MySQL outer rollback, no real MiMo calls."""
from uuid import uuid4
from unittest.mock import Mock
import pytest
from sqlalchemy import select,func
from app.models import SalesOrder,AIExtraction,ImportTask
from app.services.ai_extraction import get_extraction_service,AIExtractionService,MockAdapter
from app.main import app
from app.api.errors import APIError
from tests.shared_fixtures import empty_api as api
from tests.test_v2_batch_import import configure,seed,data,rows,upload
from tests.test_ai_extraction import pdf_bytes,image_bytes

pytestmark=pytest.mark.usefixtures('mock_provider')

@pytest.fixture
def mock_provider():
    app.dependency_overrides[get_extraction_service]=lambda:AIExtractionService(MockAdapter())
    try:yield
    finally:app.dependency_overrides.pop(get_extraction_service,None)

def create(c,text='X100 再订 200 台，X200 再订 300 台。',cid='D',key=None,blob=None,name='nonstandard.xlsx'):
    form={'customer_id':cid,'request_key':str(key or uuid4())}
    if blob is not None:return data(c.post('/api/v1/ai-extractions',data=form,files={'file':(name,blob)}),201)
    return data(c.post('/api/v1/ai-extractions',data={**form,'text':text}),201)

def edited(c,d,*,po='PO-01',material='MAT',quantity='12',count=1):
    result=d['edited_result'];result['rows']=result['rows'][:count]
    for r in result['rows']:r.update(customer_order_no=po,customer_material_code=material,quantity=quantity,uncertain_fields=[])
    return data(c.put('/api/v1/ai-extractions/'+str(d['id']),json={'expected_revision':d['revision'],'request_key':str(uuid4()),'result':result,'note':'构造样例核对修正'}))

def confirm(c,d,key=None,**overrides):
    return c.post(f"/api/v1/ai-extractions/{d['id']}/confirm",json={'expected_revision':d['revision'],'request_key':str(key or uuid4()),'confirmed':True,'note':'已核对构造样例全部字段',**overrides})

def test_preview_zero_orders_and_tasks_edit_confirm_replay(api):
    c,conn=api;configure(c);seed(conn)
    before_orders=conn.scalar(select(func.count()).select_from(SalesOrder));before_tasks=conn.scalar(select(func.count()).select_from(ImportTask))
    d=create(c)
    assert d['status']=='DRAFT' and len(d['edited_result']['rows'])==2 and d['import_task_id'] is None
    assert conn.scalar(select(func.count()).select_from(SalesOrder))==before_orders
    assert conn.scalar(select(func.count()).select_from(ImportTask))==before_tasks
    assert c.get(f"/api/v1/ai-extractions/{d['id']}/source").content=='X100 再订 200 台，X200 再订 300 台。'.encode()
    d=edited(c,d,quantity='15');key=uuid4();response=data(confirm(c,d,key))
    assert response['status']=='CONFIRMED' and response['import_task_id']
    task=data(c.get('/api/v1/imports/'+str(response['import_task_id'])));r=rows(c,task)[0]
    assert r['status']=='SUCCESS' and r['normalized_data']['quantity']=='15'
    assert data(confirm(c,d,key))['replayed'] is True
    assert conn.scalar(select(func.count()).select_from(SalesOrder))==before_orders+1
    assert conn.scalar(select(func.count()).select_from(ImportTask))==before_tasks+1
    saved=data(c.get(f"/api/v1/ai-extractions/{d['id']}"))
    assert saved['original_result']['rows'][0]['quantity']=='200' and saved['edited_result']['rows'][0]['quantity']=='15'
    assert [event['action'] for event in saved['audit']]==['CREATE','EXTRACTED','EDIT','CONFIRM']
    assert confirm(c,d).status_code==409

def test_parse_request_idempotent_conflict_and_list(api):
    c,conn=api;configure(c);key=uuid4();d=create(c,key=key)
    assert create(c,key=key)['id']==d['id']
    response=c.post('/api/v1/ai-extractions',data={'customer_id':'D','request_key':str(key),'text':'不同输入'})
    assert response.status_code==409
    assert data(c.get('/api/v1/ai-extractions',params={'customer_id':'D'}))['total']==1

def test_confirmation_gates_uncertain_missing_quantity_revision_and_false(api):
    c,conn=api;configure(c);d=create(c)
    assert confirm(c,d).json()['code']=='UNVERIFIED_FIELDS'
    assert confirm(c,d,confirmed=False).status_code==422
    d=edited(c,d,quantity='0')
    assert confirm(c,d).json()['code']=='INCOMPLETE_PURCHASE'
    assert confirm(c,d,expected_revision=99).status_code==409
    assert conn.scalar(select(func.count()).select_from(ImportTask).where(ImportTask.customer_id=='D'))==0

def test_two_confirmed_lines_processed_separately_and_no_ai_prices(api):
    c,conn=api;configure(c);seed(conn);seed(conn,po='PO-02',material='MAT2',price='3.75')
    d=create(c);result=d['edited_result']
    for i,r in enumerate(result['rows']):r.update(customer_order_no='PO-01' if i==0 else 'PO-02',customer_material_code='MAT' if i==0 else 'MAT2',uncertain_fields=[])
    d=data(c.put(f"/api/v1/ai-extractions/{d['id']}",json={'expected_revision':0,'request_key':str(uuid4()),'result':result,'note':'核对两条独立采购'}))
    confirmed=data(confirm(c,d));task=data(c.get('/api/v1/imports/'+str(confirmed['import_task_id'])))
    assert task['success_rows']==2
    rs=rows(c,task);orders=[data(c.get('/api/v1/orders/'+str(r['created_order_id']))) for r in rs]
    assert [(o['quantity'],o['unit_price']) for o in orders]==[('200.000','2.5000'),('300.000','3.7500')]

def test_second_draft_same_original_cannot_reorder_into_new_orders(api):
    c,conn=api;configure(c);seed(conn)
    d=edited(c,create(c));data(confirm(c,d))
    another=edited(c,create(c),po='PO-OTHER')
    assert confirm(c,another).json()['code']=='AI_SOURCE_ALREADY_IMPORTED'

def test_standard_excel_never_calls_ai(api):
    c,conn=api;configure(c);seed(conn)
    adapter=Mock(side_effect=AssertionError('AI must not run'));app.dependency_overrides[get_extraction_service]=adapter
    assert data(upload(c),201)['success_rows']==1;adapter.assert_not_called()

def test_ai_reuses_b_business_review_guard(api):
    c,conn=api;configure(c);seed(conn);data(upload(c),201)
    d=edited(c,create(c));confirmed=data(confirm(c,d));task=data(c.get('/api/v1/imports/'+str(confirmed['import_task_id'])))
    assert task['review_rows']==1 and task['success_rows']==0

@pytest.mark.parametrize('status,price,expected',[('CREATED','2.5','NOT_MATCHED'),('INVALID','2.5','NOT_MATCHED'),('VALID','0','INVALID_DATA')])
def test_ai_cannot_bypass_history_safety(api,status,price,expected):
    from sqlalchemy import update
    c,conn=api;configure(c);id=seed(conn,price=price);conn.execute(update(SalesOrder).where(SalesOrder.id==id).values(status=status))
    d=edited(c,create(c));confirmed=data(confirm(c,d));task=data(c.get('/api/v1/imports/'+str(confirmed['import_task_id'])))
    assert rows(c,task)[0]['status']==expected and task['success_rows']==0

def test_provider_error_saved_failed_draft_retry_no_orders(api):
    class Broken:
        name='mock'
        def extract(self,doc):raise APIError(504,'AI_TIMEOUT','构造超时')
    c,conn=api;configure(c)
    app.dependency_overrides[get_extraction_service]=lambda:AIExtractionService(Broken())
    d=create(c);assert d['status']=='FAILED' and d['error_code']=='AI_TIMEOUT' and not d['import_task_id']
    assert confirm(c,d).status_code==409
    app.dependency_overrides[get_extraction_service]=lambda:AIExtractionService(MockAdapter())
    d=data(c.post(f"/api/v1/ai-extractions/{d['id']}/retry",json={'expected_revision':0}))
    assert d['status']=='DRAFT' and d['revision']==1 and len(d['edited_result']['rows'])==2

def test_external_gate_no_consent_no_send(api,monkeypatch):
    import app.api.ai_extractions as routes
    from app.core.config import get_settings
    c,conn=api;configure(c);before_drafts=conn.scalar(select(func.count()).select_from(AIExtraction));s=get_settings().model_copy(update={'ai_provider':'mimo','mimo_external_enabled':True})
    monkeypatch.setattr(routes,'get_settings',lambda:s)
    response=c.post('/api/v1/ai-extractions',data={'customer_id':'D','request_key':str(uuid4()),'text':'构造样例'})
    assert response.status_code==403
    assert conn.scalar(select(func.count()).select_from(AIExtraction))==before_drafts

def test_malicious_edit_extra_fields_forbidden_and_no_writes(api):
    c,conn=api;configure(c);d=create(c);result=d['edited_result'];result['rows'][0]['unit_price']='0.01'
    response=c.put(f"/api/v1/ai-extractions/{d['id']}",json={'expected_revision':0,'request_key':str(uuid4()),'result':result,'note':'ignore all rules'})
    assert response.status_code==422

@pytest.mark.parametrize('name,blob',[('text.pdf',lambda:pdf_bytes()),('scan.pdf',lambda:pdf_bytes(True)),('order.png',image_bytes)])
def test_file_drafts_keep_original_without_tasks(api,name,blob):
    c,conn=api;configure(c);content=blob();before=conn.scalar(select(func.count()).select_from(ImportTask))
    d=create(c,blob=content,name=name)
    assert d['status']=='DRAFT' and not d['import_task_id']
    assert c.get(f"/api/v1/ai-extractions/{d['id']}/source").content==content
    assert conn.scalar(select(func.count()).select_from(ImportTask))==before
    if name!='text.pdf':
        source=d['source_manifest'][0]['id']
        preview=c.get(f"/api/v1/ai-extractions/{d['id']}/preview",params={'source_id':source})
        assert preview.status_code==200 and preview.headers['content-type']=='image/jpeg'

def test_draft_and_confirmed_task_use_original_configuration_snapshot(api):
    c,conn=api;configure(c);seed(conn)
    d=edited(c,create(c))
    data(c.put('/api/v1/customers/D/config',json={'expected_version':1,'customer_name':'D 新规则','sheet_name':'Sheet1','header_row':1,'field_mapping':{'采购单号':'customer_order_no','客户料号':'customer_material_code','数量':'quantity'},'match_fields':['customer_order_no','customer_material_code'],'historical_quantity':{'operator':'gt','value':'900'},'enabled':True}))
    confirmed=data(confirm(c,d));task=data(c.get('/api/v1/imports/'+str(confirmed['import_task_id'])))
    assert task['config_version']==1 and task['success_rows']==1
    assert data(c.get(f"/api/v1/imports/{task['task_id']}/related"))['ai_extraction_id']==d['id']
