"""Real multipart/SQL trial checks, only in a disposable MySQL schema."""
import json
import os
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import CustomerConfig, SalesOrder
from app.services import customer_preview
from app.services.matching_engine import match_purchase_row
from app.core.database import get_db
from app.main import app
from fastapi.testclient import TestClient
from tests.test_api import data, snapshot, upload
from tests.test_excel_parser import workbook_bytes
from tests.test_generic_matching import (
    COMBINATIONS, FIELDS, VALUES, history, excel, save, row,
)

pytestmark = pytest.mark.skipif(os.getenv('RUN_DB_TESTS') != '1', reason='Disposable MySQL only')


def draft(client, fields=None):
    fields = fields or ['customer_order_no']
    data(client.post('/api/v1/customers', json={'customer_id': 'D', 'customer_name': '新增客户'}), 201)
    return dict(expected_version=0, customer_name='未保存名称', sheet_name='Sheet1', header_row=1,
                field_mapping={f: f for f in [*FIELDS, 'quantity']}, match_fields=fields,
                historical_quantity=None, enabled=False)


def trial(client, body, blob=None, *, cid='D'):
    return data(client.post(f'/api/v1/customers/{cid}/config/trial-match',
                           data={'configuration': json.dumps(body)},
                           files={'file': ('trial.xlsx', blob if blob is not None else excel(FIELDS))}))


@pytest.mark.parametrize('fields', COMBINATIONS, ids=lambda f: '+'.join(f))
def test_unsaved_draft_all_combinations_reuse_engine_without_writes(generic_api, fields):
    client, conn = generic_api
    body = draft(client, fields)
    target = history(conn)
    for field in fields:
        history(conn, **{field: 'DIFFERENT', 'product_code': 'DECOY-' + field})
    history(conn, 'A', product_code='OTHER-CUSTOMER')
    history(conn, status='CREATED', product_code='NOT-ELIGIBLE')
    before = snapshot(conn)
    statements = []
    def record(_conn, _cursor, statement, *_args):
        statements.append(statement.strip().split()[0].upper())
    event.listen(conn, 'before_cursor_execute', record)
    try:
        with patch.object(customer_preview, 'match_purchase_row', wraps=match_purchase_row) as engine:
            result = trial(client, body, excel(fields))
            assert engine.call_count == 1
            assert engine.call_args.kwargs['config'].rule.match_fields == fields
    finally:
        event.remove(conn, 'before_cursor_execute', record)
    assert not {'INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'CREATE', 'ALTER', 'DROP'} & set(statements)
    assert result['summary'] == dict(matched_rows=1, unmatched_rows=0, ambiguous_rows=0, other_rows=0)
    assert result['rows'][0]['result']['matched_order']['id'] == target
    assert result['rows'][0]['match_values'] == {f: VALUES[f] for f in fields}
    assert snapshot(conn) == before
    customer = data(client.get('/api/v1/customers/D'))
    assert customer['config_version'] == 0 and not customer['configured'] and not customer['enabled']


def test_draft_overrides_saved_rule_and_optional_fields_not_required(generic_api):
    client, conn = generic_api
    _, body = save(client, 'D', ['customer_order_no', 'customer_material_code', 'customer_model'])
    history(conn)
    body.update(match_fields=['customer_batch'], expected_version=0, source_columns=None, field_mapping={f:f for f in [*FIELDS, 'quantity']})  # Even a stale draft need not save.
    before = snapshot(conn)
    result = trial(client, body, excel(['customer_batch']))
    assert result['rows'][0]['result']['status'] == 'MATCHED'
    assert snapshot(conn) == before


@pytest.mark.parametrize('condition,expected', [
    (None, 'MATCHED'), ({'operator': 'gt', 'value': '500'}, 'NOT_MATCHED'),
    ({'operator': 'gte', 'value': '500'}, 'MATCHED'), ({'operator': 'eq', 'value': '500'}, 'MATCHED'),
    ({'operator': 'eq', 'value': '501'}, 'NOT_MATCHED'),
])
def test_draft_quantity_filters(generic_api, condition, expected):
    client, conn = generic_api
    body = draft(client)
    body['historical_quantity'] = condition
    history(conn, quantity=Decimal(500))
    assert trial(client, body)['rows'][0]['result']['status'] == expected


def test_summary_errors_and_bounded_ambiguity_candidates(generic_api):
    client, conn = generic_api
    body = draft(client)
    history(conn)
    for i in range(8):
        history(conn, customer_order_no='AMBIGUOUS', product_code=f'P-{i}')
    history(conn, customer_order_no='BAD-PRICE', unit_price=Decimal(0))
    blob = workbook_bytes(['customer_order_no', 'quantity'], [
        ['PO-1', 1], ['MISSING', 2], ['AMBIGUOUS', 3], ['BAD-PRICE', 4], [None, 5], ['PO-1', 1.5],
    ])
    before = snapshot(conn)
    result = trial(client, body, blob)
    assert result['total_rows'] == result['evaluated_rows'] == 6
    assert result['summary'] == dict(matched_rows=1, unmatched_rows=1, ambiguous_rows=1, other_rows=3)
    assert [r['row_number'] for r in result['rows']] == list(range(2, 8))
    assert [r['result']['status'] for r in result['rows']] == [
        'MATCHED', 'NOT_MATCHED', 'AMBIGUOUS_MATCH', 'INVALID_REFERENCE', 'INVALID_INPUT', 'INVALID_INPUT']
    candidates = result['rows'][2]['candidates']
    assert len(candidates) == 2 and len({c['product_code'] for c in candidates}) == 2
    assert all(c['product_name'] and c['order_no'] for c in candidates)
    assert all(r['result']['message'] for r in result['rows'])
    assert snapshot(conn) == before


def test_same_product_latest_order_not_ambiguity(generic_api):
    client, conn = generic_api
    body = draft(client)
    history(conn, unit_price=Decimal(1))
    history(conn, created_at=datetime(2026, 2, 1), unit_price=Decimal(3))
    latest = history(conn, created_at=datetime(2026, 2, 1), unit_price=Decimal(4))
    result = trial(client, body)['rows'][0]['result']
    assert result['status'] == 'MATCHED' and result['matched_order']['id'] == latest
    assert Decimal(result['matched_order']['unit_price']) == 4


@pytest.mark.parametrize('changes', [
    {'unit_price': Decimal(0)}, {'unit_price': Decimal(-1)}, {'product_code': ''},
    {'product_name': ''}, {'amount': Decimal(1)},
])
def test_invalid_newest_reference_never_falls_back(generic_api, changes):
    client, conn = generic_api
    body = draft(client)
    history(conn)
    history(conn, created_at=datetime(2026, 3, 1), **changes)
    result = trial(client, body)['rows'][0]['result']
    assert result['status'] == 'INVALID_REFERENCE' and result['matched_order'] is None


@pytest.mark.parametrize('blob', [b'broken', workbook_bytes(['quantity'], [[12]]),
                                  workbook_bytes(['customer_order_no', 'quantity'], [])])
def test_bad_file_missing_headers_and_no_sample_never_invent_rows(generic_api, blob):
    client, conn = generic_api
    body = draft(client)
    before = snapshot(conn)
    result = trial(client, body, blob)
    assert result['file_error'] and result['total_rows'] == result['evaluated_rows'] == 0
    assert result['rows'] == [] and snapshot(conn) == before


def test_row_limit_bounds_engine_calls_and_reports_actual_total(generic_api):
    client, conn = generic_api
    body = draft(client)
    history(conn)
    blob = workbook_bytes(['customer_order_no', 'quantity'], [['PO-1', 1]] * 102)
    with patch.object(customer_preview, 'match_purchase_row', wraps=match_purchase_row) as engine:
        result = trial(client, body, blob)
    assert engine.call_count == result['evaluated_rows'] == result['row_limit'] == 100
    assert result['total_rows'] == 102 and result['truncated'] and result['summary']['matched_rows'] == 100


def test_reclick_reads_new_history_and_import_rechecks_price_product_and_duplicate(generic_api):
    client, conn = generic_api
    _, body = save(client, 'D', ['customer_order_no'])
    blob = excel(['customer_order_no'])
    first = history(conn)
    assert trial(client, body)['rows'][0]['result']['matched_order']['id'] == first
    second = history(conn, status='CREATED', source_type='AUTO', created_at=datetime(2026, 3, 1), unit_price=Decimal(4))
    assert trial(client, body)['rows'][0]['result']['matched_order']['id'] == first
    conn.execute(SalesOrder.__table__.update().where(SalesOrder.id == second).values(status='VALID'))
    assert trial(client, body)['rows'][0]['result']['matched_order']['id'] == second
    task = data(upload(client, 'D', blob), 201)
    created = row(client, task)
    if created['status'] == 'REVIEW_REQUIRED':
        created = confirm(client, task, created, 'NEW')
    assert created['status'] == 'SUCCESS' and created['matched_order_id'] == second
    saved_order = conn.execute(select(SalesOrder.__table__).where(SalesOrder.id == created['created_order_id'])).mappings().one()
    assert saved_order['unit_price'] == 4
    # Duplicate preview still matches; only formal import executes the purchase guard.
    assert trial(client, body)['summary']['matched_rows'] == 1
    duplicate_response = upload(client, 'D', blob)
    assert duplicate_response.status_code == 409 and duplicate_response.json()['code'] == 'DUPLICATE_FILE'
    duplicate_task = data(client.post('/api/v1/imports', data={'customer_id':'D','duplicate_confirmed':'true'},
        files={'file':('test.xlsx',blob)}),201)
    duplicate = row(client, duplicate_task)
    assert duplicate['status'] == 'REVIEW_REQUIRED'
    duplicate = confirm(client, duplicate_task, duplicate, 'EXISTING', saved_order['id'])
    assert duplicate['status'] == 'DUPLICATE' and duplicate['created_order_id'] is None
    history(conn, product_code='NEW-PRODUCT', created_at=datetime(2026, 4, 1))
    assert trial(client, body)['summary']['ambiguous_rows'] == 1
    # Different procurement quantity avoids the exact-source duplicate check.
    ambiguous_task = data(upload(client, 'D', excel(['customer_order_no'], quantity=13)), 201)
    ambiguous = row(client, ambiguous_task)
    assert ambiguous['status'] in {'REVIEW_REQUIRED', 'AMBIGUOUS'}
    if ambiguous['status'] == 'REVIEW_REQUIRED':
        ambiguous = confirm(client, ambiguous_task, ambiguous, 'NEW')
    assert ambiguous['status'] == 'AMBIGUOUS' and ambiguous['created_order_id'] is None
    assert conn.execute(select(SalesOrder.__table__).where(SalesOrder.id == saved_order['id'])).mappings().one() == saved_order


def test_parsing_preview_never_queries_history(generic_api):
    client, conn = generic_api
    body = draft(client)
    before = snapshot(conn)
    sql = []
    def record(_conn, _cursor, statement, *_args):
        sql.append(statement.lower())
    event.listen(conn, 'before_cursor_execute', record)
    try:
        preview = data(client.post('/api/v1/customers/D/config/preview', data={'configuration': json.dumps(body)},
                                   files={'file': ('parse.xlsx', excel(['customer_order_no']))}))
    finally:
        event.remove(conn, 'before_cursor_execute', record)
    assert preview['valid_rows'] == 1
    assert all('sales_order' not in statement for statement in sql)
    assert snapshot(conn) == before


def test_no_autoflush_of_pending_business_objects(generic_api):
    client, conn = generic_api
    body = draft(client)
    with Session(bind=conn, join_transaction_mode='create_savepoint') as db:
        pending = CustomerConfig(customer_id='NEVER-FLUSH', customer_name='待写入', sheet_name='Sheet1',
                                 header_row=1, field_mapping={}, enabled=False, config_version=0)
        db.add(pending)
        config, parsed = customer_preview.parse_draft(db, 'D', json.dumps(body), excel(FIELDS), 'sample.xlsx')
        customer_preview.trial_match(db, config, parsed)
        assert pending.id is None and pending in db.new


def test_separate_commits_between_clicks_are_visible(generic_engine):
    # Unlike the rollback fixture, this commits only into the disposable schema.
    # Separate HTTP sessions must see newly committed/qualified references.
    with Session(generic_engine) as db, db.begin():
        db.add(CustomerConfig(customer_id='LIVE', customer_name='隔离实时查询', sheet_name='Sheet1',
                              header_row=1, field_mapping={}, enabled=False, config_version=0))
    body = dict(expected_version=0, customer_name='实时查询草稿', sheet_name='Sheet1', header_row=1,
                field_mapping={'customer_order_no': 'customer_order_no', 'quantity': 'quantity'},
                match_fields=['customer_order_no'], historical_quantity=None, enabled=True)
    def dependency():
        with Session(generic_engine) as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    try:
        with TestClient(app) as client:
            assert trial(client, body, cid='LIVE')['summary']['unmatched_rows'] == 1
            with generic_engine.begin() as conn:
                first = history(conn, 'LIVE')
            assert trial(client, body, cid='LIVE')['rows'][0]['result']['matched_order']['id'] == first
            with generic_engine.begin() as conn:
                second = history(conn, 'LIVE', status='CREATED', source_type='AUTO', created_at=datetime(2026, 3, 1))
            assert trial(client, body, cid='LIVE')['rows'][0]['result']['matched_order']['id'] == first
            with generic_engine.begin() as conn:
                conn.execute(SalesOrder.__table__.update().where(SalesOrder.id == second).values(status='PROCESSING'))
            assert trial(client, body, cid='LIVE')['rows'][0]['result']['matched_order']['id'] == second
            with generic_engine.begin() as conn:
                history(conn, 'LIVE', product_code='CONFLICT', created_at=datetime(2026, 4, 1))
            assert trial(client, body, cid='LIVE')['summary']['ambiguous_rows'] == 1
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize('change', [
    {'match_fields': []}, {'match_fields': ['product_code']},
    {'match_fields': ['customer_model', 'customer_model']},
    {'field_mapping': {'customer_order_no': 'customer_order_no'}},
    {'historical_quantity': {'operator': 'gt', 'value': '-1'}},
    {'filter_conditions': {'customer_scope': 'all'}},
])
def test_invalid_trial_drafts_do_not_write(generic_api, change):
    client, conn = generic_api
    body = draft(client)
    body.update(change)
    before = snapshot(conn)
    response = client.post('/api/v1/customers/D/config/trial-match', data={'configuration': json.dumps(body)},
                           files={'file': ('test.xlsx', excel(FIELDS))})
    assert response.status_code == 422 and snapshot(conn) == before


def test_formal_import_requeries_invalidated_reference_price(generic_api):
    client, conn = generic_api
    _, body = save(client, 'D', ['customer_order_no'])
    target = history(conn)
    assert trial(client, body)['summary']['matched_rows'] == 1
    conn.execute(SalesOrder.__table__.update().where(SalesOrder.id == target).values(unit_price=0, amount=0))
    purchase = row(client, data(upload(client, 'D', excel(['customer_order_no'])), 201))
    assert purchase['status'] == 'INVALID_DATA' and purchase['created_order_id'] is None


def confirm(client, task, purchase, decision, existing=None):
    from uuid import uuid4
    return data(client.post(f"/api/v1/imports/{task['task_id']}/rows/{purchase['id']}/purchase-review", json={
        'request_key': str(uuid4()), 'expected_revision': purchase['manual_revision'],
        'decision': decision, 'existing_order_id': existing, 'note': '隔离测试：已核实独立需求或已有采购'}))['row']

from tests.matching_fixtures import generic_engine, generic_api
