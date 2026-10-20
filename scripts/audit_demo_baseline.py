"""Read-only evidence export after the existing, manually confirmed demo reset.

Run: python -m scripts.audit_demo_baseline --stage first (or final).
Does not initialize, reset, save configuration, import, or construct orders.
"""
import argparse
from collections import Counter
import hashlib
import json

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.models import CustomerConfig, MatchRule, SalesOrder
from app.repositories.customer_configuration import load_configuration
from app.repositories.order_matching import candidate_query
from app.services.matching_engine import match_purchase_row
from app.services.customer_management import detail, suggest_mapping
from scripts.demo_dataset import ROOT, samples, verify
from scripts.environment_admin import admin_engine, assert_target, capture_tables

REPORT = ROOT / 'docs/reports/demo_baseline'


def fingerprints():
    result = {}
    for name in ['order_automation', 'order_test', 'order_demo']:
        engine = admin_engine(name)
        try:
            with engine.connect() as conn:
                tables = {}
                for table in inspect(conn).get_table_names():
                    rows = [list(r) for r in conn.exec_driver_sql('SELECT * FROM `' + table + '` ORDER BY 1')]
                    digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False, default=str, separators=(',', ':')).encode()).hexdigest()
                    tables[table] = {'count': len(rows), 'sha256': digest}
                result[name] = tables
        finally:
            engine.dispose()
    return result


def audit(stage):
    REPORT.mkdir(parents=True, exist_ok=True)
    engine = admin_engine('order_demo')
    output = []
    try:
        with engine.connect() as conn:
            assert_target(conn, 'demo')
            with Session(bind=conn) as session:
                verification = verify(session)
                customers = [detail(session, cid) for cid in 'ABC']
                assert [c['configured'] for c in customers] == [True, True, False]
                assert customers[2]['versions'] == []
                b = load_configuration(session, 'B')
                assert b.rule.match_fields == ['customer_order_no', 'customer_model']
                assert not b.rule.validation_config.consistency_fields
                for template, path, rows in samples():
                    cid = template.customer.customer_id
                    config = template if cid == 'C' else load_configuration(session, cid)
                    scenarios = [('C 草稿: 批次+料号; 历史数量>500' if cid == 'C' else cid + ' 默认', config)]
                    if cid == 'B':
                        three = config.model_copy(deep=True)
                        three.rule.match_fields.append('customer_material_code')
                        scenarios.append(('B 三字段未保存草稿', three))
                    for label, current in scenarios:
                        for row in rows:
                            result = match_purchase_row(session, row.data, config=current)
                            candidates = [dict(r) for r in session.execute(candidate_query(current, row.data)).mappings()]
                            output.append({'customer': cid, 'file': path.name, 'excel_row': row.row_number,
                                'scenario': label, 'match_fields': current.rule.match_fields,
                                'consistency_fields': current.rule.validation_config.consistency_fields,
                                'values': {f: getattr(row.data, f) for f in ['customer_order_no', 'customer_model', 'customer_material_code', 'customer_batch']},
                                'purchase_quantity': str(row.data.quantity), 'status': result.status,
                                'reference_id': result.matched_order.id if result.matched_order else None,
                                'candidate_ids': [r['id'] for r in candidates], 'candidates': candidates,
                                'reference_order_no': result.matched_order.order_no if result.matched_order else None,
                                'product_code': result.matched_order.product_code if result.matched_order else None,
                                'unit_price': str(result.matched_order.unit_price) if result.matched_order else None,
                                'reason': result.message})
                assert len(output) == 27
                tables = capture_tables(conn)
                for name in ['import_task', 'import_task_row', 'manual_correction', 'ai_extraction', 'order_change']:
                    assert not tables[name], name + ' must be empty'
                orders = session.scalars(select(SalesOrder).order_by(SalesOrder.order_no)).all()
                assert Counter(o.customer_id for o in orders) == {'A': 11, 'B': 5, 'C': 8}
                assert all(o.source_type == 'SEED' and o.source_order_id is None and o.import_task_id is None
                    and o.purchase_source_digest is None and o.purchase_reference_digest is None and o.purchase_group_digest is None for o in orders)
                assert all(o.status == 'VALID' and o.unit_price > 0 and o.amount == o.unit_price * o.quantity for o in orders)
                # Stable business representation excludes generated keys and receipt timestamps.
                stable = {
                    'customers': [{k: c[k] for k in ['customer_id', 'enabled', 'config_version', 'configured', 'configuration']} for c in customers],
                    'orders': [{col.name: getattr(o, col.name) for col in SalesOrder.__table__.c if col.name not in ['id']} for o in orders],
                    'results': [{k: r[k] for k in ['customer', 'excel_row', 'scenario', 'status', 'reference_order_no', 'product_code', 'unit_price']} for r in output]}
                suggestions = {c.customer.customer_id: suggest_mapping(p.read_bytes(), p.name) for c, p, _ in samples()}
                report = {'stage': stage, 'verification': verification, 'customers': customers, 'rows': output,
                          'seed_counts': dict(Counter(o.customer_id for o in orders)), 'suggestions': suggestions,
                          'stable_business': stable, 'clean_initial_state': True}
                # Baseline C has no usable formal configuration: only explicit draft can match.
                c_template, _, c_rows = list(samples())[2]
                assert match_purchase_row(session, c_rows[0].data).status == 'CONFIG_ERROR'
                assert match_purchase_row(session, c_rows[-1].data, config=c_template).status == 'NOT_MATCHED'
                no_filter = c_template.model_copy(deep=True)
                no_filter.rule.filter_conditions.historical_quantity = None
                assert match_purchase_row(session, c_rows[-1].data, config=no_filter).status == 'MATCHED'
                report['c_499_without_quantity_filter'] = 'MATCHED'
                assert session.scalar(select(MatchRule).where(MatchRule.customer_id == 'C')) is None
    finally:
        engine.dispose()
    before_path = REPORT / ('a_complete/before_fingerprints.json' if stage == 'a-complete' else 'before_fingerprints.json')
    before = json.loads(before_path.read_text(encoding='utf-8'))
    after = fingerprints()
    report['other_databases_unchanged'] = {name: before[name] == after[name] for name in ['order_automation', 'order_test']}
    assert all(report['other_databases_unchanged'].values())
    original = json.loads((REPORT / 'original_excel_audit.json').read_text(encoding='utf-8'))
    assert all(hashlib.sha256(__import__('pathlib').Path(item['file']).read_bytes()).hexdigest() == item['sha256'] for item in original)
    first = REPORT / 'first.json'
    if stage == 'final' and first.exists():
        first_report = json.loads(first.read_text(encoding='utf-8'))
        report['repeat_business_equal'] = first_report['stable_business'] == json.loads(json.dumps(stable, default=str))
        assert report['repeat_business_equal']
    (REPORT / (stage + '.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    lines = ['# ABC 原始 Excel 逐行验收表', '', '由实际 order_demo、原始 Excel 和现有匹配引擎只读计算。所有 DEMO / SIM 数据均为本地模拟历史，非真实 OMS。', '',
             '|客户/场景|原文件|Excel 行|单号 / 机型 / 料号 / 批次|匹配规则（含一致性约束）|状态|历史单号 / ID|产品 / 单价|说明|',
             '|---|---|---:|---|---|---|---|---|---|']
    for r in output:
        candidates = r['candidates']
        references = '<br>'.join(f"{c['order_no']} / {c['id']}" for c in candidates) or '—'
        products = '<br>'.join(f"{c['product_code']} / {c['unit_price']}（历史数量 {c['quantity']}）" for c in candidates) or '—'
        reason = r['reason']
        if r['customer'] == 'C' and r['status'] == 'NOT_MATCHED':
            reason = '对应模拟历史数量499，不满足>500；去掉数量限制可匹配。当前采购数量为' + r['purchase_quantity']
        fields = list(dict.fromkeys(r['match_fields'] + r['consistency_fields']))
        lines.append('|'+ '|'.join([r['scenario'], r['file'], str(r['excel_row']), ' / '.join(v or '—' for v in r['values'].values()),
            ' + '.join(fields), r['status'], references, products, reason]) + '|')
    (REPORT / '逐行验收表.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'stage': stage, 'seed_counts': report['seed_counts'], 'other_databases_unchanged': report['other_databases_unchanged'],
                      'repeat_business_equal': report.get('repeat_business_equal'), 'rows': len(output)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', choices=['first', 'final', 'a-complete'], required=True)
    audit(parser.parse_args().stage)
