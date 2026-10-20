"""Safety gates first; actual fixture checks run only when explicitly enabled."""
import os
from types import SimpleNamespace
import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.models import ImportTask, SalesOrder, CustomerConfig
from app.repositories.customer_configuration import load_configuration
from app.services.excel_parser import parse_excel
from app.services.matching_engine import match_purchase_row
from tests.db_isolation import temporary_schema_engine
from tests.shared_fixtures import configured_abc

@pytest.mark.parametrize('database', ['order_demo','order_automation','production','order_test'])
def test_managed_database_connections_rejected_before_driver(database):
    from tests.conftest import guard_connection
    with pytest.raises(RuntimeError, match='forbids connections'):
        guard_connection(SimpleNamespace(name='mysql'), None, [], {'database':database})

@pytest.mark.parametrize('statement', ['SELECT * FROM order_demo.sales_order',
    'SELECT * FROM `order_automation`.sales_order', 'USE order_test'])
def test_cross_schema_sql_rejected(statement):
    from tests.conftest import guard_statement
    with pytest.raises(RuntimeError, match='forbids SQL'):
        guard_statement(None,None,statement,None,None,False)

@pytest.mark.skipif(os.getenv('RUN_DB_TESTS')!='1', reason='Disposable MySQL only')
def test_legacy_fixture_has_own_history_and_no_tasks(legacy_runtime):
    with Session(legacy_runtime) as db:
        assert db.scalar(select(func.count()).select_from(ImportTask)) == 0
        orders = db.scalars(select(SalesOrder)).all()
        assert len(orders) == 31
        assert all(o.order_no.startswith('SEED-M03-V1-') for o in orders)
        assert load_configuration(db,'C').customer.enabled

@pytest.mark.skipif(os.getenv('RUN_DB_TESTS')!='1', reason='Disposable MySQL only')
def test_generic_c_configuration_is_enabled(configured_abc):
    _, engine = configured_abc
    with Session(engine) as db:
        config = load_configuration(db,'C')
        assert config.customer.enabled and config.customer.config_version == 1
        assert db.scalar(select(func.count()).select_from(ImportTask)) == 0

@pytest.mark.skipif(os.getenv('RUN_DB_TESTS')!='1', reason='Disposable MySQL only')
def test_demo_profile_contract_only_in_disposable_schema():
    from scripts.demo_dataset import seed, samples
    from app.repositories.customer_configuration import ConfigurationError
    with temporary_schema_engine('order_automation_envcheck_') as engine:
        with Session(engine) as db, db.begin(): seed(db,'demo')
        with Session(engine) as db:
            c = db.scalar(select(CustomerConfig).where(CustomerConfig.customer_id=='C'))
            assert c.config_version == 0 and not c.enabled and c.field_mapping == {}
            with pytest.raises(ConfigurationError): load_configuration(db,'C')
            for config, _, rows in samples():
                cid = config.customer.customer_id
                if cid == 'A':
                    assert all(match_purchase_row(db,r.data,config=config).status=='MATCHED' for r in rows)
                    assert len(rows) == 9
                if cid == 'B':
                    assert match_purchase_row(db,rows[0].data,config=config).status == 'AMBIGUOUS_MATCH'
                    config.rule.match_fields.append('customer_material_code')
                    assert match_purchase_row(db,rows[0].data,config=config).status == 'MATCHED'
