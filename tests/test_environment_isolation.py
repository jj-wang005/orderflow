import json,os,sys
from pathlib import Path
from uuid import uuid4
from decimal import Decimal
import pytest
from pydantic import ValidationError
from sqlalchemy import text,select,func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from app.core.config import Settings,ROOT
from app.models import Base,EnvironmentIdentity,SalesOrder,ImportTask
from scripts import environment_admin as admin
from scripts.demo_dataset import seed,verify
from tests.db_isolation import temporary_schema_engine
from app.main import app
from app.core.database import get_db,get_engine
from tests.test_v2_batch_import import configure,upload,excel,data,rows,review
from tests.test_ai_drafts import create,edited,confirm
from tests.test_order_status import change
from app.services.ai_extraction import get_extraction_service,AIExtractionService,MockAdapter

pytestmark=pytest.mark.skipif(os.getenv('RUN_DB_TESTS')!='1',reason='Explicit order_test environment required')

@pytest.mark.parametrize('profile,name',[('demo','order_test'),('test','order_demo'),('test','order_automation')])
def test_mismatched_environment_fails_closed(profile,name):
 with pytest.raises(ValidationError):Settings(_env_file=None,app_env=profile,db_name=name,db_user='dummy',db_password='dummy')

@pytest.mark.parametrize('profile',['demo','test'])
def test_full_22_samples_have_19_qualified_references(profile):
 with temporary_schema_engine('order_automation_envcheck_') as engine:
  with Session(engine) as session, session.begin(): seed(session,profile)
  with Session(engine) as session:
   result=verify(session);assert len(result)==22
   assert {cid:sum(r['status']=='MATCHED' for r in result if r['customer']==cid) for cid in 'ABC'}=={'A':9 if profile=='demo' else 8,'B':3 if profile=='demo' else 4,'C':7}


@pytest.mark.skip(reason='Requires managed-account cross-database grant verification; prohibited in isolated regression')
@pytest.mark.parametrize('profile',['demo','test'])
def test_runtime_account_cannot_access_other_databases(profile):
 settings=Settings(_env_file=ROOT/('.env.'+profile),app_env=profile)
 from sqlalchemy import create_engine
 engine=create_engine(settings.database_url,hide_parameters=True)
 try:
  for forbidden in ['order_automation',admin.NAMES['test' if profile=='demo' else 'demo']]:
   with engine.connect() as c,pytest.raises(SQLAlchemyError):c.exec_driver_sql('SELECT count(*) FROM `'+forbidden+'`.sales_order')
 finally:engine.dispose()

def test_existing_target_initializer_refuses_without_overwriting(monkeypatch):
 # Unit contract: initializer must stop at the existing-schema probe, before any write.
 from unittest.mock import MagicMock
 engine=MagicMock();conn=engine.connect.return_value.__enter__.return_value
 conn.exec_driver_sql.return_value=[('order_demo',)]
 monkeypatch.setattr(admin,'admin_engine',lambda *args:engine)
 with pytest.raises(RuntimeError,match='已存在'):admin.initialize_all()
 assert conn.exec_driver_sql.call_count==1
 assert conn.exec_driver_sql.call_args.args==('SHOW DATABASES',)
 conn.execute.assert_not_called()
 conn.scalar.assert_not_called()


def test_confirmation_wrong_token_is_non_destructive(monkeypatch):
 # No subprocess and no real managed connection: exercise only the CLI confirmation gate.
 from unittest.mock import MagicMock
 engine=MagicMock()
 monkeypatch.setattr(admin,'admin_engine',lambda *args:engine)
 monkeypatch.setattr(admin,'assert_target',lambda *args:None)
 def forbidden(*args):pytest.fail('Reset must never run without the exact confirmation')
 monkeypatch.setattr(admin,'reset',forbidden)
 monkeypatch.setattr(sys,'argv',['environment_admin','reset','--profile','test'])
 monkeypatch.setattr('builtins.input',lambda prompt:'NO')
 with pytest.raises(RuntimeError,match='未明确确认'):admin.main()


def test_reset_detects_open_application_connection():
 # The busy-connection check is real MySQL, but scoped to a disposable schema.
 with temporary_schema_engine('order_automation_envcheck_') as engine:
  with engine.connect() as held,engine.connect() as verifier:
   held.exec_driver_sql('SELECT 1')
   with pytest.raises(RuntimeError,match='仍有连接'):admin.assert_idle(verifier)


def test_history_clear_helper_always_refuses_without_writing():
 class ReadOnlyProbe:
  def scalar(self,statement):return 'order_automation'
  def exec_driver_sql(self,*args):raise AssertionError('No DML permitted')
 with pytest.raises(RuntimeError,match='禁止清理'):admin.clear_business(ReadOnlyProbe())

def test_missing_test_config_never_falls_back_to_history(monkeypatch,tmp_path):
 monkeypatch.setenv('APP_ENV','test')
 import app.core.config as config
 monkeypatch.setattr(config,'ROOT',tmp_path)
 (tmp_path/'.env').write_text('DB_NAME=order_automation\nDB_USER=dummy\nDB_PASSWORD=dummy\n',encoding='utf-8')
 config.get_settings.cache_clear()
 try:
  with pytest.raises(RuntimeError,match='缺少'):config.get_settings()
 finally:config.get_settings.cache_clear()

def test_invalid_test_password_never_falls_back(monkeypatch):
 # Unit contract only: failed connection construction must not retry a different URL.
 from unittest.mock import Mock
 from sqlalchemy.exc import OperationalError
 from app.core import database
 database.get_engine.cache_clear()
 factory=Mock(side_effect=OperationalError('connection',{},Exception('invalid test credential')))
 monkeypatch.setattr(database,'create_engine',factory)
 try:
  with pytest.raises(OperationalError):database.get_engine()
  assert factory.call_count==1
  assert factory.call_args.args[0].database=='order_test'
 finally:database.get_engine.cache_clear()


def test_cross_environment_restore_rejected(monkeypatch,tmp_path):
 monkeypatch.setattr(admin,'ROOT',tmp_path)
 folder=tmp_path/'.backups'/'test';folder.mkdir(parents=True)
 path=folder/'copy.json';payload={'profile':'demo','database':'order_demo','version':'0008','tables':{}}
 raw=json.dumps(payload).encode();path.write_bytes(raw);path.with_suffix('.sha256').write_text(__import__('hashlib').sha256(raw).hexdigest())
 with pytest.raises(RuntimeError):admin.load_backup(path,'test')
 path.write_bytes(b'changed')
 with pytest.raises(RuntimeError,match='校验'):admin.load_backup(path,'test')

def test_complete_foreign_key_backup_roundtrip_and_rollback_reset():
 with temporary_schema_engine('order_automation_envcheck_') as engine:
  with Session(engine) as s,s.begin():
   s.add(EnvironmentIdentity(id=1,profile='test',instance_id=str(uuid4()),dataset_version='roundtrip'))
   seed(s,'test')
  def db():
   with Session(engine,expire_on_commit=False) as s:yield s
  app.dependency_overrides[get_db]=db;app.dependency_overrides[get_extraction_service]=lambda:AIExtractionService(MockAdapter())
  try:
   with TestClient(app) as c:
    configure(c)
    with Session(engine) as s,s.begin():s.add(SalesOrder(order_no='ROUNDTRIP-SEED',customer_id='D',customer_order_no='PO-01',customer_material_code='MAT',product_code='P',product_name='模拟参考',quantity=800,unit_price=Decimal('2.5'),amount=2000,status='VALID',source_type='SEED'))
    draft=edited(c,create(c));confirmed=data(confirm(c,draft));task=data(c.get('/api/v1/imports/'+str(confirmed['import_task_id'])));r=rows(c,task)[0]
    order=data(c.get('/api/v1/orders/'+str(r['created_order_id'])));data(change(c,order))
    next_task=data(upload(c,excel(quantity=13)),201);data(review(c,next_task,rows(c,next_task)[0]))
    failed=data(upload(c,b'broken'),201);fixed=data(upload(c,excel('NEW-PO'),retry=failed['task_id']),201);row=rows(c,fixed)[0]
    data(c.post(f"/api/v1/imports/{fixed['task_id']}/rows/{row['id']}/correction",json={'request_key':str(uuid4()),'expected_revision':0,'values':{'customer_order_no':'NEW-PO','customer_material_code':'MAT','quantity':'14'},'note':'独立库修正用于备份恢复验证'}))
   with engine.connect() as conn:
    before=admin.capture_tables(conn);assert all(before[name] for name in admin.DELETE_ORDER)
    conn.commit()
    with conn.begin():
     admin.clear_business(conn)
     with Session(bind=conn,join_transaction_mode='create_savepoint') as s:seed(s,'test');verify(s);s.commit()
     # Deliberately roll back rebuilding, prove the old full graph survives.
     conn.rollback()
    assert admin.capture_tables(conn)==before;conn.commit()
    with conn.begin():admin.restore_rows(conn,{'tables':before})
    assert admin.capture_tables(conn)==before
  finally:app.dependency_overrides.pop(get_db,None);app.dependency_overrides.pop(get_extraction_service,None)

@pytest.mark.parametrize('batch,expected',[('TEST-Q499','NOT_MATCHED'),('TEST-Q500','NOT_MATCHED'),('TEST-Q501','MATCHED')])
def test_c_boundary_quantities_are_history_not_purchase(batch,expected, environment_engine):
 from app.schemas.purchase_row import NormalizedPurchaseRow
 from app.services.matching_engine import match_purchase_row
 with Session(environment_engine) as s:
  p=NormalizedPurchaseRow(customer_id='C',customer_batch=batch,customer_material_code='TEST-MAT',quantity=Decimal(7),sheet_name='test',row_number=1,raw_data={})
  assert match_purchase_row(s,p).status==expected

@pytest.mark.parametrize('po,expected',[('TEST-CREATED','NOT_MATCHED'),('TEST-INVALID','NOT_MATCHED'),('TEST-ZERO','INVALID_REFERENCE'),('TEST-CONFLICT','AMBIGUOUS_MATCH'),('TEST-LATEST','MATCHED')])
def test_test_only_safety_scenarios(po,expected, environment_engine):
 from app.schemas.purchase_row import NormalizedPurchaseRow
 from app.services.matching_engine import match_purchase_row
 with Session(environment_engine) as s:
  p=NormalizedPurchaseRow(customer_id='B',customer_order_no=po,customer_model='TEST-MODEL',quantity=Decimal(1),sheet_name='test',row_number=1,raw_data={})
  result=match_purchase_row(s,p);assert result.status==expected
  if expected=='MATCHED':assert result.matched_order.unit_price==Decimal(3)


@pytest.fixture(scope='module')
def environment_engine():
 with temporary_schema_engine('order_automation_envcheck_') as engine:
  with Session(engine) as session, session.begin():seed(session,'test')
  yield engine
