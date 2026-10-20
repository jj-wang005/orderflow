"""Shared real-commit competition fixtures, always using a disposable schema."""
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.main import app
from app.core.database import get_db
from app.models import SalesOrder
from app.repositories.customer_configuration import initialize_configurations
from app.services.customer_configuration import read_configuration_file
from tests.shared_fixtures import require_database_tests

@pytest.fixture
def isolated_engine():
 require_database_tests()
 from tests.db_isolation import temporary_schema_engine
 with temporary_schema_engine() as engine:
  config=read_configuration_file()[0].model_copy(deep=True);config.customer.customer_id='D';config.customer.customer_name='D 隔离并发'
  with Session(engine) as db,db.begin():
   initialize_configurations(db,[config])
   db.add(SalesOrder(order_no='ISOLATED-SEED',customer_id='D',customer_order_no='PO-01',customer_material_code='MAT',product_code='P',product_name='并发模拟产品',unit_price=Decimal('2.5'),quantity=Decimal(800),amount=Decimal(2000),status='VALID',source_type='SEED'))
  yield engine

@pytest.fixture
def client(isolated_engine):
 def db():
  with Session(isolated_engine,expire_on_commit=False) as session:yield session
 app.dependency_overrides[get_db]=db
 try:
  with TestClient(app,raise_server_exceptions=False) as c:yield c,isolated_engine
 finally:app.dependency_overrides.pop(get_db,None)
