import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from sqlalchemy import select, func, event, delete
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError, OperationalError
from pymysql.err import OperationalError as DriverOperationalError
from app.core.database import get_engine, get_session_factory
from app.models import SalesOrder, ImportTask, ImportTaskRow, CustomerConfig, MatchRule
from app.schemas.matching import HistoricalOrderReference
from app.schemas.purchase_row import NormalizedPurchaseRow
from app.services import order_service, import_service
from app.services.order_service import calculate_amount, build_sales_order, OrderValidationError
from app.services.import_service import (import_purchase_file, create_import_task, process_import_task,
                                        process_task_row, ImportRejected, FatalImportError)
from app.repositories.import_repository import ImportInvariantError
from app.services.customer_configuration import ROOT
from app.services.matching_engine import match_purchase_row
from app.services.excel_parser import parse_purchase_file
from tests.test_excel_parser import workbook_bytes
from scripts.seed_data import load_seed_orders
from tests.reference_faults import install_reference_faults

@pytest.fixture
def session():
    if os.getenv('RUN_DB_TESTS')!='1':pytest.skip('Requires local MySQL RUN_DB_TESTS=1')
    with get_engine().connect() as connection:
        outer=connection.begin()
        install_reference_faults(connection)
        try:
            with Session(bind=connection,join_transaction_mode='create_savepoint') as db:yield db
        finally:outer.rollback()

def sample(cid):return next((ROOT/'sample_files').glob(cid+'*.xlsx'))

def single_file():return workbook_bytes(rows=[['1206692205','E0112-000060',1]])

def count_orders(session,task_id):
    return session.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.import_task_id==task_id))

def seed_snapshot(session):
    return [tuple(getattr(o,c.name) for c in SalesOrder.__table__.c) for o in session.scalars(select(SalesOrder).where(SalesOrder.source_type=='SEED').order_by(SalesOrder.id))]

def reference():
    spec=next(o for o in load_seed_orders() if o.order_no=='SEED-M03-V1-A-TIE-LAST')
    values=spec.order_values()
    return HistoricalOrderReference.model_validate({field:values.get(field) if field!='id' else 1 for field in HistoricalOrderReference.model_fields})

def purchase():
    return NormalizedPurchaseRow(customer_id='A',customer_order_no='001',customer_material_code='NEW',quantity=Decimal('12'),raw_data={},sheet_name='Sheet1',row_number=2)

@pytest.mark.parametrize('price,quantity,expected', [('1.005','1','1.01'),('1.004','1','1.00'),('6.25','12','75.00'),('1.3333','19','25.33'),('0.0001','1','0.00')])
def test_amount_precision(price,quantity,expected):
    amount=calculate_amount(Decimal(price),Decimal(quantity))
    assert amount==Decimal(expected) and amount.as_tuple().exponent==-2

@pytest.mark.parametrize('price,quantity',[(Decimal('0'),Decimal('1')),(Decimal('-1'),Decimal('1')),(Decimal('1'),Decimal('0')),(Decimal('NaN'),Decimal('1')),(1.5,Decimal('1'))])
def test_invalid_amount_inputs(price,quantity):
    with pytest.raises(OrderValidationError):calculate_amount(price,quantity)


def test_amount_overflow_without_float_rounding():
    with pytest.raises(OrderValidationError,match='金额超过'):
        calculate_amount(Decimal('99999999999999.9999'),Decimal('999999999999999'))


def test_order_fields_and_optional_inheritance():
    ref=reference().model_copy(update={'customer_batch':'OLD-BATCH','customer_model':'OLD-MODEL'})
    row=purchase().model_copy(update={'customer_order_no':None,'customer_material_code':None})
    order=build_sales_order(row,ref,1)
    assert order.customer_order_no is None and order.customer_material_code is None
    assert order.customer_batch=='OLD-BATCH' and order.customer_model=='OLD-MODEL'
    preferred=build_sales_order(row.model_copy(update={'customer_batch':'NEW-BATCH','customer_model':'NEW-MODEL'}),ref,1)
    assert preferred.customer_batch=='NEW-BATCH' and preferred.customer_model=='NEW-MODEL'
    assert preferred.quantity==12 and preferred.quantity!=ref.quantity
    assert preferred.product_code==ref.product_code and preferred.product_name==ref.product_name
    assert preferred.unit_price==ref.unit_price and preferred.amount==Decimal('152.40')
    assert preferred.status=='CREATED' and preferred.source_type=='AUTO'
    assert preferred.source_order_id==ref.id and preferred.import_task_id==1
    assert preferred.source_sheet_name=='Sheet1' and preferred.source_row_number==2

@pytest.mark.parametrize('changes',[{'unit_price':Decimal('0')},{'unit_price':Decimal('-1')},{'product_name':'  '},{'product_code':''},{'customer_id':'B'}])
def test_invalid_or_cross_customer_reference_cannot_build(changes):
    with pytest.raises(OrderValidationError):build_sales_order(purchase(),reference().model_copy(update=changes),1)


def test_uuid_order_numbers_are_unique_without_database_max():
    values={order_service.generate_order_no() for _ in range(2000)}
    assert len(values)==2000 and all(v.startswith('AUTO-') and len(v)==37 for v in values)


def test_actual_22_rows_eight_orders_and_all_traceability(session):
    before=seed_snapshot(session)
    expectations=json.loads((ROOT/'config/historical_seed_v1.json').read_text(encoding='utf-8'))['purchase_expectations']
    totals=[0,0,0,0]
    tasks=[]
    for cid in ['A','B','C']:
        task=import_purchase_file(session,cid,sample(cid));tasks.append(task)
        assert task.status=='PARTIAL_SUCCESS' and task.pending_rows==0
        assert task.config_version==1 and task.rule_version==1
        assert task.file_hash==hashlib.sha256(sample(cid).read_bytes()).hexdigest()
        assert task.started_at is not None and task.finished_at>=task.started_at
        for index,value in enumerate([task.total_rows,task.success_rows,task.unmatched_rows,task.failed_rows]):totals[index]+=value
        for row in task.rows:
            expected=next(r for r in expectations if r['customer_id']==cid and r['row_number']==row.row_number)
            mapping={'MATCHED':'SUCCESS','NOT_MATCHED':'NOT_MATCHED','INVALID_REFERENCE':'INVALID_DATA','AMBIGUOUS_MATCH':'AMBIGUOUS'}
            assert row.status==mapping[expected['expected_status']]
            assert row.raw_data and row.normalized_data
            if row.status=='SUCCESS':
                order=row.created_order
                historical=session.scalar(select(SalesOrder).where(SalesOrder.order_no==expected['expected_order_no']))
                assert order.source_order_id==historical.id==row.matched_order_id
                assert order.id==row.created_order_id
                assert order.product_code==historical.product_code and order.product_name==historical.product_name
                assert order.unit_price==historical.unit_price
                assert order.quantity==Decimal(expected['values']['quantity'])
                assert order.amount==Decimal(expected['expected_amount'])
                assert order.import_task_id==task.id and order.source_row_number==row.row_number and order.source_sheet_name=='Sheet1'
                assert order.source_type=='AUTO' and order.status=='CREATED'
            else:
                assert row.created_order_id is None and row.created_order is None
                assert row.error_code==expected['expected_status'] and row.error_message
        assert count_orders(session,task.id)==task.success_rows
    assert totals==[22,8,9,5]
    assert seed_snapshot(session)==before
    assert session.scalar(select(func.count()).select_from(ImportTask).where(ImportTask.id.in_([t.id for t in tasks])))==3
    assert session.scalar(select(func.count()).select_from(ImportTaskRow).where(ImportTaskRow.task_id.in_([t.id for t in tasks])))==22
    parsed=parse_purchase_file(session,'C',sample('C'))
    match=match_purchase_row(session,next(r.data for r in parsed.rows if r.row_number==3))
    assert match.status=='MATCHED' and match.matched_order.order_no=='SEED-M03-V1-C-ELIGIBLE-800'


def test_replay_same_task_and_successful_row_are_idempotent(session):
    result=import_purchase_file(session,'A',sample('A'))
    first=result.rows[0];finish=result.finished_at
    again=process_task_row(session,result.id,first.sheet_name,first.row_number)
    assert again.model_dump()==first.model_dump()
    replay=process_import_task(session,result.id)
    assert replay.model_dump()==result.model_dump() and replay.finished_at==finish
    assert count_orders(session,result.id)==3


def test_same_file_repeat_purchase_requires_explicit_new_review(session):
    content=single_file()  # Reuse exact bytes; ZIP/Excel timestamps can vary between calls.
    one=import_purchase_file(session,'A',content,file_name='repeat.xlsx')
    two=import_purchase_file(session,'A',content,file_name='repeat.xlsx')
    assert one.id!=two.id and one.file_hash==two.file_hash
    assert one.success_rows == 1 and two.success_rows == 0
    row = two.rows[0]
    assert row.status == 'REVIEW_REQUIRED' and row.created_order_id is None
    from app.schemas.purchase_review import PurchaseReviewRequest
    from app.services.purchase_review import review_purchase
    result = review_purchase(session,two.id,row.id,PurchaseReviewRequest(
        request_key=uuid4(), expected_revision=session.get(ImportTaskRow,row.id).manual_revision, decision='NEW',
        note='测试核实：客户确认独立追加采购'))
    assert result['row'].status == 'SUCCESS'
    assert result['row'].created_order_id != one.rows[0].created_order_id
    assert count_orders(session,one.id) == count_orders(session,two.id) == 1


def test_unique_task_position_remains_database_enforced(session):
    task_id=create_import_task(session,'A',single_file(),file_name='unique.xlsx')
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(ImportTaskRow(task_id=task_id,sheet_name='Sheet1',row_number=2,raw_data={}))
            session.flush()
    result=process_import_task(session,task_id)
    assert result.success_rows==1 and len(result.rows)==1


def test_invalid_input_rows_do_not_block_other_rows(session):
    content=workbook_bytes(rows=[['1206692205','E0112-000060',0],['1206692205','E0112-000060',12]])
    result=import_purchase_file(session,'A',content,file_name='partial.xlsx')
    assert result.total_rows==2 and result.success_rows==1 and result.failed_rows==1
    assert result.rows[0].status=='INVALID_DATA' and result.rows[0].error_code=='INVALID_INPUT'
    assert result.rows[0].raw_data['订单数量']==0 and result.rows[0].normalized_data is None
    assert result.rows[1].created_order.quantity==12


def test_all_failure_and_file_level_failure_statistics(session):
    content=workbook_bytes(rows=[['NO-MATCH','MAT',1],['NO-MATCH','MAT',0]])
    result=import_purchase_file(session,'A',content,file_name='all-failure.xlsx')
    assert result.status=='FAILED' and result.total_rows==2 and result.unmatched_rows==1 and result.failed_rows==1
    assert result.success_rows==0 and count_orders(session,result.id)==0
    file_failure=import_purchase_file(session,'A',b'bad workbook',file_name='bad.xlsx')
    assert file_failure.status=='FAILED' and file_failure.total_rows==0
    assert 'INVALID_FILE' in file_failure.error_message and file_failure.rows==[]


def test_unknown_customer_rejected_without_new_task(session):
    before=session.scalar(select(func.count()).select_from(ImportTask))
    with pytest.raises(ImportRejected):import_purchase_file(session,'UNKNOWN',single_file(),file_name='test.xlsx')
    assert session.scalar(select(func.count()).select_from(ImportTask))==before


def test_order_unique_conflict_rolls_back_only_one_row(session,monkeypatch):
    real=order_service.generate_order_no;calls=0
    def number():
        nonlocal calls
        calls+=1
        return 'SEED-M03-V1-A-TIE-LAST' if calls==1 else real()
    monkeypatch.setattr(order_service,'generate_order_no',number)
    result=import_purchase_file(session,'A',sample('A'))
    assert result.success_rows==2 and result.failed_rows==3 and result.unmatched_rows==4
    assert result.rows[0].status=='FAILED' and result.rows[0].error_code=='DB_UNIQUE_CONFLICT'
    assert result.rows[0].created_order_id is None and result.rows[0].matched_order_id is not None
    assert count_orders(session,result.id)==2
    assert all(r.status=='SUCCESS' for r in result.rows[1:3])


def test_actual_mysql_detail_fk_failure_removes_already_inserted_order(session):
    injected=False
    def break_success(db,flush_context,instances):
        nonlocal injected
        if injected:return
        for obj in db.dirty:
            if isinstance(obj,ImportTaskRow) and obj.status=='SUCCESS':
                obj.created_order_id=-1  # Actual MySQL FK violation after the order INSERT.
                injected=True;return
    event.listen(session,'before_flush',break_success)
    try:result=import_purchase_file(session,'A',sample('A'))
    finally:event.remove(session,'before_flush',break_success)
    assert injected and result.success_rows==2
    assert result.rows[0].status=='FAILED' and result.rows[0].created_order_id is None
    assert count_orders(session,result.id)==2
    assert session.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.import_task_id==result.id,SalesOrder.source_row_number==2))==0


def test_actual_mysql_data_error_is_row_local(session,monkeypatch):
    real=order_service.generate_order_no;calls=0
    def number():
        nonlocal calls
        calls+=1
        return 'X'*65 if calls==1 else real()
    monkeypatch.setattr(order_service,'generate_order_no',number)
    result=import_purchase_file(session,'A',sample('A'))
    assert result.rows[0].status=='FAILED' and result.rows[0].error_code=='DB_WRITE_FAILED'
    assert result.success_rows==2 and count_orders(session,result.id)==2


def test_outer_rollback_does_not_leave_orders_or_tasks(session):
    before=session.scalar(select(func.count()).select_from(ImportTask))
    ids=[]
    with pytest.raises(RuntimeError):
        with session.begin_nested():
            result=import_purchase_file(session,'A',single_file(),file_name='rollback.xlsx');ids.append(result.id)
            assert result.success_rows==1
            raise RuntimeError('caller aborts')
    assert session.scalar(select(func.count()).select_from(ImportTask))==before
    assert count_orders(session,ids[0])==0


def test_config_change_after_task_creation_uses_snapshot(session):
    task_id=create_import_task(session,'A',single_file(),file_name='version.xlsx')
    customer=session.scalar(select(CustomerConfig).where(CustomerConfig.customer_id=='A'))
    customer.config_version+=1;session.flush()
    result=process_import_task(session,task_id)
    assert result.rows[0].error_code is None and result.success_rows==1


def test_reference_changed_after_match_is_rejected(session,monkeypatch):
    real=import_service.match_purchase_row
    def change_after_match(db,purchase,**kwargs):
        result=real(db,purchase,**kwargs)
        if result.status=='MATCHED':
            ref=db.get(SalesOrder,result.matched_order.id)
            ref.unit_price+=Decimal('1');ref.amount=(ref.unit_price*ref.quantity).quantize(Decimal('.01'));db.flush()
        return result
    monkeypatch.setattr(import_service,'match_purchase_row',change_after_match)
    result=import_purchase_file(session,'A',single_file(),file_name='changed.xlsx')
    assert result.rows[0].error_code=='REFERENCE_CHANGED' and count_orders(session,result.id)==0
    # The injected historical mutation is part of the failed row SAVEPOINT, so it also rolls back.
    assert session.scalar(select(SalesOrder.unit_price).where(SalesOrder.order_no=='SEED-M03-V1-A-TIE-LAST'))==Decimal('12.7')


def test_fatal_deadlock_is_not_falsely_reported_as_recoverable_row_failure(session,monkeypatch):
    def deadlock(*args):raise OperationalError('test',{},DriverOperationalError(1213,'test deadlock'))
    monkeypatch.setattr(import_service.orders,'insert_order',deadlock)
    with pytest.raises(FatalImportError):import_purchase_file(session,'A',single_file(),file_name='fatal.xlsx')


def test_auto_valid_can_reference_and_created_remains_excluded(session):
    result=import_purchase_file(session,'A',single_file(),file_name='auto.xlsx')
    order=session.get(SalesOrder,result.rows[0].created_order_id);order.status='VALID';session.flush()
    parsed=parse_purchase_file(session,'A',single_file(),file_name='auto.xlsx')
    matched=match_purchase_row(session,parsed.rows[0].data)
    assert matched.status=='MATCHED' and matched.matched_order.id==order.id
    order.status='CREATED';session.flush()
    matched=match_purchase_row(session,parsed.rows[0].data)
    assert matched.status=='MATCHED' and matched.matched_order.id==order.source_order_id


def test_inconsistent_success_state_does_not_create_second_order(session):
    task_id=create_import_task(session,'A',single_file(),file_name='inconsistent.xlsx')
    row=session.scalar(select(ImportTaskRow).where(ImportTaskRow.task_id==task_id))
    row.status='SUCCESS';session.flush()
    with pytest.raises(ImportInvariantError):process_task_row(session,task_id,'Sheet1',2)
    assert count_orders(session,task_id)==0


def test_real_two_connection_race_reuses_one_committed_order_and_cleans_test_scope():
    if os.getenv('RUN_DB_TESTS')!='1':pytest.skip('Requires local MySQL RUN_DB_TESTS=1')
    factory=get_session_factory();task_id=None
    reader_ready,writer_done,second_lock,release,second_finished=(Event() for _ in range(5))
    engine=get_engine()
    def watch(conn,cursor,statement,parameters,context,executemany):
        if conn.info.get('m06_worker')=='second' and 'FROM customer_config' in statement and 'FOR UPDATE' in statement:
            second_lock.set()
    event.listen(engine,'before_cursor_execute',watch)
    try:
        # TEMPORARY commit is necessary for independent connections; finally removes only this task's rows/orders.
        with factory() as db, db.begin():
            task_id=create_import_task(db,'A',single_file(),file_name='TEST-M06-CONCURRENCY-'+uuid4().hex+'.xlsx')
        def first():
            assert reader_ready.wait(10)
            with factory() as db, db.begin():
                result=process_task_row(db,task_id,'Sheet1',2)
                writer_done.set()
                assert release.wait(10)
                return result
        def second():
            try:
                with factory() as db, db.begin():
                    connection=db.connection();connection.info['m06_worker']='second'
                    try:
                        # Establish an OLD REPEATABLE READ snapshot before winner commits.
                        assert db.scalar(select(ImportTaskRow.status).where(ImportTaskRow.task_id==task_id))=='PENDING'
                        reader_ready.set();assert writer_done.wait(10)
                        return process_task_row(db,task_id,'Sheet1',2)
                    finally:connection.info.pop('m06_worker',None)
            finally:second_finished.set()
        with ThreadPoolExecutor(max_workers=2) as executor:
            one=executor.submit(first);two=executor.submit(second)
            try:
                assert writer_done.wait(10) and second_lock.wait(10)
                assert not second_finished.wait(0.2)  # Second writer waits on the shared customer lock until first commit.
            finally:release.set()
            winner,reused=one.result(timeout=15),two.result(timeout=15)
        assert winner.status==reused.status=='SUCCESS'
        assert winner.created_order_id==reused.created_order_id
        with factory() as db:
            assert count_orders(db,task_id)==1
            task=db.get(ImportTask,task_id)
            assert task.status=='SUCCESS' and task.total_rows==task.success_rows==1
            assert task.unmatched_rows==task.failed_rows==0
    finally:
        release.set();reader_ready.set();writer_done.set()
        event.remove(engine,'before_cursor_execute',watch)
        if task_id is not None:
            with factory() as db, db.begin():
                task=db.get(ImportTask,task_id)
                assert task is not None and task.file_name.startswith('TEST-M06-CONCURRENCY-')
                db.execute(delete(ImportTaskRow).where(ImportTaskRow.task_id==task_id))
                db.execute(delete(SalesOrder).where(SalesOrder.import_task_id==task_id,SalesOrder.source_type=='AUTO'))
                db.execute(delete(ImportTask).where(ImportTask.id==task_id))
        with factory() as db:
            assert db.get(ImportTask,task_id) is None and count_orders(db,task_id)==0


def test_repeated_business_keys_require_review_before_second_order(session):
    content=workbook_bytes(rows=[['1206692205','E0112-000060',1],['1206692205','E0112-000060',2]])
    result=import_purchase_file(session,'A',content,file_name='two-purchases.xlsx')
    assert result.total_rows == 2 and result.success_rows == 1 and result.review_rows == 1
    first, second = result.rows
    assert first.status == 'SUCCESS' and second.status == 'REVIEW_REQUIRED'
    assert second.created_order_id is None and count_orders(session,result.id) == 1
    from app.schemas.purchase_review import PurchaseReviewRequest
    from app.services.purchase_review import review_purchase
    reviewed = review_purchase(session,result.id,second.id,PurchaseReviewRequest(
        request_key=uuid4(), expected_revision=session.get(ImportTaskRow,second.id).manual_revision, decision='NEW',
        note='测试核实：数量不同不能自动视为新采购，现人工确认追加采购'))
    assert reviewed['row'].status == 'SUCCESS' and count_orders(session,result.id) == 2
    assert len({first.created_order_id,reviewed['row'].created_order_id}) == 2


def test_b_optional_material_is_not_inherited_from_history(session):
    content=workbook_bytes(headers=['单号','机型','数量'],rows=[['01-202505-477','Q55D71B',1]])
    result=import_purchase_file(session,'B',content,file_name='optional.xlsx')
    assert result.success_rows==1 and result.rows[0].created_order.customer_material_code is None

@pytest.mark.parametrize('change',[{'quantity':'0'},{'customer_id':'B'},{'row_number':99}])
def test_corrupted_stored_purchase_snapshot_does_not_build(session,change):
    task_id=create_import_task(session,'A',single_file(),file_name='corrupt.xlsx')
    row=session.scalar(select(ImportTaskRow).where(ImportTaskRow.task_id==task_id))
    row.normalized_data={**row.normalized_data,**change};session.flush()
    result=process_import_task(session,task_id)
    assert result.success_rows==0 and result.rows[0].error_code=='INVALID_INPUT'
    assert count_orders(session,task_id)==0


def test_missing_header_is_recorded_as_file_failure_without_fake_rows(session):
    content=workbook_bytes(headers=['采购订单号','订单数量'],rows=[['1206692205',1]])
    result=import_purchase_file(session,'A',content,file_name='missing-column.xlsx')
    assert result.status=='FAILED' and result.total_rows==0
    assert result.error_message.startswith('MISSING_COLUMN:') and count_orders(session,result.id)==0


def test_task_summary_cannot_claim_counts_different_from_actual_rows(session):
    from app.schemas.import_task import ImportTaskResult
    result=import_purchase_file(session,'A',single_file(),file_name='counts.xlsx')
    payload=result.model_dump();payload.update(success_rows=0,failed_rows=1)
    with pytest.raises(ValueError):ImportTaskResult.model_validate(payload)


def test_database_valid_reference_but_purchase_amount_overflow_is_rejected(session):
    base=session.scalar(select(SalesOrder).where(SalesOrder.order_no=='SEED-M03-V1-A-TIE-LAST'))
    values={c.name:getattr(base,c.name) for c in SalesOrder.__table__.c if c.name!='id'}
    business_key='OVERFLOW-'+uuid4().hex
    values.update(order_no='TEST-M06-OVERFLOW-'+uuid4().hex,customer_order_no=business_key,
                  unit_price=Decimal('99999999999999.9999'))
    values['amount']=calculate_amount(values['unit_price'],values['quantity'])
    session.add(SalesOrder(**values));session.flush()
    content=workbook_bytes(rows=[[business_key,'E0112-000060','999999999999999']])
    result=import_purchase_file(session,'A',content,file_name='overflow.xlsx')
    assert result.rows[0].status=='INVALID_DATA' and result.rows[0].error_code=='AMOUNT_OVERFLOW'
    assert result.success_rows==0 and count_orders(session,result.id)==0


# This module owns a fresh legacy baseline; no managed database is queried.
pytestmark = pytest.mark.usefixtures("legacy_runtime")
