import json
import os
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4
from contextlib import nullcontext
import pytest
from pydantic import ValidationError
from sqlalchemy import select, event
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from sqlalchemy.dialects import mysql
from app.core.database import get_engine
from app.models import SalesOrder, CustomerConfig, MatchRule
from app.repositories.order_matching import candidate_query
from app.schemas.matching import HistoricalOrderReference
from app.services.customer_configuration import read_configuration_file, ROOT
from app.services.excel_parser import parse_purchase_file
from app.services.matching_engine import match_purchase_row
from tests.reference_faults import install_reference_faults

MANIFEST=json.loads((ROOT/'config/historical_seed_v1.json').read_text(encoding='utf-8'))
EXPECTATIONS=MANIFEST['purchase_expectations']

@pytest.fixture
def session():
    if os.getenv('RUN_DB_TESTS')!='1':pytest.skip('Requires local MySQL RUN_DB_TESTS=1')
    with get_engine().connect() as connection:
        outer=connection.begin()
        install_reference_faults(connection)
        try:
            with Session(bind=connection,join_transaction_mode='create_savepoint') as db:yield db
        finally:outer.rollback()

def purchase(session,cid='A',row=2):
    result=parse_purchase_file(session,cid,next((ROOT/'sample_files').glob(cid+'*.xlsx')))
    assert result.file_error is None
    return next(r.data for r in result.rows if r.row_number==row and r.status=='SUCCESS')

def insert_reference(session,base,**changes):
    fields={c.name:getattr(base,c.name) for c in SalesOrder.__table__.c if c.name!='id'}
    fields.update(order_no='TEST-M05-'+uuid4().hex, created_at=base.created_at+timedelta(days=100),updated_at=base.created_at+timedelta(days=100))
    fields.update(changes)
    if 'amount' not in changes and ('unit_price' in changes or 'quantity' in changes):
        fields['amount']=(fields['unit_price']*fields['quantity']).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    order=SalesOrder(**fields);session.add(order);session.flush();return order

def base_reference(session,no='SEED-M03-V1-A-TIE-LAST'):
    return session.scalar(select(SalesOrder).where(SalesOrder.order_no==no))

@pytest.mark.parametrize('expected',EXPECTATIONS,ids=[r['customer_id']+'-'+str(r['row_number']) for r in EXPECTATIONS])
def test_all_22_real_rows_against_seed_expectations(session,expected):
    result=match_purchase_row(session,purchase(session,expected['customer_id'],expected['row_number']))
    assert result.status==expected['expected_status']
    assert result.config_version==1 and result.rule_version==1
    if expected['expected_order_no'] is not None:
        actual=base_reference(session,expected['expected_order_no'])
        assert actual.id in result.candidate_order_ids
    if result.status=='MATCHED':
        reference=result.matched_order
        assert reference.order_no==expected['expected_order_no']
        assert reference.id==actual.id
        assert reference.product_code==actual.product_code and reference.unit_price==actual.unit_price
        assert reference.customer_id==expected['customer_id']
        assert reference.status=='VALID' and reference.source_type!='AUTO'
        assert result.error_code is None
        with pytest.raises(ValidationError):reference.product_code='MUTATED'
    else:
        assert result.matched_order is None and result.error_code==result.status
        if result.status=='AMBIGUOUS_MATCH':assert len(result.candidate_order_ids)==2

@pytest.mark.parametrize('cid,field',[('A','customer_order_no'),('A','customer_material_code'),('B','customer_order_no'),('B','customer_model'),('C','customer_batch'),('C','customer_material_code')])
def test_missing_keys_never_trigger_sales_query(session,cid,field):
    row=purchase(session,cid).model_copy(update={field:None})
    statements=[];connection=session.connection()
    def record(conn,cursor,statement,parameters,context,executemany):statements.append(statement.lower())
    event.listen(connection,'before_cursor_execute',record)
    try:result=match_purchase_row(session,row)
    finally:event.remove(connection,'before_cursor_execute',record)
    assert result.status=='INVALID_INPUT' and field in result.error_fields
    assert not any('sales_order' in statement for statement in statements)

@pytest.mark.parametrize('source,status',[('SEED','INVALID'),('SEED','CREATED'),('AUTO','INVALID'),('AUTO','CREATED')])
def test_newer_ineligible_record_cannot_supply_product_or_price(session,source,status):
    base=base_reference(session)
    excluded=insert_reference(session,base,source_type=source,status=status,product_code='SIM-WRONG',unit_price=Decimal('999'))
    result=match_purchase_row(session,purchase(session))
    assert result.status=='MATCHED' and result.matched_order.id==base.id
    assert excluded.id not in result.candidate_order_ids
    assert result.matched_order.unit_price==Decimal('12.7')

@pytest.mark.parametrize('cid,wrong_field,wrong_value,base_no',[
 ('A','customer_material_code','OTHER','SEED-M03-V1-A-TIE-LAST'),
 ('B','customer_model','OTHER','SEED-M03-V1-B-NORMAL-NEW'),
])
def test_latest_wrong_safety_key_is_filtered_before_product_or_price(session,cid,wrong_field,wrong_value,base_no):
    base=base_reference(session,base_no)
    bad=insert_reference(session,base,**{wrong_field:wrong_value,'product_code':'SIM-WRONG','unit_price':Decimal('999')})
    result=match_purchase_row(session,purchase(session,cid))
    assert result.status=='MATCHED' and result.matched_order.id==base.id and bad.id not in result.candidate_order_ids

@pytest.mark.parametrize('quantity,expected_status',[('500','NOT_MATCHED'),('501','MATCHED'),('800','MATCHED')])
def test_historical_quantity_boundary_independent_of_purchase_quantity(session,quantity,expected_status):
    row=purchase(session,'C',3)
    base=base_reference(session,'SEED-M03-V1-C-ELIGIBLE-800')
    key='TEST-'+uuid4().hex
    test=insert_reference(session,base,customer_batch=key,quantity=Decimal(quantity))
    result=match_purchase_row(session,row.model_copy(update={'customer_batch':key,'quantity':Decimal('1')}))
    assert result.status==expected_status
    if result.status=='MATCHED':assert result.matched_order.id==test.id

@pytest.mark.parametrize('other_cid',['B','C'])
def test_cross_customer_only_candidate_cannot_match(session,other_cid):
    base=base_reference(session)
    key='TEST-'+uuid4().hex
    insert_reference(session,base,customer_id=other_cid,customer_order_no=key,unit_price=Decimal('999'))
    result=match_purchase_row(session,purchase(session).model_copy(update={'customer_order_no':key}))
    assert result.status=='NOT_MATCHED'

@pytest.mark.parametrize('days',[-1,0,1])
def test_different_products_are_ambiguous_even_with_different_timestamps(session,days):
    base=base_reference(session)
    conflict=insert_reference(session,base,product_code='SIM-CONFLICT',created_at=base.created_at+timedelta(days=days))
    result=match_purchase_row(session,purchase(session))
    assert result.status=='AMBIGUOUS_MATCH' and result.matched_order is None
    assert conflict.id in result.candidate_order_ids


def test_consistent_product_uses_latest_timestamp_then_id(session):
    base=base_reference(session)
    one=insert_reference(session,base,unit_price=Decimal('21.00'),product_name='更新产品名称')
    two=insert_reference(session,one,created_at=one.created_at,unit_price=Decimal('22.00'))
    result=match_purchase_row(session,purchase(session))
    assert result.status=='MATCHED' and result.matched_order.id==two.id
    assert result.matched_order.unit_price==22 and result.matched_order.product_name=='更新产品名称'

@pytest.mark.parametrize('changes,field',[({'unit_price':Decimal('0')},'unit_price'),({'unit_price':Decimal('-1')},'unit_price'),({'product_code':''},'product_code'),({'product_name':'  '},'product_name'),({'quantity':Decimal('0')},'quantity')])
def test_invalid_latest_reference_does_not_fall_back_to_valid_old_reference(session,changes,field):
    base=base_reference(session)
    invalid=insert_reference(session,base,**changes)
    result=match_purchase_row(session,purchase(session))
    assert result.status=='INVALID_REFERENCE' and result.matched_order is None
    assert invalid.id in result.candidate_order_ids and field in result.error_fields


def test_missing_product_identity_in_older_eligible_candidate_blocks_match(session):
    base=base_reference(session)
    bad=insert_reference(session,base,created_at=base.created_at-timedelta(days=1),product_code='')
    result=match_purchase_row(session,purchase(session))
    assert result.status=='INVALID_REFERENCE' and bad.id in result.candidate_order_ids

@pytest.mark.parametrize('changes',[{'unit_price':None},{'product_code':None},{'product_name':None},{'unit_price':Decimal('NaN')}])
def test_unrepresentable_database_reference_still_rejected_at_service_boundary(session,monkeypatch,changes):
    # NOT NULL/DECIMAL prohibit these in MySQL; inject read output, do not weaken schema.
    import app.services.matching_engine as engine
    base=base_reference(session)
    values={name:getattr(base,name) for name in HistoricalOrderReference.model_fields}
    values.update(changes)
    monkeypatch.setattr(engine,'latest_candidate',lambda *args:values)
    result=engine.match_purchase_row(session,purchase(session))
    assert result.status=='INVALID_REFERENCE' and result.matched_order is None

@pytest.mark.parametrize('customer_key',['different',"x' OR 1=1 --"])
def test_unknown_and_injection_like_business_keys_never_relax_filters(session,customer_key):
    row=purchase(session).model_copy(update={'customer_order_no':customer_key})
    query=candidate_query(read_configuration_file()[0],row)
    compiled=query.compile(dialect=mysql.dialect())
    assert customer_key not in str(compiled)
    assert customer_key in compiled.params.values()
    assert match_purchase_row(session,row).status=='NOT_MATCHED'


def test_trailing_space_and_case_do_not_cross_match_business_codes(session):
    base=base_reference(session);row=purchase(session);key='KEY-'+uuid4().hex
    insert_reference(session,base,customer_order_no=key+' ')
    assert match_purchase_row(session,row.model_copy(update={'customer_order_no':key})).status=='NOT_MATCHED'
    insert_reference(session,base,customer_order_no=key.lower())
    assert match_purchase_row(session,row.model_copy(update={'customer_order_no':key})).status=='NOT_MATCHED'


def test_rule_fields_and_threshold_are_database_driven(session):
    row=purchase(session,'B')
    config=session.scalar(select(CustomerConfig).where(CustomerConfig.customer_id=='B'))
    config.field_mapping={**config.field_mapping,'新批次':'customer_batch'}
    rule=session.scalar(select(MatchRule).where(MatchRule.customer_id=='B',MatchRule.enabled.is_(True)))
    rule.match_fields=['customer_batch'];session.flush()
    base=base_reference(session,'SEED-M03-V1-B-NORMAL-NEW');key='TEST-'+uuid4().hex
    test=insert_reference(session,base,customer_order_no='DIFFERENT',customer_model='DIFFERENT',customer_batch=key)
    result=match_purchase_row(session,row.model_copy(update={'customer_batch':key}))
    assert result.status=='MATCHED' and result.matched_order.id==test.id
    c_rule=session.scalar(select(MatchRule).where(MatchRule.customer_id=='C',MatchRule.enabled.is_(True)))
    c_rule.filter_conditions={**c_rule.filter_conditions,'historical_quantity':{'operator':'gt','value':'1000'}}
    session.flush()
    assert match_purchase_row(session,purchase(session,'C',3)).status=='NOT_MATCHED'

@pytest.mark.parametrize('mutation',[
 lambda r:setattr(r,'match_fields',['customer_order_no; DROP TABLE sales_order']),
 lambda r:setattr(r,'sort_config',[{'field':'unit_price','direction':'DESC'}]),
 lambda r:setattr(r,'filter_conditions',{**r.filter_conditions,'status':'INVALID'}),
 lambda r:setattr(r,'filter_conditions',{**r.filter_conditions,'sql':'1=1'}),
])
def test_unsafe_database_rule_rejected(session,mutation):
    row=purchase(session)
    rule=session.scalar(select(MatchRule).where(MatchRule.customer_id=='A',MatchRule.enabled.is_(True)))
    mutation(rule);session.flush()
    result=match_purchase_row(session,row)
    assert result.status=='CONFIG_ERROR' and result.matched_order is None


def test_no_autoflush_no_writes_and_no_stale_order_identity(session):
    row=purchase(session);base=base_reference(session)
    base.unit_price=Decimal('0')  # Pending caller edit must not affect persisted reference read.
    session.add(SalesOrder(order_no='INCOMPLETE-PENDING-M05'))
    statements=[];connection=session.connection()
    def record(conn,cursor,statement,parameters,context,executemany):statements.append(statement.lower())
    event.listen(connection,'before_cursor_execute',record)
    try:result=match_purchase_row(session,row)
    finally:event.remove(connection,'before_cursor_execute',record)
    assert result.status=='MATCHED' and result.matched_order.unit_price==Decimal('12.7')
    assert base.unit_price==0  # Reading did not overwrite caller's pending ORM object.
    assert len(session.new)==1
    assert statements and all(q.lstrip().startswith('select') for q in statements)
    assert not any('for update' in q for q in statements)
    sales_queries=[q for q in statements if 'sales_order' in q]
    assert len(sales_queries)==2 and all('limit' in q for q in sales_queries)


def test_database_error_is_structured_and_does_not_expose_credentials(monkeypatch):
    import app.services.matching_engine as engine
    from app.schemas.purchase_row import NormalizedPurchaseRow
    row=NormalizedPurchaseRow(customer_id='A',customer_order_no='001',customer_material_code='m',quantity=Decimal('1'),raw_data={},sheet_name='Sheet1',row_number=2)
    class FakeSession:no_autoflush=nullcontext()
    def fail(*args):raise OperationalError('private SQL',{},Exception('private password'))
    monkeypatch.setattr(engine,'load_configuration',fail)
    result=engine.match_purchase_row(FakeSession(),row)
    assert result.status=='MATCH_ERROR' and 'private' not in result.model_dump_json()

@pytest.mark.parametrize('changes',[{'quantity':Decimal('0')},{'customer_order_no':' 001 '},{'quantity':1.5},{'customer_id':None},{'row_number':None}])
def test_invalid_normalized_input_before_database_access(monkeypatch,changes):
    import app.services.matching_engine as engine
    from app.schemas.purchase_row import NormalizedPurchaseRow
    row=NormalizedPurchaseRow(customer_id='A',customer_order_no='001',customer_material_code='m',quantity=Decimal('1'),raw_data={},sheet_name='Sheet1',row_number=2).model_copy(update=changes)
    def forbidden(*args):pytest.fail('Invalid input must not query database')
    monkeypatch.setattr(engine,'load_configuration',forbidden)
    assert engine.match_purchase_row(None,row).status=='INVALID_INPUT'

@pytest.mark.parametrize('target',['missing','disabled_customer','disabled_rule','multiple_rules'])
def test_unusable_configuration_never_returns_match(session,target):
    row=purchase(session)
    if target=='missing':row=row.model_copy(update={'customer_id':'UNKNOWN'})
    elif target=='disabled_customer':
        session.scalar(select(CustomerConfig).where(CustomerConfig.customer_id=='A')).enabled=False
    else:
        rule=session.scalar(select(MatchRule).where(MatchRule.customer_id=='A',MatchRule.enabled.is_(True)))
        if target=='disabled_rule':rule.enabled=False
        else:
            values={c.name:getattr(rule,c.name) for c in MatchRule.__table__.c if c.name!='id'}
            values['rule_version']=999
            session.add(MatchRule(**values))
    session.flush()
    result=match_purchase_row(session,row)
    assert result.status=='CONFIG_ERROR' and result.matched_order is None


def test_product_conflict_is_not_hidden_behind_top_candidate_limit(session):
    base=base_reference(session)
    conflict=insert_reference(session,base,product_code='SIM-OLD-CONFLICT',created_at=base.created_at-timedelta(days=10))
    for index in range(40):
        insert_reference(session,base,created_at=base.created_at+timedelta(days=index+1))
    result=match_purchase_row(session,purchase(session))
    assert result.status=='AMBIGUOUS_MATCH' and conflict.id in result.candidate_order_ids


def test_same_product_latest_price_valid_can_ignore_old_invalid_price(session):
    base=base_reference(session)
    insert_reference(session,base,created_at=base.created_at-timedelta(days=1),unit_price=Decimal('0'))
    result=match_purchase_row(session,purchase(session))
    assert result.status=='MATCHED' and result.matched_order.id==base.id


def test_sales_query_failure_is_structured(session,monkeypatch):
    import app.services.matching_engine as engine
    row=purchase(session)
    def fail(*args):raise OperationalError('private SQL',{},Exception('private password'))
    monkeypatch.setattr(engine,'latest_candidate',fail)
    result=engine.match_purchase_row(session,row)
    assert result.status=='MATCH_ERROR' and result.matched_order is None
    assert 'private' not in result.model_dump_json()


# This module owns a fresh legacy baseline; no managed database is queried.
pytestmark = pytest.mark.usefixtures("legacy_runtime")
