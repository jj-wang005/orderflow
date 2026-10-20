"""Shared configurable-matching fixtures with no dependency on collected tests."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.main import app
from app.core.database import get_db
from app.repositories.customer_configuration import initialize_configurations
from app.services.customer_configuration import read_configuration_file
from tests.db_isolation import temporary_schema_engine
from tests.shared_fixtures import require_database_tests

@pytest.fixture(scope='module')
def generic_engine():
    require_database_tests()
    with temporary_schema_engine() as engine:
        with Session(engine) as db, db.begin():
            initialize_configurations(db, read_configuration_file())
        yield engine


@pytest.fixture
def generic_api(generic_engine):
    with generic_engine.connect() as conn:
        outer = conn.begin()
        def dependency():
            with Session(bind=conn, join_transaction_mode='create_savepoint', expire_on_commit=False) as db:
                yield db
        app.dependency_overrides[get_db] = dependency
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                yield client, conn
        finally:
            app.dependency_overrides.pop(get_db, None)
            outer.rollback()
