"""Focused HTTP/engine checks using the existing outer-rollback isolation pattern.

Only order_demo is connected. Every test mutation rolls back, including API
commits (savepoints). No extra database, schema change or persistent test data.
"""
import json
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.main import app
from app.models import SalesOrder
from app.repositories.customer_configuration import load_configuration
from app.services.matching_engine import match_purchase_row
from app.services.order_service import calculate_amount
from scripts.demo_dataset import ROOT, samples
from scripts.environment_admin import admin_engine, assert_target, capture_tables


def run():
    engine = admin_engine('order_demo')
    checks = {}
    with engine.connect() as conn:
        assert_target(conn, 'demo')
        before = capture_tables(conn)
        conn.commit()
        outer = conn.begin()
        def isolated():
            with Session(bind=conn, join_transaction_mode='create_savepoint', expire_on_commit=False) as session:
                yield session
        app.dependency_overrides[get_db] = isolated
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                def upload(cid, key=None, confirm=False):
                    path = ROOT / 'sample_files' / (cid + '客户采购订单模板.xlsx')
                    return client.post('/api/v1/imports', data={'customer_id': cid, 'duplicate_confirmed': str(confirm).lower()},
                        files={'file': (path.name, path.read_bytes())}, headers={'Idempotency-Key': key} if key else {})
                def data(response, status=200):
                    assert response.status_code == status, response.text
                    return response.json()['data']
                blocked = upload('C')
                assert blocked.status_code == 422 and blocked.json()['code'] == 'CONFIG_ERROR', blocked.text
                checks['c_formal_import_before_save'] = '422 CONFIG_ERROR'
                configs = {c.customer.customer_id: c for c, _, _ in samples()}
                c = configs['C']
                body = {'expected_version': 0, 'customer_name': c.customer.customer_name, 'sheet_name': 'Sheet1',
                    'header_row': 1, 'field_mapping': c.customer.field_mapping, 'match_fields': c.rule.match_fields,
                    'historical_quantity': {'operator': 'gt', 'value': '500'}, 'enabled': True}
                saved = data(client.put('/api/v1/customers/C/config', json=body))
                assert saved['configured'] and saved['config_version'] == 1
                checks['c_first_save'] = 'configured V1'
                keys = {cid: 'baseline-rollback-' + uuid4().hex for cid in 'ABC'}
                tasks = {}
                for cid, expected in [('A', 9), ('B', 3), ('C', 7)]:
                    task = data(upload(cid, keys[cid]), 201)
                    tasks[cid] = task
                    assert task['success_rows'] == expected, task
                    rows = data(client.get(f"/api/v1/imports/{task['task_id']}/rows"))['items']
                    auto = list(conn.execute(select(SalesOrder.__table__).where(SalesOrder.import_task_id == task['task_id'])).mappings())
                    assert len(auto) == expected
                    for order in auto:
                        reference = conn.execute(select(SalesOrder.__table__).where(SalesOrder.id == order['source_order_id'])).mappings().one()
                        parsed = next(r.data for cf, _, rs in samples() if cf.customer.customer_id == cid for r in rs if r.row_number == order['source_row_number'])
                        assert order['product_code'] == reference['product_code'] and order['unit_price'] == reference['unit_price']
                        assert order['quantity'] == parsed.quantity
                        assert order['amount'] == calculate_amount(reference['unit_price'], parsed.quantity)
                        assert order['status'] == 'CREATED' and order['source_type'] == 'AUTO'
                    if cid == 'B':
                        ambiguous = next(r for r in rows if r['row_number'] == 2)
                        assert ambiguous['error_code'] == 'AMBIGUOUS_MATCH' and ambiguous['created_order_id'] is None
                    if cid != 'A':
                        assert next(r for r in rows if r['row_number'] == {'B': 6, 'C': 9}[cid])['created_order_id'] is None
                    else:
                        assert all(r['created_order_id'] is not None for r in rows)
                    checks[cid + '_formal_import'] = {'task': task, 'rows': rows, 'created_orders': len(auto), 'reference_price_product_and_purchase_quantity': True}
                replay = data(upload('A', keys['A']))
                assert replay['task_id'] == tasks['A']['task_id'] and replay['idempotency_replayed']
                duplicate = upload('A', 'baseline-other-' + uuid4().hex)
                assert duplicate.status_code == 409 and duplicate.json()['code'] == 'DUPLICATE_FILE'
                count_before = conn.scalar(select(func.count()).select_from(SalesOrder))
                confirmed = data(upload('A', 'baseline-confirmed-' + uuid4().hex, True), 201)
                assert confirmed['success_rows'] == 0 and conn.scalar(select(func.count()).select_from(SalesOrder)) == count_before
                checks['request_idempotency'] = 'same task replayed without new order'
                checks['file_hash_duplicate'] = '409 DUPLICATE_FILE'
                checks['confirmed_reimport_purchase_guard'] = {'new_orders': 0, 'task': confirmed}
            with Session(bind=conn, join_transaction_mode='create_savepoint') as session:
                config = load_configuration(session, 'B')
                purchase = list(samples())[1][2][0].data.model_copy(update={'customer_order_no': 'BASELINE-SAFETY-ONLY'})
                cases = [
                    ('CREATED excluded', [('B', 'CREATED', 'P1', '10')], 'NOT_MATCHED'),
                    ('INVALID excluded', [('B', 'INVALID', 'P1', '10')], 'NOT_MATCHED'),
                    ('other customer excluded', [('A', 'VALID', 'P1', '10')], 'NOT_MATCHED'),
                    ('zero price rejected', [('B', 'VALID', 'P1', '0')], 'INVALID_REFERENCE'),
                    ('different products rejected', [('B', 'VALID', 'P1', '10'), ('B', 'VALID', 'P2', '11')], 'AMBIGUOUS_MATCH'),
                    ('same product multiple orders valid', [('B', 'VALID', 'P1', '10'), ('B', 'VALID', 'P1', '11')], 'MATCHED'),
                ]
                for label, histories, expected in cases:
                    savepoint = session.begin_nested()
                    try:
                        for cid, status, product, price in histories:
                            unit = Decimal(price)
                            session.add(SalesOrder(order_no='ROLLBACK-'+uuid4().hex, customer_id=cid,
                                customer_order_no=purchase.customer_order_no, customer_model=purchase.customer_model,
                                customer_material_code=purchase.customer_material_code,
                                product_code=product, product_name='模拟回滚核验产品', unit_price=unit,
                                quantity=Decimal(800), amount=unit*800, status=status, source_type='SEED',
                                created_at=datetime(2030, 1, 1), updated_at=datetime(2030, 1, 1)))
                        session.flush()
                        result = match_purchase_row(session, purchase, config=config)
                        assert result.status == expected, (label, result)
                        checks[label] = result.status
                    finally:
                        savepoint.rollback()
        finally:
            app.dependency_overrides.pop(get_db, None)
            outer.rollback()
            after = capture_tables(conn)
            assert after == before, 'Rollback failed to restore complete business graph'
            checks['all_tables_restored_after_outer_rollback'] = True
    engine.dispose()
    path = ROOT / 'docs/reports/demo_baseline/workflow_checks.json'
    path.write_text(json.dumps(checks, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print('Rollback-only HTTP imports, idempotency, duplicate purchase, reference safety: PASS; all tables unchanged')


if __name__ == '__main__':
    run()
