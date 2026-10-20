"""Read-only final receipts; original business contents are never exported."""
import hashlib,json
from pathlib import Path
import httpx
from sqlalchemy import text,select
from sqlalchemy.orm import Session
from scripts.environment_admin import admin_engine,assert_target
from scripts.demo_dataset import verify
from scripts.verify_environments import fingerprint

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/reports/v2_0_environments'

def main():
    before=json.loads((OUT/'original_before.json').read_text(encoding='utf-8'))
    engine=admin_engine('order_automation')
    after={'version':None,'tables':{}}
    with engine.connect() as conn:
        conn.exec_driver_sql('SET TRANSACTION READ ONLY')
        after['version']=conn.scalar(text('SELECT version_num FROM alembic_version'))
        for name,info in before['tables'].items():
            rows=[dict(r) for r in conn.execute(text(f'SELECT * FROM `{name}` ORDER BY id')).mappings()]
            digest=hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()
            after['tables'][name]={'count':len(rows),'sha256':digest,'unchanged':len(rows)==info['count'] and digest==info['sha256']}
        after['seed_211_present']=conn.scalar(text('SELECT count(*) FROM sales_order WHERE id=211 AND source_type="SEED"'))==1
        after['xiaomi_preserved']=conn.scalar(text('SELECT count(*) FROM customer_config WHERE customer_id="xiaomi"'))==1
    engine.dispose()
    after['all_original_records_unchanged']=all(t['unchanged'] for t in after['tables'].values()) and after['version']==before['version']
    (OUT/'original_after.json').write_text(json.dumps(after,indent=2),encoding='utf-8')
    assert after['all_original_records_unchanged'],'Original hashes differ; investigate without writing anything'
    results={'original_unchanged':True,'environments':{},'sample_files_unchanged':True}
    for profile,front,back in [('demo',5180,8010),('test',5174,8001)]:
        engine=admin_engine('order_'+profile)
        with engine.connect() as conn:
            assert_target(conn,profile)
            tables=fingerprint(conn)
            version=conn.scalar(text('SELECT version_num FROM alembic_version'))
        with Session(engine) as session: rows=verify(session)
        health=httpx.get(f'http://127.0.0.1:{front}/health',trust_env=False).json()
        assert health['environment']==profile and health['database_name']=='order_'+profile
        results['environments'][profile]={'version':version,'tables':tables,'matched':sum(r['status']=='MATCHED' for r in rows),'not_matched':sum(r['status']!='MATCHED' for r in rows),'frontend_port':front,'backend_port':back,'health':health}
        engine.dispose()
    manifest=json.loads((OUT/'demo_initial_manifest.json').read_text(encoding='utf-8'))
    for sample in manifest['samples']:
        assert hashlib.sha256((ROOT/sample['file']).read_bytes()).hexdigest()==sample['sha256']
    (OUT/'final_verification.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'original_unchanged':True,'demo_matches':results['environments']['demo']['matched'],'test_matches':results['environments']['test']['matched'],'samples_unchanged':True}))

if __name__=='__main__':main()
