"""Configurable matching via real HTTP/MySQL, exclusively in a disposable schema.

One module schema plus rollback per case avoids relying on mutable demo/test data.
"""
import json
import os
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from itertools import combinations
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.main import app
from app.models import SalesOrder, ImportTask, ImportTaskRow, MatchRule, ConfigurationVersion
from app.repositories.customer_configuration import (
    initialize_configurations, load_configuration, task_configuration, ConfigurationError,
)
from app.schemas.purchase_row import NormalizedPurchaseRow
from app.services.customer_configuration import read_configuration_file
from app.services.import_service import create_import_task, process_import_task
from app.services.matching_engine import match_purchase_row
from tests.db_isolation import temporary_schema_engine
from tests.test_api import data, upload
from tests.test_excel_parser import workbook_bytes

pytestmark = pytest.mark.skipif(os.getenv('RUN_DB_TESTS') != '1', reason='Disposable MySQL only')
FIELDS = ('customer_order_no', 'customer_material_code', 'customer_model', 'customer_batch')
VALUES = dict(zip(FIELDS, ('PO-1', 'MAT-1', 'MODEL-1', 'BATCH-1')))
COMBINATIONS = [list(c) for n in range(1, 5) for c in combinations(FIELDS, n)]


from tests.matching_fixtures import generic_engine, generic_api


def save(client, cid, fields, quantity=None):
    if cid == 'D' and not any(c['customer_id'] == cid for c in data(client.get('/api/v1/customers'))):
        data(client.post('/api/v1/customers', json={'customer_id': cid, 'customer_name': '配置新增 D'}), 201)
    old = data(client.get('/api/v1/customers/' + cid))
    mapping = {f: f for f in [*fields, 'quantity']}
    body = dict(expected_version=old['config_version'], customer_name=old['customer_name'],
                sheet_name='Sheet1', header_row=1, field_mapping=mapping, match_fields=fields,
                historical_quantity=quantity, enabled=True,
                source_columns=[{'index': i, 'name': f} for i, f in enumerate(mapping, 1)])
    saved = data(client.put('/api/v1/customers/' + cid + '/config', json=body))
    return saved, body


def history(conn, cid='D', **changes):
    values = dict(order_no=uuid4().hex, customer_id=cid, **VALUES, product_code='PRODUCT',
                  product_name='隔离产品', unit_price=Decimal('2.5'), quantity=Decimal(800),
                  status='VALID', source_type='SEED', created_at=datetime(2026, 1, 1))
    values.update(changes)
    values.setdefault('amount', (values['unit_price'] * values['quantity']).quantize(Decimal('.01')))
    return conn.execute(SalesOrder.__table__.insert().values(**values)).inserted_primary_key[0]


def excel(fields, **changes):
    values = {**VALUES, 'quantity': 12, **changes}
    return workbook_bytes([*fields, 'quantity'], [[values[f] for f in [*fields, 'quantity']]])


def row(client, task):
    return data(client.get(f"/api/v1/imports/{task['task_id']}/rows"))['items'][0]


def purchase(cid, fields):
    return NormalizedPurchaseRow(customer_id=cid, **{f: VALUES[f] for f in fields},
                                 quantity=Decimal(12), raw_data={}, sheet_name='Sheet1', row_number=2)


@pytest.mark.parametrize('cid', ['A', 'B', 'C', 'D'])
@pytest.mark.parametrize('fields', COMBINATIONS, ids=lambda f: '+'.join(f))
def test_all_supported_combinations_apply_every_condition_and_can_build(generic_api, cid, fields):
    client, conn = generic_api
    saved, body = save(client, cid, fields)
    assert saved['configuration']['rule']['validation_config'] == {'extra_required_fields': [], 'consistency_fields': []}
    target = history(conn, cid)
    # Each chosen condition must eliminate a different-product decoy; none may be dropped.
    for f in fields:
        history(conn, cid, **{f: 'DIFFERENT', 'product_code': 'DECOY-' + f})
    history(conn, 'B' if cid != 'B' else 'A', product_code='OTHER-CUSTOMER')
    history(conn, cid, status='CREATED', product_code='INELIGIBLE')
    with Session(bind=conn, join_transaction_mode='create_savepoint') as db:
        config = load_configuration(db, cid)
        assert set(config.rule.required_fields) == {*fields, 'quantity'}
        matched = match_purchase_row(db, purchase(cid, fields), config=config)
        assert matched.status == 'MATCHED' and matched.matched_order.id == target
    preview = data(client.post('/api/v1/customers/' + cid + '/config/preview',
                              data={'configuration': json.dumps(body)},
                              files={'file': ('generic.xlsx', excel(fields))}))
    assert preview['valid_rows'] == 1
    task = data(upload(client, cid, excel(fields)), 201)
    r = row(client, task)
    if not {'customer_order_no', 'customer_batch'} & set(fields):
        # Procurement identity review stays independent of history matching.
        assert r['status'] == 'REVIEW_REQUIRED'
        r = data(client.post(f"/api/v1/imports/{task['task_id']}/rows/{r['id']}/purchase-review", json={
            'request_key': str(uuid4()), 'expected_revision': 0, 'decision': 'NEW',
            'note': '隔离测试：客户已核实独立采购需求'}))['row']
    assert r['status'] == 'SUCCESS' and r['matched_order_id'] == target
    order = data(client.get('/api/v1/orders/' + str(r['created_order_id'])))
    assert order['product_code'] == 'PRODUCT' and Decimal(order['amount']) == Decimal(30)
    assert Decimal(order['quantity']) == 12 and Decimal(order['unit_price']) == Decimal('2.5')
    explanation = data(client.get(f"/api/v1/imports/{task['task_id']}/rows/{r['id']}/match-explanation"))
    assert explanation['match_fields'] == fields and explanation['current_result']['status'] == 'MATCHED'
    with Session(bind=conn, join_transaction_mode='create_savepoint') as db:
        assert process_import_task(db, task['task_id']).rows[0].created_order_id == r['created_order_id']
    assert conn.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.source_type == 'AUTO')) == 1


@pytest.mark.parametrize('missing', [*FIELDS, 'quantity'])
def test_missing_any_selected_value_blocks_import_and_preview(generic_api, missing):
    client, conn = generic_api
    _, body = save(client, 'D', list(FIELDS))
    history(conn)
    blob = excel(list(FIELDS), **{missing: None})
    preview = data(client.post('/api/v1/customers/D/config/preview',
                              data={'configuration': json.dumps(body)}, files={'file': ('missing.xlsx', blob)}))
    assert preview['valid_rows'] == 0 and preview['invalid_rows'] == 1
    assert missing in preview['rows'][0]['errors'][0]['fields']
    r = row(client, data(upload(client, 'D', blob), 201))
    assert r['status'] == 'INVALID_DATA' and r['created_order_id'] is None
    assert missing in r['error_message']


@pytest.mark.parametrize('cid', ['C', 'D'])
@pytest.mark.parametrize('condition,expected', [
    (None, 'MATCHED'), ({'operator': 'gt', 'value': '500'}, 'NOT_MATCHED'),
    ({'operator': 'gte', 'value': '500'}, 'MATCHED'),
    ({'operator': 'gte', 'value': '1000'}, 'NOT_MATCHED'),
    ({'operator': 'eq', 'value': '500'}, 'MATCHED'),
])
def test_quantity_filters_are_configured_for_history_only(generic_api, cid, condition, expected):
    client, conn = generic_api
    save(client, cid, ['customer_order_no'], condition)
    history(conn, cid, quantity=Decimal(500))
    with Session(bind=conn, join_transaction_mode='create_savepoint') as db:
        result = match_purchase_row(db, purchase(cid, ['customer_order_no']))
        assert result.status == expected  # Current purchase quantity is 12, never the threshold.


@pytest.mark.parametrize('fields', [list(FIELDS[:n]) for n in range(1, 5)])
@pytest.mark.parametrize('fault,expected', [('conflict', 'AMBIGUOUS_MATCH'), ('price', 'INVALID_REFERENCE'),
                                         ('product', 'INVALID_REFERENCE'), ('none', 'NOT_MATCHED')])
def test_safety_checks_are_identical_for_any_number_of_keys(generic_api, fields, fault, expected):
    client, conn = generic_api
    save(client, 'D', fields)
    if fault != 'none':
        history(conn)
        if fault == 'conflict':
            history(conn, product_code='OTHER')
        elif fault == 'price':
            history(conn, created_at=datetime(2026, 2, 1), unit_price=Decimal(0))
        else:
            history(conn, created_at=datetime(2025, 1, 1), product_code='')
    with Session(bind=conn, join_transaction_mode='create_savepoint') as db:
        result = match_purchase_row(db, purchase('D', fields))
        assert result.status == expected and result.matched_order is None
    r = row(client, data(upload(client, 'D', excel(fields)), 201))
    assert r['status'] == {'AMBIGUOUS_MATCH': 'AMBIGUOUS', 'INVALID_REFERENCE': 'INVALID_DATA', 'NOT_MATCHED': 'NOT_MATCHED'}[expected]
    assert r['created_order_id'] is None


def test_same_product_multiple_models_materials_and_latest_tie_break(generic_api):
    client, conn = generic_api
    save(client, 'D', ['customer_material_code'])
    history(conn, customer_model='ANOTHER-MODEL', unit_price=Decimal(1))
    history(conn, created_at=datetime(2026, 2, 1), unit_price=Decimal(3))
    newest = history(conn, customer_model='THIRD-MODEL', created_at=datetime(2026, 2, 1), unit_price=Decimal(4))
    with Session(bind=conn, join_transaction_mode='create_savepoint') as db:
        result = match_purchase_row(db, purchase('D', ['customer_material_code']))
        assert result.status == 'MATCHED' and result.matched_order.id == newest
        assert result.matched_order.unit_price == 4


@pytest.mark.parametrize('cid', ['A', 'B', 'C'])
def test_old_snapshot_and_correction_keep_original_conditions_after_new_rule(generic_api, cid):
    client, conn = generic_api
    cfg = next(c for c in read_configuration_file() if c.customer.customer_id == cid)
    headers = list(cfg.customer.field_mapping)
    blob = workbook_bytes(headers, [[{**VALUES, 'quantity': 12}[f] for f in cfg.customer.field_mapping.values()]])
    target = history(conn, cid)
    # Old A/B/C effective second key rejects the different-product decoy.
    second = 'customer_model' if cid == 'B' else 'customer_material_code'
    history(conn, cid, **{second: 'DIFFERENT', 'product_code': 'OTHER'})
    first = cfg.rule.match_fields[0]
    if cid == 'C':
        history(conn, cid, quantity=Decimal(500), product_code='BELOW-THRESHOLD')
    with Session(bind=conn, join_transaction_mode='create_savepoint') as db:
        task_id = create_import_task(db, cid, blob, file_name='old.xlsx')
        original = deepcopy(db.get(ImportTask, task_id).config_snapshot)
        db.commit()
    # An invalid row can still be corrected under the old snapshot after saving.
    bad = workbook_bytes(headers, [[{**VALUES, 'quantity': -1}[f] for f in cfg.customer.field_mapping.values()]])
    invalid = data(upload(client, cid, bad), 201)
    invalid_row = row(client, invalid)
    saved, _ = save(client, cid, [first])
    assert saved['versions'][1]['snapshot']['rule'] == original['rule']
    with Session(bind=conn, join_transaction_mode='create_savepoint') as db:
        t = db.get(ImportTask, task_id)
        assert t.config_snapshot == original and task_configuration(db, t).rule.model_dump(mode='json') == original['rule']
        processed = process_import_task(db, task_id)
        assert processed.rows[0].status == 'SUCCESS' and processed.rows[0].matched_order_id == target
        db.commit()
    url = f"/api/v1/imports/{invalid['task_id']}/rows/{invalid_row['id']}/correction"
    ctx = data(client.get(url))
    assert {f['field'] for f in ctx['fields'] if f['required']} == set(cfg.rule.required_fields)
    corrected = data(client.post(url, json={'request_key': str(uuid4()), 'expected_revision': 0,
        'note': '按原快照补全数量', 'values': {f: str({**VALUES, 'quantity': 12}[f]) for f in cfg.customer.field_mapping.values()}}))['row']
    # Existing procurement review remains separate; a correction never auto-resolves it.
    assert corrected['status'] == 'REVIEW_REQUIRED'
    explain = data(client.get(f"/api/v1/imports/{invalid['task_id']}/rows/{invalid_row['id']}/match-explanation"))
    assert explain['current_result']['status'] == 'MATCHED' and explain['current_result']['matched_order']['id'] == target
    new_task = data(upload(client, cid, excel([first])), 201)
    assert new_task['config_version'] == 2
    new_row = row(client, new_task)
    explain = data(client.get(f"/api/v1/imports/{new_task['task_id']}/rows/{new_row['id']}/match-explanation"))
    assert explain['configured_match_fields'] == [first] and explain['current_result']['status'] == 'AMBIGUOUS_MATCH'


def test_basic_rename_preserves_legacy_validation_and_snapshotless_task_receipt(generic_api):
    client, conn = generic_api
    # Include a non-comparison legacy required field to catch silent loss on rename.
    rule = conn.execute(select(MatchRule.__table__).where(MatchRule.customer_id == 'A')).mappings().one()
    customer = data(client.get('/api/v1/customers/A'))['configuration']['customer']
    conn.execute(MatchRule.__table__.update().where(MatchRule.id == rule['id']).values(
        validation_config={'extra_required_fields': ['customer_material_code', 'customer_batch'],
                           'consistency_fields': ['customer_material_code']}))
    from app.models import CustomerConfig
    conn.execute(CustomerConfig.__table__.update().where(CustomerConfig.customer_id == 'A').values(
        field_mapping={**customer['field_mapping'], '批次': 'customer_batch'}))
    before = data(client.get('/api/v1/customers/A'))['configuration']
    saved = data(client.patch('/api/v1/customers/A', json={'expected_version': 1, 'customer_name': '仅改名'}))
    after = saved['configuration']['rule']
    assert {k: v for k, v in after.items() if k != 'rule_version'} == {k: v for k, v in before['rule'].items() if k != 'rule_version'}
    with Session(bind=conn, join_transaction_mode='create_savepoint') as db:
        # No snapshot field mutation: emulate a pre-snapshot task in memory.
        from types import SimpleNamespace
        legacy = SimpleNamespace(customer_id='A', config_version=1, rule_version=1, config_snapshot=None)
        original = task_configuration(db, legacy)
        assert original.rule.model_dump(mode='json') == before['rule']
        assert legacy.config_snapshot is None
        with pytest.raises(ConfigurationError):
            task_configuration(db, SimpleNamespace(customer_id='A', config_version=99, rule_version=99, config_snapshot=None))


@pytest.mark.parametrize('fields', [[], ['product_code'], ['customer_order_no; DROP TABLE sales_order'],
                                  ['customer_order_no', 'customer_order_no']])
def test_invalid_keys_never_create_version(generic_api, fields):
    client, conn = generic_api
    saved, body = save(client, 'D', ['customer_order_no'])
    count = conn.scalar(select(func.count()).select_from(ConfigurationVersion))
    body.update(expected_version=1, match_fields=fields)
    assert client.put('/api/v1/customers/D/config', json=body).status_code == 422
    assert conn.scalar(select(func.count()).select_from(ConfigurationVersion)) == count


def test_missing_mapping_never_silently_drops_selected_condition(generic_api):
    client, conn = generic_api
    _, body = save(client, 'D', ['customer_order_no'])
    body.update(expected_version=1, match_fields=['customer_order_no', 'customer_model'])
    response = client.put('/api/v1/customers/D/config', json=body)
    assert response.status_code == 422 and response.json()['code'] == 'INVALID_CONFIGURATION'
    assert data(client.get('/api/v1/customers/D'))['config_version'] == 1
