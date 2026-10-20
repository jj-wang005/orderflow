"""Seed preflight without touching any database or original workbook."""
from collections import Counter
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.models import CustomerConfig, MatchRule, ConfigurationVersion, SalesOrder
from scripts.demo_dataset import seed, samples


def collect(profile, database):
    session = MagicMock()
    objects = []
    session.add.side_effect = objects.append
    session.scalars.return_value.all.return_value = []
    def scalar(query):
        sql = str(query)
        if sql == 'SELECT DATABASE()':
            return database
        if 'count(' in sql:
            return sum(isinstance(o, SalesOrder) for o in objects) if 'sales_order' in sql else 0
        return None
    session.scalar.side_effect = scalar
    result = seed(session, profile)
    return objects, result


def test_demo_preserves_history_and_creates_real_product_conflict():
    objects, result = collect('demo', 'order_demo')
    orders = [o for o in objects if isinstance(o, SalesOrder)]
    assert Counter(o.customer_id for o in orders) == {'A': 11, 'B': 5, 'C': 8}
    customer = next(o for o in objects if isinstance(o, CustomerConfig) and o.customer_id == 'C')
    assert (customer.field_mapping, customer.enabled, customer.config_version) == ({}, False, 0)
    assert not any(isinstance(o, (MatchRule, ConfigurationVersion)) and o.customer_id == 'C' for o in objects)
    config, _, rows = list(samples())[1]
    assert config.customer.field_mapping['机型'] == 'customer_model'
    assert config.customer.field_mapping['物料编码'] == 'customer_material_code'
    assert config.rule.validation_config.consistency_fields == []
    po, model, material = (getattr(rows[0].data, f) for f in ('customer_order_no', 'customer_model', 'customer_material_code'))
    candidates = [o for o in orders if o.customer_id == 'B' and o.customer_order_no == po and o.customer_model == model]
    assert len({o.product_code for o in candidates}) == 2
    assert len([o for o in candidates if o.customer_material_code == material]) == 1
    assert all(o.status == 'VALID' and o.unit_price > 0 and o.amount == o.quantity * o.unit_price for o in orders)
    assert Counter(o.quantity for o in orders if o.customer_id == 'C') == {Decimal(800): 7, Decimal(499): 1}
    a = [o for o in orders if o.customer_id == 'A' and o.customer_order_no == '1206692206']
    assert len({o.customer_material_code for o in a}) == len({o.product_code for o in a}) == 2
    last = next(o for o in orders if o.order_no == 'DEMO-V1-A-09')
    assert (last.customer_order_no, last.customer_material_code, last.product_code, last.unit_price) == ('1206692209', 'E0111-000006', 'SIM-DEMO-A-09', Decimal('18'))
    assert result['orders'] == 24


def test_test_seed_business_data_remains_unchanged():
    objects, result = collect('test', 'order_test')
    assert result['orders'] == 32
    assert any(isinstance(o, MatchRule) and o.customer_id == 'C' for o in objects)
    assert not any(isinstance(o, SalesOrder) and o.order_no == 'DEMO-V2-B-01-ALT' for o in objects)


@pytest.mark.parametrize('profile,database', [('demo', 'order_test'), ('test', 'order_demo'), ('demo', 'order_automation')])
def test_seed_rejects_other_environment(profile, database):
    with pytest.raises(RuntimeError, match='禁止'):
        collect(profile, database)
