import json
import os
import re
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
import pytest
from openpyxl import Workbook, load_workbook
from sqlalchemy import select, event
from sqlalchemy.orm import Session
from app.core.database import get_engine
from app.models import CustomerConfig, MatchRule
from app.services.customer_configuration import read_configuration_file, ROOT
from app.services.excel_parser import parse_excel, parse_purchase_file, ParseLimits

CONFIGS={c.customer.customer_id:c for c in read_configuration_file()}

def workbook_bytes(headers=None, rows=None, *, sheet="Sheet1", header_row=1, auxiliary=False):
    headers = headers if headers is not None else ["采购订单号","物料/品项编码","订单数量"]
    rows = rows if rows is not None else [["00123","MAT",12]]
    book=Workbook(); ws=book.active;ws.title=sheet
    for _ in range(header_row-1):ws.append(["标题"])
    ws.append(headers)
    for row in rows:ws.append(row)
    if auxiliary:book.create_sheet("字段映射说明").append(["不是采购数据"])
    stream=BytesIO();book.save(stream);book.close();return stream.getvalue()

def parse(content,config=None,**options):
    return parse_excel(config or CONFIGS["A"],content,file_name="test.xlsx",**options)

def rewrite_xml(content, transform):
    output=BytesIO()
    with ZipFile(BytesIO(content)) as source, ZipFile(output,"w") as target:
        for item in source.infolist():
            value=source.read(item.filename)
            if item.filename=="xl/worksheets/sheet1.xml":value=transform(value.decode()).encode()
            target.writestr(item,value)
    return output.getvalue()

@pytest.mark.parametrize("cid,count",[("A",9),("B",5),("C",8)])
def test_real_excel_rows_and_provenance(cid,count):
    path=next((ROOT/"sample_files").glob(cid+"*.xlsx"))
    result=parse_excel(CONFIGS[cid],path)
    assert result.file_error is None and len(result.rows)==count and result.success_rows==count
    assert result.invalid_rows==0
    expectations=json.loads((ROOT/"config/historical_seed_v1.json").read_text(encoding="utf-8"))["purchase_expectations"]
    for row in result.rows:
        expected=next(r for r in expectations if r["customer_id"]==cid and r["row_number"]==row.row_number)
        assert row.sheet_name=="Sheet1"
        for field,value in expected["values"].items():
            actual=getattr(row.data,field)
            assert actual==(Decimal(value) if field=="quantity" else value)
        assert isinstance(row.data.quantity,Decimal)
        assert row.data.model_dump(mode="json")["raw_data"]==row.raw_data
    if cid=="B":
        assert result.rows[0].data.customer_model==result.rows[0].raw_data["机型"]
        assert result.rows[0].data.customer_model!=result.rows[0].raw_data["客户/型号名称"]
    if cid=="C":
        assert isinstance(result.rows[0].raw_data["料号"],int)
        assert result.rows[1].data.quantity==12
        assert result.rows[0].data.customer_order_no is None
    if cid=="A":
        assert len(result.rows[0].raw_data)>3
        assert "币种" in result.rows[0].raw_data

@pytest.mark.parametrize("content,name",[(b"","test.xlsx"),(b"not a workbook","test.xlsx"),(b"not a workbook","test.xls"),(b"PKbroken","test.xlsx")])
def test_invalid_file(content,name):
    result=parse_excel(CONFIGS["A"],content,file_name=name)
    assert result.file_error.code=="INVALID_FILE" and result.rows==[]

def test_wrong_extension_missing_path_and_missing_bytes_filename(tmp_path):
    assert parse_excel(CONFIGS["A"],workbook_bytes()).file_error.code=="INVALID_FILE"
    assert parse_excel(CONFIGS["A"],tmp_path/"missing.xlsx").file_error.code=="INVALID_FILE"
    assert parse_excel(CONFIGS["A"],workbook_bytes(),file_name="test.xlsm").file_error.code=="INVALID_FILE"
    assert parse_excel(CONFIGS["A"],workbook_bytes(),file_name="TEST.XLSX").file_error is None

@pytest.mark.parametrize("limits",[ParseLimits(max_size_bytes=10),ParseLimits(max_uncompressed_bytes=10),ParseLimits(max_rows=1),ParseLimits(max_columns=2)])
def test_resource_limits(limits):
    result=parse(workbook_bytes(rows=[["a","m",1],["b","m",2]]),limits=limits)
    assert result.file_error.code=="INVALID_FILE"

def test_missing_required_column_and_duplicate_headers():
    missing=parse(workbook_bytes(headers=["采购订单号","订单数量"],rows=[["a",1]]))
    assert missing.file_error.code=="MISSING_COLUMN" and missing.file_error.fields==["物料/品项编码"]
    duplicate=parse(workbook_bytes(headers=["采购订单号","物料/品项编码","订单数量","订单数量"],rows=[["a","m",1,2]]))
    assert duplicate.file_error.code=="INVALID_FILE" and "订单数量" in duplicate.file_error.fields

def test_wrong_sheet_empty_data_and_auxiliary_sheet():
    assert parse(workbook_bytes(sheet="Other")).file_error.code=="INVALID_FILE"
    assert parse(workbook_bytes(rows=[])).file_error.code=="INVALID_FILE"
    assert parse(workbook_bytes(rows=[[None,None,None]])).file_error.code=="INVALID_FILE"
    assert len(parse(workbook_bytes(auxiliary=True)).rows)==1
    config=CONFIGS["A"].model_copy(deep=True);config.customer.sheet_name="字段映射说明"
    assert parse(workbook_bytes(auxiliary=True),config).file_error.code=="CONFIG_ERROR"

def test_blank_rows_zero_codes_bad_rows_do_not_lose_valid_rows():
    result=parse(workbook_bytes(rows=[["00123","m",1],[None,None,None],[" "," "," "],["bad","m",0],["bad","m",-2],["bad","m","oops"],[None,"m",1],[0,"m",2],["good","m",3]]))
    assert result.file_error is None
    assert [r.row_number for r in result.rows]==[2,5,6,7,8,9,10]
    assert result.success_rows==3 and result.invalid_rows==4
    assert result.rows[-2].data.customer_order_no=="0"
    assert result.rows[-1].status=="SUCCESS"
    assert any("customer_order_no" in e.fields for e in result.rows[4].errors)

def test_dynamic_mapping_and_header_row():
    config=CONFIGS["A"].model_copy(deep=True)
    config.customer.sheet_name="采购"
    config.customer.header_row=3
    config.customer.field_mapping={"新单号":"customer_order_no","新料号":"customer_material_code","新数量":"quantity"}
    result=parse(workbook_bytes(headers=["新单号","新料号","新数量"],sheet="采购",header_row=3),config)
    assert result.file_error is None and result.rows[0].row_number==4
    assert result.rows[0].data.customer_order_no=="00123"

def test_mapping_conflicts_and_unknown_fields_rejected():
    for mapping in [{"采购订单号":"unknown"},{"采购订单号":"customer_order_no","另一列":"customer_order_no"}]:
        config=CONFIGS["A"].model_copy(deep=True);config.customer.field_mapping=mapping
        assert parse(workbook_bytes(),config).file_error.code=="CONFIG_ERROR"

def test_optional_mapped_column_can_be_missing():
    result=parse(workbook_bytes(headers=["单号","机型","数量"],rows=[["test","model",12]]),CONFIGS["B"])
    assert result.file_error is None and result.rows[0].data.customer_material_code is None

def test_unmapped_fields_dates_and_unheaded_cells_retained():
    result=parse(workbook_bytes(headers=["采购订单号","物料/品项编码","订单数量","日期","备注"],rows=[["a","m",1,datetime(2025,1,2)," keep spaces "]]))
    raw=result.rows[0].raw_data
    assert raw["日期"]=="2025-01-02T00:00:00" and raw["备注"]==" keep spaces "
    assert result.rows[0].raw_cells["日期"]["original_type"]=="datetime"
    bad=parse(workbook_bytes(headers=["采购订单号","物料/品项编码","订单数量",None],rows=[["a","m",1,"unheaded"]]))
    assert bad.rows[0].status=="INVALID_INPUT" and bad.rows[0].raw_data["__column_D__"]=="unheaded"

def test_formula_policy_and_cached_values():
    content=workbook_bytes(rows=[["a","m","=1+1"],["b","m",3]])
    result=parse(content)
    assert result.rows[0].status=="INVALID_INPUT" and result.rows[1].status=="SUCCESS"
    assert result.rows[0].raw_data["订单数量"]=="=1+1"
    assert "可信缓存" in result.rows[0].errors[0].message
    assert parse(content,trust_formula_cache=True).rows[0].status=="INVALID_INPUT"
    cached=rewrite_xml(content,lambda xml:re.sub(r"<f>1\+1</f><v(?:\s*/>|></v>)", "<f>1+1</f><v>2</v>", xml))
    assert parse(cached).rows[0].status=="INVALID_INPUT"
    trusted=parse(cached,trust_formula_cache=True)
    assert trusted.rows[0].data.quantity==Decimal("2")
    assert trusted.rows[0].raw_data["订单数量"]=="=1+1"
    assert trusted.rows[0].raw_cells["订单数量"]["cached_value"]==2

def test_excel_error_cell_and_nonmatching_formula_are_traceable():
    result=parse(workbook_bytes(headers=["采购订单号","物料/品项编码","订单数量","备注"],rows=[["a","m","#VALUE!","=1+1"],["b","m",1,"=1+1"]]))
    assert result.rows[0].status=="INVALID_INPUT" and result.rows[1].status=="SUCCESS"
    assert result.rows[1].raw_data["备注"]=="=1+1"

def test_numeric_format_and_precision_preservation():
    book=load_workbook(BytesIO(workbook_bytes(rows=[["000123","m",1],[123,"m",2],[1234567890123456,"m",3]])))
    book.active['A3'].number_format='000000'
    stream=BytesIO();book.save(stream);book.close()
    result=parse(stream.getvalue())
    assert result.rows[0].data.customer_order_no=="000123"
    assert result.rows[1].status=="INVALID_INPUT" and result.rows[1].raw_data["采购订单号"]==123
    assert result.rows[1].raw_cells["采购订单号"]["number_format"]=='000000'
    assert result.rows[2].status=="INVALID_INPUT"

def test_small_incorrect_dimensions_do_not_drop_rows_and_bounds_checked():
    content=workbook_bytes(rows=[["a","m",1],["b","m",2]])
    modified=rewrite_xml(content,lambda xml:re.sub(r'<dimension ref="[^"]+"\s*/>','<dimension ref="A1:A1"/>',xml))
    with ZipFile(BytesIO(modified)) as z:assert b'A1:A1' in z.read('xl/worksheets/sheet1.xml')
    assert len(parse(modified).rows)==2
    bad=rewrite_xml(modified,lambda xml:xml.replace('r="A3"','r="AAA3"'))
    assert parse(bad).file_error.code=="INVALID_FILE"

@pytest.fixture
def db_session():
    if os.getenv("RUN_DB_TESTS")!='1':pytest.skip("Requires local MySQL RUN_DB_TESTS=1")
    with get_engine().connect() as conn:
        outer=conn.begin()
        try:
            with Session(bind=conn,join_transaction_mode="create_savepoint") as session:yield session
        finally:outer.rollback()

@pytest.mark.parametrize("cid,count",[("A",9),("B",5),("C",8)])
def test_database_config_drives_real_excel_read_only(db_session,cid,count):
    connection=db_session.connection();statements=[]
    def record(conn,cursor,statement,parameters,context,executemany):statements.append(statement.lower())
    event.listen(connection,"before_cursor_execute",record)
    try:result=parse_purchase_file(db_session,cid,next((ROOT/"sample_files").glob(cid+"*.xlsx")))
    finally:event.remove(connection,"before_cursor_execute",record)
    assert result.file_error is None and result.success_rows==count
    assert statements and all(statement.lstrip().startswith('select') for statement in statements)
    assert not any('sales_order' in statement or 'import_task' in statement for statement in statements)

def test_database_missing_and_corrupt_config(db_session):
    assert parse_purchase_file(db_session,"UNKNOWN",workbook_bytes(),file_name="test.xlsx").file_error.code=='CONFIG_ERROR'
    rule=db_session.scalar(select(MatchRule).where(MatchRule.customer_id=='A',MatchRule.enabled.is_(True)))
    rule.match_fields=['unknown'];db_session.flush()
    assert parse_purchase_file(db_session,"A",workbook_bytes(),file_name="test.xlsx").file_error.code=='CONFIG_ERROR'

def test_database_changed_mapping_without_customer_branch(db_session):
    customer=db_session.scalar(select(CustomerConfig).where(CustomerConfig.customer_id=='A'))
    customer.field_mapping={"单号新":"customer_order_no","料号新":"customer_material_code","数量新":"quantity"}
    customer.config_version=2;db_session.flush()
    result=parse_purchase_file(db_session,'A',workbook_bytes(headers=['单号新','料号新','数量新']),file_name='test.xlsx')
    assert result.file_error is None and result.rows[0].data.customer_order_no=='00123'
    assert result.config_version==2

@pytest.mark.parametrize("target",["customer","rule"])
def test_database_disabled_configuration(db_session,target):
    model=CustomerConfig if target=="customer" else MatchRule
    query=select(model).where(model.customer_id=='A')
    if model is MatchRule:query=query.where(model.enabled.is_(True))
    row=db_session.scalar(query);row.enabled=False;db_session.flush()
    result=parse_purchase_file(db_session,'A',workbook_bytes(),file_name='test.xlsx')
    assert result.file_error.code=='CONFIG_ERROR' and not result.rows

def test_configuration_db_failure_is_structured_and_sanitized(monkeypatch):
    from contextlib import nullcontext
    from sqlalchemy.exc import OperationalError
    import app.services.excel_parser as parser
    class FakeSession:
        no_autoflush=nullcontext()
    def fail(*args):raise OperationalError('private SQL',{},Exception('private password'))
    monkeypatch.setattr(parser,'load_configuration',fail)
    result=parser.parse_purchase_file(FakeSession(),'A',workbook_bytes(),file_name='test.xlsx')
    assert result.file_error.code=='CONFIG_ERROR'
    assert 'private' not in result.model_dump_json()

def test_malformed_xml_workbook_is_structured():
    invalid=rewrite_xml(workbook_bytes(),lambda xml:xml[:-100])
    result=parse(invalid)
    assert result.file_error.code=='INVALID_FILE' and result.rows==[]

def test_header_mapping_is_exact_and_numeric_header_is_rejected():
    assert parse(workbook_bytes(headers=[' 采购订单号 ','物料/品项编码','订单数量'])).file_error.code=='MISSING_COLUMN'
    assert parse(workbook_bytes(headers=[123,'物料/品项编码','订单数量'])).file_error.code=='INVALID_FILE'

def test_read_configuration_does_not_autoflush_pending_orders(db_session):
    from app.models import SalesOrder
    db_session.add(SalesOrder(order_no='UNSAVED-M04'))
    # This deliberately incomplete pending object would fail if parser autoflushed.
    result=parse_purchase_file(db_session,'A',workbook_bytes(),file_name='test.xlsx')
    assert result.file_error is None and result.success_rows==1
    assert any(isinstance(row,SalesOrder) and row.id is None for row in db_session.new)


# This module owns a fresh legacy baseline; no managed database is queried.
pytestmark = pytest.mark.usefixtures("legacy_runtime")
