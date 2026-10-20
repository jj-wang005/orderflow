"""Bounded HTTP persistence smoke test; cleans ONLY its own unmodified records.
Never writes history DB. Does not send any data to MiMo.
"""
from pathlib import Path
from uuid import uuid4
import json,hashlib
import httpx
from sqlalchemy import text,select,func
from app.models import Base,CustomerConfig,ImportTask,ImportTaskRow,SalesOrder,AIExtraction,OrderChange,ManualCorrection
from scripts.environment_admin import admin_engine,assert_target

ROOT=Path(__file__).resolve().parents[1]

def fingerprint(conn):
    result={}
    for name,table in Base.metadata.tables.items():
        rows=[dict(r) for r in conn.execute(select(table).order_by(table.c.id)).mappings()]
        result[name]={'count':len(rows),'sha256':hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()}
    return result

def main():
    engines={p:admin_engine('order_'+p) for p in ('demo','test')};before={};receipts=[]
    for p,e in engines.items():
        with e.connect() as conn:assert_target(conn,p);before[p]=fingerprint(conn)
    try:
        for profile,port in [('demo',8010),('test',8001)]:
            other='test' if profile=='demo' else 'demo';token=uuid4().hex;name='ENV-VERIFY-'+token+'.xlsx'
            blob=(ROOT/'sample_files/A客户采购订单模板.xlsx').read_bytes()
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=60,trust_env=False) as client:
                health=client.get('/health').json()
                assert health['environment']==profile and health['database_name']=='order_'+profile
                response=client.post('/api/v1/imports',data={'customer_id':'A'},files={'file':(name,blob)},headers={'Idempotency-Key':token})
                assert response.status_code==201,response.status_code
                task=response.json()['data'];tid=task['task_id']
                assert task['success_rows']>0
            # A new HTTP connection sees committed rows (not a TestClient transaction).
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False) as client:
                saved=client.get(f'/api/v1/imports/{tid}').json()['data'];assert saved['success_rows']==task['success_rows']
                replay=client.post('/api/v1/imports',data={'customer_id':'A'},files={'file':(name,blob)},headers={'Idempotency-Key':token}).json()['data']
                assert replay['task_id']==tid and replay['idempotency_replayed']
                c=client.post('/api/v1/imports',data={'customer_id':'A'},files={'file':(name,blob)},headers={'Idempotency-Key':uuid4().hex})
                assert c.status_code==409  # No unconfirmed duplicate task.
            with engines[other].connect() as conn:assert fingerprint(conn)==before[other]
            # Own task only. Lock in the same order as application writers.
            with engines[profile].begin() as conn:
                assert_target(conn,profile)
                conn.execute(select(CustomerConfig.id).where(CustomerConfig.customer_id=='A').with_for_update()).all()
                stored=conn.execute(select(ImportTask.__table__).where(ImportTask.id==tid).with_for_update()).mappings().one()
                assert stored['file_name']==name and stored['status']==task['status']
                own_rows=conn.execute(select(ImportTaskRow.__table__).where(ImportTaskRow.task_id==tid).with_for_update()).mappings().all()
                owned=conn.execute(select(SalesOrder.__table__).where(SalesOrder.import_task_id==tid).with_for_update()).mappings().all();ids=[o['id'] for o in owned]
                assert all(o['status']=='CREATED' and o['edit_revision']==0 for o in owned)
                assert conn.scalar(select(func.count()).select_from(AIExtraction).where(AIExtraction.import_task_id==tid))==0
                assert conn.scalar(select(func.count()).select_from(OrderChange).where(OrderChange.order_id.in_(ids)))==0
                assert conn.scalar(select(func.count()).select_from(ManualCorrection).where(ManualCorrection.row_id.in_([r['id'] for r in own_rows])))==0
                assert conn.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.source_order_id.in_(ids)))==0
                assert conn.scalar(select(func.count()).select_from(ImportTaskRow).where(ImportTaskRow.task_id!=tid,(ImportTaskRow.created_order_id.in_(ids)|ImportTaskRow.matched_order_id.in_(ids)|ImportTaskRow.duplicate_order_id.in_(ids))))==0
                conn.execute(ImportTaskRow.__table__.delete().where(ImportTaskRow.task_id==tid))
                conn.execute(SalesOrder.__table__.delete().where(SalesOrder.import_task_id==tid))
                conn.execute(ImportTask.__table__.delete().where(ImportTask.id==tid))
            with engines[profile].connect() as conn:assert fingerprint(conn)==before[profile]
            receipts.append({'profile':profile,'backend_port':port,'task_id':tid,'persisted_success_rows':saved['success_rows'],'review_rows':saved['review_rows'],'unmatched_rows':saved['unmatched_rows'],'idempotent_replay':True,'duplicate_request_rejected':True,'other_database_unchanged':True,'own_test_records_cleaned':True})
        out=ROOT/'docs/reports/v2_0_environments/http_persistence_verification.json'
        out.write_text(json.dumps(receipts,indent=2),encoding='utf-8');print(json.dumps(receipts))
    finally:
        for engine in engines.values():engine.dispose()

if __name__=='__main__':main()
