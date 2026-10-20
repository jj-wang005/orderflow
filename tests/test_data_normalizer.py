from datetime import date
from decimal import Decimal
import pytest
from app.schemas.purchase_row import RawPurchaseRow
from app.services.customer_configuration import read_configuration_file
from app.services.data_normalizer import normalize_code, normalize_quantity, normalize_row

@pytest.mark.parametrize("value, expected", [(None,None),("",None),("   ",None),(" 00123 ","00123"),(123,"123"),(0,"0"),(123456789012345,"123456789012345"),(" Ab-c ","Ab-c")])
def test_code_normalization(value,expected):
    assert normalize_code(value)==expected

@pytest.mark.parametrize("value", [True,123.5,123.0,1234567890123456,date(2025,1,1),Decimal("123"),float("inf")])
def test_unsafe_code_rejected(value):
    with pytest.raises(ValueError): normalize_code(value)

@pytest.mark.parametrize("format", ["00000","0.00E+00"])
def test_ambiguous_numeric_code_format(format):
    with pytest.raises(ValueError): normalize_code(123,format)

@pytest.mark.parametrize("value,expected", [(12,Decimal("12")),(" 12.0 ",Decimal("12.0")),(100.0,Decimal("100")),(Decimal("1.000"),Decimal("1.000")),("1e2",Decimal("100"))])
def test_quantity_decimal(value,expected):
    result=normalize_quantity(value)
    assert isinstance(result,Decimal) and result==expected

@pytest.mark.parametrize("value", ["12.5",0.1,Decimal("0.001"),None,""," ",0,-1,"bad","NaN","Infinity",True,float("nan"),"1,000",date(2025,1,1)])
def test_invalid_quantity(value):
    with pytest.raises(ValueError): normalize_quantity(value)

@pytest.mark.parametrize("cid", ["A","B","C"])
def test_dynamic_required_fields_and_null_optional_fields(cid):
    config=next(c for c in read_configuration_file() if c.customer.customer_id==cid)
    values={column: ("12" if field=="quantity" else "00123") for column,field in config.customer.field_mapping.items() if field in config.rule.required_fields}
    raw=RawPurchaseRow(sheet_name="Sheet1",row_number=7,raw_data=values,values=values)
    result=normalize_row(config,raw)
    assert result.status=="SUCCESS" and result.data.quantity==12
    for field in ["customer_order_no","customer_material_code","customer_batch","customer_model"]:
        if field not in config.rule.required_fields:
            assert getattr(result.data,field) is None
    for column,field in config.customer.field_mapping.items():
        if field not in config.rule.required_fields:continue
        missing={**values,column:None}
        result=normalize_row(config,raw.model_copy(update={"values":missing,"raw_data":missing}))
        assert result.status=="INVALID_INPUT" and result.data is None
        assert any(field in e.fields for e in result.errors)
        assert result.row_number==7 and result.raw_data[column] is None

@pytest.mark.parametrize("quantity", ["0.0001","1e18"])
def test_quantity_database_precision_rejected_without_rounding(quantity):
    config=read_configuration_file()[0]
    values={"采购订单号":"001","物料/品项编码":"test","订单数量":quantity}
    result=normalize_row(config,RawPurchaseRow(sheet_name="Sheet1",row_number=2,raw_data=values,values=values))
    assert result.status=="INVALID_INPUT" and result.errors[0].fields==["quantity"]
