"""Read-only row-hash receipts for the A multi-material correction. No DB writes."""
import json,hashlib,sys
from datetime import datetime
from pathlib import Path
from sqlalchemy import text,inspect
from scripts.environment_admin import admin_engine,DELETE_ORDER

OUT=Path(__file__).resolve().parents[1]/'docs/reports/a_multi_material_fix'

def sha(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()

def snapshot():
    result={'checked_at':datetime.now().isoformat(),'databases':{}}
    for name in ('order_automation','order_demo','order_test'):
        engine=admin_engine(name)
        with engine.connect() as conn:
            conn.exec_driver_sql('SET TRANSACTION READ ONLY')
            tables={}
            for table in DELETE_ORDER:
                rows=[dict(r) for r in conn.execute(text(f'SELECT * FROM `{table}` ORDER BY id')).mappings()]
                tables[table]={'count':len(rows),'sha256':sha(rows),'row_hashes':{str(row['id']):sha(row) for row in rows}}
            result['databases'][name]={'version':conn.scalar(text('SELECT version_num FROM alembic_version')),'tables':tables,
                'sales_order_unique_constraints':inspect(conn).get_unique_constraints('sales_order')}
        engine.dispose()
    return result

def main():
    if len(sys.argv)!=2 or sys.argv[1] not in {'before','after'}:raise SystemExit('Use before/after')
    kind=sys.argv[1];path=OUT/(kind+'.json')
    if path.exists():raise SystemExit('Receipt already exists; not overwritten')
    result=snapshot()
    if kind=='after':
        before=json.loads((OUT/'before.json').read_text(encoding='utf-8'))
        for name,db in before['databases'].items():
            now=result['databases'][name]
            assert db['version']==now['version'] and db['sales_order_unique_constraints']==now['sales_order_unique_constraints']
            for table,info in db['tables'].items():
                assert all(now['tables'][table]['row_hashes'].get(i)==h for i,h in info['row_hashes'].items()),f'Existing records changed: {name}/{table}'
        result['all_baseline_records_and_unique_constraints_unchanged']=True
    OUT.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(kind,'receipt saved; no DB writes')

if __name__=='__main__':main()
