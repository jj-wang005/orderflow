"""Local verification server. ALL writes are under one order_test outer rollback.
Only run explicitly with APP_ENV=test RUN_DB_TESTS=1; never a daily startup entry.
"""
import os,json,threading
from pathlib import Path
from uuid import uuid4
from decimal import Decimal
import uvicorn
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.database import get_engine,get_db
from app.main import app
from app.models import SalesOrder,ConfigurationVersion
from app.repositories.customer_configuration import initialize_configurations
from app.services.customer_configuration import read_configuration_file
from app.services.http_import import submit_import
from app.services.excel_parser import ParseLimits
from app.services import ai_drafts
from app.schemas.ai_extraction import ExtractionResult

def main():
    if os.environ.get('APP_ENV')!='test' or os.environ.get('RUN_DB_TESTS')!='1' or get_settings().db_name!='order_test':raise RuntimeError('Explicit isolated test only')
    engine=get_engine();conn=engine.connect();outer=conn.begin();guard=threading.Lock()
    cid='UX92-'+uuid4().hex[:12]
    def db():
        with guard,Session(bind=conn,join_transaction_mode='create_savepoint',expire_on_commit=False) as session:yield session
    app.dependency_overrides[get_db]=db
    try:
        config=read_configuration_file()[0].model_copy(deep=True);config.customer.customer_id=cid;config.customer.customer_name='92/93 浏览器回滚验证'
        with Session(bind=conn,join_transaction_mode='create_savepoint',expire_on_commit=False) as session,session.begin():
            initialize_configurations(session,[config]);session.add(ConfigurationVersion(customer_id=cid,config_version=1,rule_version=1,snapshot=config.model_dump(mode='json')))
            session.add(SalesOrder(order_no='BROWSER-SYNTHETIC-HISTORY',customer_id=cid,customer_order_no='UI-PO',customer_material_code='UI-MAT',product_code='SIM-UI',product_name='浏览器验证模拟产品',unit_price=Decimal('2.5'),quantity=Decimal(800),amount=Decimal(2000),status='VALID',source_type='SEED'));session.flush()
            from io import BytesIO
            from openpyxl import Workbook
            book=Workbook();sheet=book.active;sheet.title=config.customer.sheet_name
            for _ in range(config.customer.header_row-1):sheet.append([])
            sheet.append(list(config.customer.field_mapping));sheet.append([{'customer_order_no':'UI-PO','customer_material_code':'UI-MAT','quantity':12}.get(f) for f in config.customer.field_mapping.values()]);buffer=BytesIO();book.save(buffer)
            task=submit_import(session,cid,buffer.getvalue(),'browser-synthetic.xlsx',uuid4().hex,ParseLimits())
            draft,_=ai_drafts.start(session,cid,'UI-MAT 采购12台（构造测试）'.encode(),'合成文字.txt','TEXT',uuid4().hex,'mock',False)
            extraction=ExtractionResult(rows=[{'customer_order_no':None,'customer_material_code':'UI-MAT','customer_model':None,'customer_batch':None,'quantity':'12','source_id':'text:1','source_excerpt':'UI-MAT 采购12台（构造测试）','extraction_warnings':[],'uncertain_fields':[]}],warnings=[])
            ai_drafts.finish(session,draft.id,0,[{'id':'text:1','location':'构造文字','text':'UI-MAT 采购12台（构造测试）','visual':False}],extraction,{'mock':True})
            from app.models import ImportTaskRow
            from sqlalchemy import select
            row=session.scalar(select(ImportTaskRow).where(ImportTaskRow.task_id==task.task_id))
            receipt={'customer_id':cid,'order_id':row.created_order_id,'task_id':task.task_id,'draft_id':draft.id,'database':'order_test','all_writes_rollback':True}
        Path('.runtime/stage92_browser.json').write_text(json.dumps(receipt),encoding='utf-8')
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8020,log_level='warning'))
        @app.post('/__stage92_validation_stop')
        def stop():server.should_exit=True;return {'stopping':True}
        server.run()
    finally:
        app.dependency_overrides.pop(get_db,None);outer.rollback();conn.close();engine.dispose()
        print('Browser verification outer transaction rolled back',flush=True)

if __name__=='__main__':main()
