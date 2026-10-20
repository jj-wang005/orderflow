"""Deterministic synthetic history, derived from unchanged ABC sample fields."""
from datetime import datetime,timedelta
from decimal import Decimal
from pathlib import Path
import hashlib,json
from sqlalchemy import select,func
from app.models import CustomerConfig,SalesOrder,ConfigurationVersion,MatchRule
from app.repositories.customer_configuration import initialize_configurations,load_configuration
from app.services.customer_configuration import read_configuration_file
from app.services.excel_parser import parse_excel
from app.services.matching_engine import match_purchase_row

ROOT=Path(__file__).resolve().parents[1]
VERSION='demo-abc-defense-v3-a-complete'

def samples():
    for config in read_configuration_file():
        path=ROOT/'sample_files'/(config.customer.customer_id+'客户采购订单模板.xlsx')
        parsed=parse_excel(config,path)
        if parsed.file_error or any(r.data is None for r in parsed.rows):raise RuntimeError('原始样例无法完整解析，禁止初始化')
        yield config,path,parsed.rows

def seed(session,profile):
    from sqlalchemy import text
    import re
    name=session.scalar(text('SELECT DATABASE()'))
    if profile not in {'demo','test'} or (name!={'demo':'order_demo','test':'order_test'}[profile] and not re.fullmatch(r'order_automation_envcheck_[a-f0-9]{32}',name)):raise RuntimeError('禁止向历史库、其他环境或未知库初始化')
    if session.scalar(select(func.count()).select_from(CustomerConfig)):raise RuntimeError('客户表非空，禁止初始化覆盖')
    manifest=[]
    for config,path,rows in samples():
        cid=config.customer.customer_id
        if profile=='demo' and cid=='C':
            # Identity is required by historical-order FKs; first configuration
            # uses the existing version-zero workflow, without a rule/receipt.
            session.add(CustomerConfig(customer_id=cid,customer_name=config.customer.customer_name,
                sheet_name=config.customer.sheet_name,header_row=config.customer.header_row,
                field_mapping={},enabled=False,config_version=0))
            session.flush()
        else:
            initialize_configurations(session,[config])
            session.add(ConfigurationVersion(customer_id=cid,config_version=1,rule_version=1,snapshot=config.model_dump(mode='json')))
        for i,row in enumerate(rows):
            expected=i<len(rows)-1 or (profile=='demo' and cid=='A')
            values={f:getattr(row.data,f) for f in ('customer_order_no','customer_material_code','customer_model','customer_batch')}
            entry={'customer':cid,'excel_row':row.row_number,**values,'purchase_quantity':str(row.data.quantity),'expected_match':expected,
                   'reason':'合格历史参考' if expected else '历史数量 499，不满足 >500' if cid=='C' else '无此完整匹配键的历史订单'}
            entry['expected_status']='MATCHED' if expected else 'NOT_MATCHED'
            if profile=='demo' and cid=='B' and i==0:
                entry.update(expected_match=False,expected_status='AMBIGUOUS_MATCH',
                    reason='双字段有不同产品；未保存草稿增加客户料号后唯一匹配',three_field_status='MATCHED')
            manifest.append(entry)
            if not expected and cid!='C':continue
            historical_quantity=Decimal(800) if expected else Decimal(499)
            code=f'SIM-DEMO-{cid}-{i+1:02}'
            price=Decimal('10.00')+i
            date=datetime(2025,5,1)+timedelta(days=i)
            def add(suffix,at,unit):
                session.add(SalesOrder(order_no=f'DEMO-V1-{cid}-{i+1:02}{suffix}',customer_id=cid,**values,
                    product_code=code,product_name=('模拟演示产品 24MHz SMD3225 晶振 '+code if cid=='A' and i==8 else '模拟演示产品 '+code),unit_price=unit,quantity=historical_quantity,
                    amount=(unit*historical_quantity).quantize(Decimal('.01')),status='VALID',source_type='SEED',created_at=at,updated_at=at))
            if i==0 and cid=='A':
                add('-OLDER',date-timedelta(days=1),Decimal('8'));add('-TIE',date,Decimal('9'));session.flush()
            add('',date,price)
        session.flush()
        if profile=='demo' and cid=='B':
            # Synthetic history: a second product on the first purchase's PO
            # and true model. Its material/product pair comes from B row 3.
            # Only the simulated history combines these fields; Excel is untouched.
            first,other=rows[0].data,rows[1].data
            session.add(SalesOrder(order_no='DEMO-V2-B-01-ALT',customer_id=cid,
                customer_order_no=first.customer_order_no,customer_model=first.customer_model,
                customer_material_code=other.customer_material_code,customer_batch=None,
                product_code='SIM-DEMO-B-02',product_name='模拟演示产品 SIM-DEMO-B-02',
                unit_price=Decimal('11'),quantity=Decimal('800'),amount=Decimal('8800'),
                status='VALID',source_type='SEED',created_at=datetime(2025,5,1),updated_at=datetime(2025,5,1)))
            session.flush()
    if profile=='test':
        # Independent, non-sample procurement keys; cannot contaminate the 22 demo rows.
        for number,(cid,po,batch,qty,status,price,product) in enumerate([
            ('C',None,'TEST-Q499',499,'VALID','2','SIM-TEST-Q499'),('C',None,'TEST-Q500',500,'VALID','2','SIM-TEST-Q500'),('C',None,'TEST-Q501',501,'VALID','2','SIM-TEST-Q501'),
            ('B','TEST-CREATED',None,800,'CREATED','2','SIM-TEST-CREATED'),('B','TEST-INVALID',None,800,'INVALID','2','SIM-TEST-INVALID'),
            ('B','TEST-ZERO',None,800,'VALID','0','SIM-TEST-ZERO'),('B','TEST-CONFLICT',None,800,'VALID','2','SIM-TEST-P1'),('B','TEST-CONFLICT',None,800,'VALID','2','SIM-TEST-P2'),
            ('B','TEST-LATEST',None,800,'VALID','2','SIM-TEST-LATEST'),('B','TEST-LATEST',None,800,'VALID','3','SIM-TEST-LATEST')],1):
            price=Decimal(price);at=datetime(2025,6,1)+timedelta(days=number)
            session.add(SalesOrder(order_no=f'TEST-SCENARIO-{number:02}',customer_id=cid,customer_order_no=po,customer_batch=batch,customer_material_code='TEST-MAT',customer_model='TEST-MODEL',product_code=product,product_name='模拟边界产品 '+product,unit_price=price,quantity=Decimal(qty),amount=price*qty,status=status,source_type='SEED',created_at=at,updated_at=at))
    session.flush()
    return {'dataset_version':VERSION if profile=='demo' else 'demo-19-of-22-v1','orders':session.scalar(select(func.count()).select_from(SalesOrder)),
            'samples':[{'customer':c.customer.customer_id,'file':str(p.relative_to(ROOT)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for c,p,r in samples()], 'expectations':manifest}

def verify(session):
    output=[]
    c=session.scalar(select(CustomerConfig).where(CustomerConfig.customer_id=='C'))
    draft_c=c is not None and c.config_version==0
    if draft_c:
        if c.enabled or c.field_mapping or session.scalar(select(func.count()).select_from(MatchRule).where(MatchRule.customer_id=='C')) or session.scalar(select(func.count()).select_from(ConfigurationVersion).where(ConfigurationVersion.customer_id=='C')):
            raise RuntimeError('C 首次配置状态不完整')
    for config,path,rows in samples():
        cid=config.customer.customer_id
        actual=config if cid=='C' and draft_c else load_configuration(session,cid)
        for index,row in enumerate(rows):
            result=match_purchase_row(session,row.data,config=actual)
            expected=index<len(rows)-1 or (draft_c and cid=='A')
            expected_status='MATCHED' if expected else 'NOT_MATCHED'
            if draft_c and cid=='B' and index==0:expected_status='AMBIGUOUS_MATCH'
            if result.status!=expected_status:raise RuntimeError(f'{cid} 第 {row.row_number} 行不符合演示预期: {result.status}')
            output.append({'customer':config.customer.customer_id,'row':row.row_number,'expected':expected_status=='MATCHED','status':result.status,
                'reference_id':result.matched_order.id if result.matched_order else None,'unit_price':str(result.matched_order.unit_price) if result.matched_order else None})
        if draft_c and cid=='B':
            three=actual.model_copy(deep=True)
            three.rule.match_fields.append('customer_material_code')
            for index,row in enumerate(rows):
                result=match_purchase_row(session,row.data,config=three)
                if result.status!=('MATCHED' if index<len(rows)-1 else 'NOT_MATCHED'):
                    raise RuntimeError('B 三字段预期不符')
    return output
