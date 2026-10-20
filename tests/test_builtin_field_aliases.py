"""Exercise existing recommendation/parser paths; no database access or writes."""
import pytest

from app.services import customer_management as management
from app.services.customer_configuration import read_configuration_file, ROOT
from app.services.excel_parser import parse_excel
from tests.test_excel_parser import workbook_bytes


def recommendations(content):
    return management.suggest_mapping(content, 'sample.xlsx')['columns']


@pytest.mark.parametrize('config', read_configuration_file(), ids=['A', 'B', 'C'])
def test_real_abc_samples_keep_existing_mapping(config):
    path = next((ROOT / 'sample_files').glob(config.customer.customer_id + '*.xlsx'))
    columns = recommendations(path.read_bytes())
    suggested = {c['column']: c['recommended_field'] for c in columns if c['recommended_field']}
    assert suggested == config.customer.field_mapping
    assert all(c['recommended_field'] is None for c in columns if c['column'] == '客户/型号名称')


@pytest.mark.parametrize('field,alias', [
    (field, alias) for field, aliases in management.ALIASES.items() for alias in sorted(aliases | {field})
])
def test_each_builtin_alias_and_standard_field(field, alias):
    value = 12 if field == 'quantity' else 'CODE-01'
    column = recommendations(workbook_bytes([alias], [[value]]))[0]
    assert column['recommended_field'] == field
    # The original header is preserved for manual review and saved mappings.
    assert column['column'] == alias


@pytest.mark.parametrize('header', ['po no.', 'CUSTOMER PART NO.', 'order QUANTITY'])
def test_english_aliases_remain_case_insensitive(header):
    field = {'po no.': 'customer_order_no', 'CUSTOMER PART NO.': 'customer_material_code',
             'order QUANTITY': 'quantity'}[header]
    assert recommendations(workbook_bytes([header], [[12 if field == 'quantity' else 'CODE']]))[0]['recommended_field'] == field


@pytest.mark.parametrize('header', [
    '客户/型号名称', '客户／型号名称', '型号', '型号名称', '产品名称', '我司产品编码',
    '销售订单号', '供应商料号', '库存数量', '已交数量', '剩余数量', '物品识别编号',
])
def test_ambiguous_or_unrecognized_headers_need_manual_selection(header):
    column = recommendations(workbook_bytes([header], [['MODEL-01']]))[0]
    assert column['recommended_field'] is None
    assert column['mappable']  # Still available for explicit manual mapping.


@pytest.mark.parametrize('headers,values', [
    (['采购订单号', 'PO No.'], ['PO1', 'PO2']),
    (['物料编码', 'Customer Part No.'], ['MAT1', 'MAT2']),
    (['客户机型', '客户型号'], ['M1', 'M2']),
    (['数量', 'Order Qty'], [10, 20]),
])
def test_duplicate_target_candidates_are_all_left_for_review(headers, values):
    assert all(c['recommended_field'] is None for c in recommendations(workbook_bytes(headers, [values])))


@pytest.mark.parametrize('value', [0, -1, 1.5, 'NaN', 'Infinity', '=1+1', True, 'twelve'])
def test_new_quantity_alias_preserves_type_validation(value):
    assert recommendations(workbook_bytes(['Order Quantity'], [[value]]))[0]['recommended_field'] is None


def test_alias_collision_never_selects_first_field(monkeypatch):
    monkeypatch.setitem(management.ALIASES, 'customer_model', management.ALIASES['customer_model'] | {'客户单号'})
    assert recommendations(workbook_bytes(['客户单号'], [['PO1']]))[0]['recommended_field'] is None


def test_manual_mapping_and_formal_parser_do_not_depend_on_aliases(monkeypatch):
    headers = ['PO No.', '客户/型号名称', 'Quantity']
    content = workbook_bytes(headers, [['PO-01', 'CUSTOMER-MAT', 12]])
    columns = recommendations(content)
    assert columns[1]['recommended_field'] is None
    # Reuse the same configuration contract the UI saves; manually choose the
    # unknown header as material code, rather than guessing customer_model.
    mapping = {c['column']: c['recommended_field'] for c in columns if c['recommended_field']}
    mapping['客户/型号名称'] = 'customer_material_code'
    body = management.ConfigurationSave(expected_version=0, customer_name='D 客户', sheet_name='Sheet1',
        header_row=1, field_mapping=mapping, match_fields=['customer_order_no', 'customer_material_code'])
    config = management.build_configuration('D', body, 1)
    monkeypatch.setattr(management, 'ALIASES', {})
    # A changed vocabulary cannot alter a confirmed configuration on later imports.
    for _ in range(2):
        result = parse_excel(config, content, file_name='sample.xlsx')
        assert result.file_error is None and result.success_rows == 1
        assert result.rows[0].data.customer_material_code == 'CUSTOMER-MAT'
        assert result.rows[0].data.customer_model is None
    assert config.customer.field_mapping == mapping
