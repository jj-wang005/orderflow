"""All HTTP corrections use the existing outer-rollback API fixture."""
import json
import os
from pathlib import Path
from uuid import uuid4
from decimal import Decimal, ROUND_HALF_UP
import pytest
from sqlalchemy import select, func
from app.models import ImportTaskRow, ImportTask, SalesOrder, ManualCorrection
from tests.shared_fixtures import legacy_api as api
from tests.test_api import upload, data, snapshot
from tests.test_excel_parser import workbook_bytes
from app.services.customer_configuration import read_configuration_file

pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="Rollback-only local MySQL")
MANIFEST = json.loads(Path("config/historical_seed_v1.json").read_text(encoding="utf-8"))


def setup_row(client, cid="A"):
    config = next(c for c in read_configuration_file() if c.customer.customer_id == cid)
    example = next(e for e in MANIFEST['purchase_expectations'] if e['customer_id'] == cid and e['expected_status'] == 'MATCHED')
    mapping = config.customer.field_mapping
    values = {f: str(example['values'].get(f)) if example['values'].get(f) is not None else None for f in mapping.values()}
    raw = [values.get(f) if f != 'quantity' else '-1' for f in mapping.values()]
    task = data(upload(client, cid, content=workbook_bytes(headers=list(mapping), rows=[raw])), 201)
    row = data(client.get(f"/api/v1/imports/{task['task_id']}/rows"))['items'][0]
    assert row['error_code'] == 'INVALID_INPUT'
    url = f"/api/v1/imports/{task['task_id']}/rows/{row['id']}/correction"
    return task, row, url, dict(request_key=str(uuid4()), expected_revision=0, values=values, note='核对原始采购数量后补录')


@pytest.mark.parametrize('cid', ['A', 'B', 'C'])
def test_correction_reuses_rules_builds_and_traces(api, cid):
    client, conn = api
    task, original, url, body = setup_row(client, cid)
    context = data(client.get(url))
    assert context['values']['quantity'] == '-1' and context['editable']
    result = data(client.post(url, json=body))
    assert result['revision'] == 1 and result['row']['status'] == 'SUCCESS'
    assert result['row']['raw_data'] == original['raw_data']
    order = data(client.get(f"/api/v1/orders/{result['row']['created_order_id']}"))
    assert order['source_type'] == 'AUTO' and order['source_order_id'] == result['row']['matched_order_id']
    reference = conn.execute(select(SalesOrder.__table__).where(SalesOrder.id == order['source_order_id'])).mappings().one()
    expected = next(e for e in MANIFEST['purchase_expectations'] if e['customer_id']==cid and e['expected_status']=='MATCHED')
    assert reference['order_no'] == expected['expected_order_no']
    assert (order['product_code'],order['product_name'],Decimal(order['unit_price'])) == (reference['product_code'],reference['product_name'],reference['unit_price'])
    assert Decimal(order['quantity']) == Decimal(body['values']['quantity'])
    assert Decimal(order['amount']) == (reference['unit_price'] * Decimal(body['values']['quantity'])).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
    history = data(client.get(url))['history']
    assert len(history) == 1 and history[0]['before_data']['normalized_data'] is None
    assert history[0]['before_data']['error_code'] == 'INVALID_INPUT'
    assert history[0]['submitted_values'] == body['values'] and history[0]['result']['created_order_id'] == order['id']
    summary = data(client.get(f"/api/v1/imports/{task['task_id']}"))
    assert summary['total_rows'] == summary['success_rows'] == 1 and summary['failed_rows'] == 0
    before = snapshot(conn)
    replay = data(client.post(url, json=body))
    assert replay['replayed'] and replay['row']['created_order_id'] == order['id']
    assert snapshot(conn) == before
    assert client.post(url, json={**body, 'request_key':str(uuid4()), 'expected_revision':1}).status_code == 409


@pytest.mark.parametrize('quantity', ['0', '-2', 'NaN', 'abc', '1.0001', '', None])
def test_invalid_correction_is_audited_without_order(api, quantity):
    client, conn = api
    task, original, url, body = setup_row(client)
    body['values']['quantity'] = quantity
    result = data(client.post(url, json=body))
    assert result['row']['status'] == 'INVALID_DATA' and result['row']['error_code'] == 'INVALID_INPUT'
    assert result['row']['created_order_id'] is None and result['row']['raw_data'] == original['raw_data']
    assert result['revision'] == 1
    assert conn.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.import_task_id == task['task_id'])) == 0
    assert data(client.get(url))['values']['quantity'] == quantity


@pytest.mark.parametrize('cid,field', [('A','customer_material_code'),('B','customer_model'),('C','customer_batch')])
def test_wrong_safety_or_match_key_remains_unmatched(api, cid, field):
    client, conn = api
    task, row, url, body = setup_row(client, cid)
    body['values'][field] = 'NO-SUCH-KEY'
    result = data(client.post(url, json=body))
    assert result['row']['status'] == 'NOT_MATCHED' and result['row']['created_order_id'] is None
    assert result['revision'] == 1


@pytest.mark.parametrize('status', ['INVALID_REFERENCE', 'AMBIGUOUS_MATCH'])
def test_bad_reference_or_product_conflict_never_forced(api, status):
    client, conn = api
    task, row, url, body = setup_row(client)
    spec = next(e for e in MANIFEST['purchase_expectations'] if e['customer_id']=='A' and e['expected_status']==status)
    body['values'].update({k:str(v) if v is not None else None for k,v in spec['values'].items() if k in body['values']})
    result = data(client.post(url, json=body))
    assert result['row']['error_code'] == status and result['row']['created_order_id'] is None


def test_revision_key_conflict_and_recovery(api):
    client, conn = api
    task, row, url, body = setup_row(client)
    body['values']['quantity'] = '0'
    data(client.post(url, json=body))
    before = snapshot(conn)
    assert client.post(url, json={**body,'note':'different'}).status_code == 409
    assert client.post(url, json={**body,'request_key':str(uuid4())}).status_code == 409
    assert snapshot(conn) == before
    body.update(request_key=str(uuid4()), expected_revision=1)
    body['values']['quantity']='12'
    result=data(client.post(url,json=body))
    assert result['revision']==2 and result['row']['status']=='SUCCESS'
    assert len(data(client.get(url))['history'])==2


@pytest.mark.parametrize('change', ['product','price','customer','missing_field','numeric_quantity','blank_note'])
def test_request_cannot_bypass_fields(api, change):
    client, conn = api
    task,row,url,body=setup_row(client)
    if change=='product':body['values']['product_code']='ANY'
    elif change=='price':body['unit_price']='1'
    elif change=='customer':body['customer_id']='B'
    elif change=='missing_field':body['values'].pop('customer_material_code')
    elif change=='numeric_quantity':body['values']['quantity']=12
    else:body['note']='   '
    before=snapshot(conn)
    assert client.post(url,json=body).status_code==422
    assert snapshot(conn)==before


def test_audit_write_failure_rolls_back_new_order(api, monkeypatch):
    client, conn=api
    task,row,url,body=setup_row(client)
    before=snapshot(conn)
    from app.models.manual_correction import ManualCorrection
    from sqlalchemy import event
    def fail(*args):raise RuntimeError('audit failure')
    event.listen(ManualCorrection,'before_insert',fail)
    try:assert client.post(url,json=body).status_code==500
    finally:event.remove(ManualCorrection,'before_insert',fail)
    assert snapshot(conn)==before


def test_cross_task_not_found_and_changed_configuration(api):
    client,conn=api
    task,row,url,body=setup_row(client)
    assert client.get(url.replace(f"imports/{task['task_id']}", 'imports/999999999')).status_code==404
    assert client.get(url.replace(f"rows/{row['id']}", 'rows/999999999')).status_code==404
    conn.execute(ImportTask.__table__.update().where(ImportTask.id==task['task_id']).values(config_version=99))
    assert client.post(url,json=body).status_code==409
    assert conn.scalar(select(func.count()).select_from(ManualCorrection).where(ManualCorrection.row_id==row['id']))==0


def test_orphan_order_at_purchase_position_prevents_duplicate(api):
    client,conn=api
    task,row,url,body=setup_row(client)
    data(client.post(url,json=body))
    conn.execute(ImportTaskRow.__table__.update().where(ImportTaskRow.id==row['id']).values(
        status='INVALID_DATA',created_order_id=None))
    before=snapshot(conn)
    assert client.post(url,json={**body,'request_key':str(uuid4()),'expected_revision':1}).status_code==409
    assert snapshot(conn)==before


def test_processing_task_not_editable(api):
    client,conn=api
    task,row,url,body=setup_row(client)
    conn.execute(ImportTask.__table__.update().where(ImportTask.id==task['task_id']).values(status='PROCESSING'))
    assert client.post(url,json=body).status_code==409


def test_c_historical_500_still_excluded_after_repair(api):
    client,conn=api
    task,row,url,body=setup_row(client,'C')
    spec=next(e for e in MANIFEST['purchase_expectations'] if e['customer_id']=='C' and e['expected_status']=='MATCHED')
    conn.execute(SalesOrder.__table__.update().where(SalesOrder.order_no==spec['expected_order_no']).values(quantity=Decimal('500')))
    result=data(client.post(url,json=body))
    assert result['row']['status']=='NOT_MATCHED' and result['row']['created_order_id'] is None
