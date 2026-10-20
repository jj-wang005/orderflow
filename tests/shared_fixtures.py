"""Named deterministic baselines and HTTP fixtures, independent of collected test modules."""
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from fastapi.testclient import TestClient
from app.core import database
from app.main import app
from app.models import Base, SalesOrder
from app.repositories.customer_configuration import initialize_configurations
from app.services.customer_configuration import read_configuration_file
from tests.db_isolation import temporary_schema_engine

ROOT = Path(__file__).resolve().parents[1]

def require_database_tests():
    if os.getenv('RUN_DB_TESTS') != '1':
        pytest.skip('Explicit RUN_DB_TESTS=1 required for disposable MySQL')

def initialize_legacy(engine):
    # Old matching tests own the complete historical manifest, including deliberate faults.
    manifest = json.loads((ROOT/'config/historical_seed_v1.json').read_text(encoding='utf-8'))
    from datetime import datetime
    from decimal import Decimal, ROUND_HALF_UP
    with Session(engine) as db, db.begin():
        initialize_configurations(db, read_configuration_file())
        for spec in manifest['orders']:
            values = {k:v for k,v in spec.items() if k != 'purpose'}
            for field in ('unit_price','quantity'):
                values[field] = Decimal(values[field])
            values['created_at'] = datetime.fromisoformat(values['created_at'])
            values['updated_at'] = values['created_at']
            values['amount'] = (values['quantity']*values['unit_price']).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            db.add(SalesOrder(**values))

@pytest.fixture
def legacy_engine():
    require_database_tests()
    with temporary_schema_engine() as engine:
        initialize_legacy(engine)
        yield engine

@pytest.fixture
def legacy_runtime(request, monkeypatch):
    if os.getenv('RUN_DB_TESTS') != '1':
        yield None
        return
    legacy_engine = request.getfixturevalue('legacy_engine')
    # Existing modules imported these functions by value; redirect every such binding,
    # including worker-session factories. Production functions and guards stay intact.
    original_engine, original_factory = database.get_engine, database.get_session_factory
    factory = sessionmaker(bind=legacy_engine, expire_on_commit=False)
    def engine_getter(): return legacy_engine
    def factory_getter(): return factory
    engine_getter.cache_clear = lambda: None
    factory_getter.cache_clear = lambda: None
    for name, module in list(sys.modules.items()):
        if module is None or not name.startswith(('app.', 'tests.')):
            continue
        if getattr(module, 'get_engine', None) is original_engine:
            monkeypatch.setattr(module, 'get_engine', engine_getter)
        if getattr(module, 'get_session_factory', None) is original_factory:
            monkeypatch.setattr(module, 'get_session_factory', factory_getter)
    yield legacy_engine

def snapshot(connection):
    return {name:[tuple(row) for row in connection.execute(select(table).order_by(table.c.id))]
            for name,table in Base.metadata.tables.items()}

@contextmanager
def rollback_http(engine):
    with engine.connect() as connection:
        outer = connection.begin()
        before = snapshot(connection)
        def dependency():
            with Session(bind=connection, join_transaction_mode='create_savepoint', expire_on_commit=False) as db:
                yield db
        previous = app.dependency_overrides.get(database.get_db)
        app.dependency_overrides[database.get_db] = dependency
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                yield client, connection
        finally:
            if previous is None: app.dependency_overrides.pop(database.get_db, None)
            else: app.dependency_overrides[database.get_db] = previous
            outer.rollback()
            assert snapshot(connection) == before

@pytest.fixture
def legacy_api(legacy_runtime):
    with rollback_http(legacy_runtime) as result:
        yield result

@pytest.fixture
def empty_api():
    require_database_tests()
    with temporary_schema_engine() as engine:
        with rollback_http(engine) as result:
            yield result

@pytest.fixture
def configured_abc():
    require_database_tests()
    from scripts.demo_dataset import seed
    with temporary_schema_engine('order_automation_envcheck_') as engine:
        # Test profile: A 8/9, B 4/5, C enabled 7/8. Demo profile stays unchanged.
        with Session(engine) as db, db.begin(): seed(db, 'test')
        def dependency():
            with Session(engine, expire_on_commit=False) as db: yield db
        previous = app.dependency_overrides.get(database.get_db)
        app.dependency_overrides[database.get_db] = dependency
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                yield client, engine
        finally:
            if previous is None: app.dependency_overrides.pop(database.get_db, None)
            else: app.dependency_overrides[database.get_db] = previous


@pytest.fixture
def abc_api():
    """Enabled ABC configuration only; each scenario supplies its own history."""
    require_database_tests()
    with temporary_schema_engine() as engine:
        with Session(engine) as db, db.begin():
            initialize_configurations(db, read_configuration_file())
        with rollback_http(engine) as result:
            yield result
