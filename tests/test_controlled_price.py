"""Controlled price edits over real HTTP/MySQL, only disposable schemas."""
import os
from decimal import Decimal
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from sqlalchemy import select,event,func
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from app.models import SalesOrder,OrderChange
from app.main import app
from app.core.database import get_db
from tests.order_edit_isolation import api,operation_engine
from tests.test_order_operations import row_setup,edit_body
from tests.test_api import data,snapshot
from tests.test_order_status import change

pytestmark=pytest.mark.skipif(os.getenv('RUN_DB_TESTS')!='1',reason='Disposable MySQL schema required')


def historical(client):
    _,row,_=row_setup(client,status='SUCCESS')
    return data(client.get(f"/api/v1/orders/{row['created_order_id']}"))


@pytest.mark.parametrize('prices',[['13.5','11.25'],['10.5','14.0001']])
def test_repeated_price_edits_reference_list_audit_and_lifecycle(api,prices):
    client,conn=api;o=historical(client);url=f"/api/v1/orders/{o['id']}"
    ref=dict(conn.execute(select(SalesOrder.__table__).where(SalesOrder.id==o['source_order_id'])).mappings().one())
    initial=edit_body(o);initial['product']['unit_price']='12';initial['values']['quantity']='4398'
    initial['verification_basis']='隔离测试初始报价 12'
    o=data(client.patch(url,json=initial))['order']
    assert Decimal(o['unit_price'])==12
    for price in prices:
        body=edit_body(o);body['product']['unit_price']=price;body['values']['quantity']='4398'
        body['note']='核对最新报价调整本次价格';body['verification_basis']='报价单 QUOTE-2026-1015；人工核对'
        result=data(client.patch(url,json=body));old=o;o=result['order']
        expected=(Decimal(price)*4398).quantize(Decimal('.01'))
        assert Decimal(o['amount'])==expected
        if price=='13.5':assert expected==59373
        context=data(client.get(url+'/edit'))
        assert Decimal(context['historical_unit_price'])==ref['unit_price']
        assert Decimal(context['order']['unit_price'])==Decimal(price)
        log=context['history'][0]
        assert Decimal(log['before_data']['unit_price'])==Decimal(old['unit_price'])
        assert Decimal(log['before_data']['amount'])==Decimal(old['amount'])
        assert Decimal(log['after_data']['amount'])==expected
        assert log['payload']['verification_basis']==body['verification_basis']
        assert log['actor_label']=='本地销管（未鉴权）' and log['created_at']
        assert Decimal(data(client.get(url))['amount'])==expected
        listed=data(client.get('/api/v1/orders?page_size=100'))['items']
        assert Decimal(next(x for x in listed if x['id']==o['id'])['amount'])==expected
        assert dict(conn.execute(select(SalesOrder.__table__).where(SalesOrder.id==ref['id'])).mappings().one())==ref
        before=snapshot(conn);assert data(client.patch(url,json=body))['replayed'] and snapshot(conn)==before
    # Quantity-only edits after a price change require no new price basis.
    body=edit_body(o);body['verification_basis']=None;body['values']['quantity']='10'
    o=data(client.patch(url,json=body))['order']
    for state in ['VALID','PROCESSING','SHIPPED','COMPLETED']:
        o=data(change(client,o,state))['order']
    assert Decimal(o['unit_price'])==Decimal(prices[-1])


@pytest.mark.parametrize('basis',[None,'','   ','omitted'])
def test_changed_price_requires_basis_and_retains_atomicity(api,basis):
    client,conn=api;o=historical(client);body=edit_body(o);body['product']['unit_price']='13.5'
    if basis=='omitted':body.pop('verification_basis')
    else:body['verification_basis']=basis
    before=snapshot(conn);response=client.patch(f"/api/v1/orders/{o['id']}",json=body)
    assert response.status_code==422 and response.json()['code']=='PRICE_BASIS_REQUIRED'
    assert snapshot(conn)==before


@pytest.mark.parametrize('price',['0','-1','1.00001','100000000000000','NaN','Infinity','abc',12,None])
def test_edit_invalid_prices(api,price):
    client,conn=api;o=historical(client);body=edit_body(o);body['product']['unit_price']=price
    before=snapshot(conn);response=client.patch(f"/api/v1/orders/{o['id']}",json=body)
    assert response.status_code==422 and response.json()['code']=='INVALID_PRICE'
    assert snapshot(conn)==before


def test_no_source_quantity_only_and_product_read_only(api):
    from tests.test_order_operations import draft
    client,conn=api;_,_,url=row_setup(client);o=data(client.post(url,json=draft()))['order']
    url=f"/api/v1/orders/{o['id']}";assert data(client.get(url+'/edit'))['historical_unit_price'] is None
    body=edit_body(o);body.pop('verification_basis');body['values']['quantity']='4398'
    o=data(client.patch(url,json=body))['order']
    assert o['source_order_id'] is None
    for field in ['product_code','product_name']:
        body=edit_body(o);body['product'][field]='CHANGED';before=snapshot(conn)
        assert client.patch(url,json=body).status_code==409 and snapshot(conn)==before
    body=edit_body(o);body['product']['unit_price']='13.5'
    assert Decimal(data(client.patch(url,json=body))['order']['amount'])==59373


@pytest.mark.parametrize('state',['VALID','PROCESSING','SHIPPED','COMPLETED','VOIDED','INVALID'])
def test_non_created_cannot_change_price(api,state):
    client,conn=api;o=historical(client);body=edit_body(o);body['product']['unit_price']='13.5'
    conn.execute(SalesOrder.__table__.update().where(SalesOrder.id==o['id']).values(status=state))
    before=snapshot(conn);assert client.patch(f"/api/v1/orders/{o['id']}",json=body).status_code==409
    assert not data(client.get(f"/api/v1/orders/{o['id']}/edit"))['editable'] and snapshot(conn)==before


def test_price_audit_failure_and_stale_version_roll_back(api):
    client,conn=api;o=historical(client);url=f"/api/v1/orders/{o['id']}";body=edit_body(o);body['product']['unit_price']='13.5'
    before=snapshot(conn)
    def fail(*args):raise RuntimeError('audit insert failure')
    event.listen(OrderChange,'before_insert',fail)
    try:assert client.patch(url,json=body).status_code==500
    finally:event.remove(OrderChange,'before_insert',fail)
    assert snapshot(conn)==before
    data(client.patch(url,json=body));before=snapshot(conn)
    body['request_key']=str(uuid4());body['product']['unit_price']='10'
    response=client.patch(url,json=body)
    assert response.status_code==409 and response.json()['code']=='REVISION_CONFLICT' and snapshot(conn)==before


def test_unaudited_positive_price_cannot_confirm(api):
    client,conn=api;o=historical(client)
    conn.execute(SalesOrder.__table__.update().where(SalesOrder.id==o['id']).values(unit_price=13,amount=Decimal(o['quantity'])*13))
    before=snapshot(conn);assert change(client,o).status_code==409 and snapshot(conn)==before


@pytest.mark.parametrize('note',[None,'','   '])
def test_edit_reason_remains_required(api,note):
    client,conn=api;o=historical(client);body=edit_body(o);body['note']=note
    before=snapshot(conn);response=client.patch(f"/api/v1/orders/{o['id']}",json=body)
    assert response.status_code==422 and response.json()['code']=='INVALID_NOTE' and snapshot(conn)==before


def test_price_capacity_boundary_and_client_amount_refused(api):
    client,conn=api;o=historical(client);body=edit_body(o)
    body['product']['unit_price']='99999999999999.9999';body['values']['quantity']='1'
    before=snapshot(conn);url=f"/api/v1/orders/{o['id']}"
    assert client.patch(url,json={**body,'amount':'1'}).status_code==422 and snapshot(conn)==before
    saved=data(client.patch(url,json=body))['order']
    assert Decimal(saved['unit_price'])==Decimal('99999999999999.9999') and saved['amount']=='100000000000000.00'


def test_committed_concurrent_price_writers_one_winner_one_audit(operation_engine):
    """Real competing transactions commit exclusively in a disposable schema."""
    engine=operation_engine
    def db():
        with Session(engine,expire_on_commit=False) as session:yield session
    app.dependency_overrides[get_db]=db
    try:
        with TestClient(app,raise_server_exceptions=False) as client:
            o=historical(client);url=f"/api/v1/orders/{o['id']}";barrier=Barrier(2)
            def run(price):
                body=edit_body(o);body['product']['unit_price']=price;barrier.wait(timeout=10)
                return client.patch(url,json=body)
            with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,['13.5','11']))
            assert sorted(r.status_code for r in results)==[200,409]
            saved=data(next(r for r in results if r.status_code==200))['order']
            current=data(client.get(url));ctx=data(client.get(url+'/edit'))
            assert Decimal(current['unit_price'])==Decimal(saved['unit_price']) and current['edit_revision']==1
            assert len(ctx['history'])==1
            assert Decimal(ctx['history'][0]['after_data']['unit_price'])==Decimal(current['unit_price'])
            assert Decimal(ctx['history'][0]['after_data']['amount'])==Decimal(current['amount'])
    finally:app.dependency_overrides.pop(get_db,None)
