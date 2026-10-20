"""Run only matching/configuration/correction regressions on a disposable baseline.

APP_ENV=test RUN_DB_TESTS=1 python -m scripts.verify_generic_matching
Existing order_test/demo/history data is neither reset nor used as the fixture.
"""
import os
import pytest
from sqlalchemy.orm import Session, sessionmaker
from app.core.config import get_settings
from app.repositories.customer_configuration import initialize_configurations
from app.services.customer_configuration import read_configuration_file
from app.models import SalesOrder
from scripts.seed_data import load_seed_orders
from tests.db_isolation import temporary_schema_engine


def main():
    if os.environ.get('APP_ENV') != 'test' or os.environ.get('RUN_DB_TESTS') != '1' or get_settings().db_name != 'order_test':
        raise RuntimeError('Explicit test environment required')
    with temporary_schema_engine() as engine:
        with Session(engine) as db, db.begin():
            initialize_configurations(db, read_configuration_file())
            # Insert known valid fixtures only in this newly created empty schema.
            # Deliberately invalid references are supplied by rollback test fixtures.
            for order in load_seed_orders():
                db.add(SalesOrder(**order.order_values()))
                db.flush()  # Manifest order defines deterministic ID tie breaks.
        import app.core.database as database
        old_engine, old_factory = database.get_engine, database.get_session_factory
        database.get_engine = lambda: engine
        database.get_session_factory = lambda: sessionmaker(bind=engine, expire_on_commit=False)
        try:
            return pytest.main([
                'tests/test_customer_configuration.py', 'tests/test_matching_engine.py',
                'tests/test_v2_customer_management.py', 'tests/test_manual_correction.py',
                '-q', '--tb=short', '--junitxml=docs/reports/generic_matching/legacy_isolated.xml',
            ])
        finally:
            database.get_engine, database.get_session_factory = old_engine, old_factory


if __name__ == '__main__':
    raise SystemExit(main())
