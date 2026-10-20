import os
from uuid import uuid4
from decimal import Decimal
import pytest
from sqlalchemy import inspect, text, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.core.database import get_engine
from app.models import Base, CustomerConfig, SalesOrder, ImportTask, ImportTaskRow

pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="Set RUN_DB_TESTS=1 after configuring and migrating local MySQL")

def test_real_schema():
    from tests.db_isolation import temporary_schema_engine
    with temporary_schema_engine(migrate=True) as engine:
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT DATABASE()")) == engine.url.database
            assert conn.scalar(text("SELECT 1")) == 1
            from alembic.config import Config
            from alembic.script import ScriptDirectory
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
        for name, table in Base.metadata.tables.items():
            columns = {c["name"]: c for c in inspector.get_columns(name)}
            assert set(columns) == set(table.c.keys())
            assert inspector.get_pk_constraint(name)["constrained_columns"] == ["id"]
            assert len(inspector.get_foreign_keys(name)) == len(table.foreign_keys)
            for index in table.indexes:
                assert any(i["name"] == index.name and i["column_names"] == [c.name for c in index.columns] for i in inspector.get_indexes(name))
            for constraint in table.constraints:
                from sqlalchemy import UniqueConstraint
                if isinstance(constraint, UniqueConstraint):
                    assert any(u["column_names"] == [c.name for c in constraint.columns] for u in inspector.get_unique_constraints(name))

def test_insert_query_and_unique_constraints():
    # Roll back the outer transaction, leaving no seed/business records.
    with get_engine().connect() as connection:
        transaction = connection.begin()
        try:
            with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                key = "TEST-" + uuid4().hex
                session.add(CustomerConfig(customer_id=key, customer_name="Module01 test", field_mapping={}))
                session.flush()
                values = dict(order_no=key, customer_id=key, customer_order_no="000123", product_code="TEST", product_name="Test only", unit_price=Decimal("12.5000"), quantity=Decimal("2.000"), amount=Decimal("25.00"), status="VALID", source_type="SEED")
                session.add(SalesOrder(**values)); session.flush()
                found = session.scalar(select(SalesOrder).where(SalesOrder.customer_id == key, SalesOrder.customer_order_no == "000123"))
                assert found.amount == Decimal("25.00")
                with pytest.raises(IntegrityError):
                    with session.begin_nested():
                        session.add(SalesOrder(**values)); session.flush()
                task = ImportTask(customer_id=key, file_name="test.xlsx", file_hash="0" * 64)
                session.add(task); session.flush()
                row_values = dict(task_id=task.id, sheet_name="Sheet1", row_number=2, raw_data={})
                session.add(ImportTaskRow(**row_values)); session.flush()
                with pytest.raises(IntegrityError):
                    with session.begin_nested():
                        session.add(ImportTaskRow(**row_values)); session.flush()
        finally:
            transaction.rollback()


# This module owns a fresh legacy baseline; no managed database is queried.
pytestmark = [*(pytestmark if isinstance(pytestmark, list) else [pytestmark]), pytest.mark.usefixtures("legacy_runtime")]
