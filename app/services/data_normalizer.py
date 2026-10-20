"""Pure mapping/validation; no customer-specific branches, database or matching."""
import math
import re
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pydantic import ValidationError
from app.schemas.customer_configuration import CustomerConfiguration
from app.schemas.purchase_row import RawPurchaseRow, NormalizedPurchaseRow, RowParseResult, ParseIssue

CODE_FIELDS = ("customer_order_no", "customer_material_code", "customer_batch", "customer_model")
NUMBER = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")

def is_blank(value) -> bool:
    return value is None or isinstance(value, str) and not value.strip()

def normalize_code(value, number_format: str = "General") -> str | None:
    if is_blank(value):
        return None
    if isinstance(value, str):
        return value.strip()  # Preserve text zeros, case and meaningful punctuation.
    if isinstance(value, bool) or isinstance(value, (date, datetime, time)):
        raise ValueError("编码必须是文本或安全整数，不能是布尔值或日期")
    if isinstance(value, int):
        if len(str(abs(value))) > 15:
            raise ValueError("数字编码超过 Excel 15 位可靠精度，请核对原始文本")
        if re.search(r"[eE][+-]", number_format) or re.search(r"(?<!0)0{2,}", number_format):
            raise ValueError("数字编码带科学计数或补零格式，不能猜测原始编码")
        return str(value)
    raise ValueError("编码不是安全整数；浮点编码需客户核对后以文本提供")

def normalize_quantity(value) -> Decimal:
    if is_blank(value):
        raise ValueError("采购数量不能为空")
    if isinstance(value, bool):
        raise ValueError("采购数量不能是布尔值")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("采购数量必须是有限数字")
    if not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("采购数量不是数字")
    text = str(value).strip()
    if not NUMBER.fullmatch(text) or len(text) > 128:
        raise ValueError("采购数量格式不合法")
    try:
        quantity = Decimal(text)  # Never Decimal(float) or binary float arithmetic.
    except InvalidOperation:
        raise ValueError("采购数量格式不合法") from None
    if not quantity.is_finite() or quantity <= 0:
        raise ValueError("采购数量必须大于 0")
    if quantity != quantity.to_integral_value():
        raise ValueError("采购数量必须为正整数件数")
    return quantity

def normalize_row(config: CustomerConfiguration, raw: RawPurchaseRow) -> RowParseResult:
    fields = {key: None for key in CODE_FIELDS}
    errors = []
    mapped = {}
    for original, target in config.customer.field_mapping.items():
        value = raw.values.get(original)
        if original in raw.cell_errors:
            errors.append(ParseIssue(code="INVALID_INPUT", fields=[target], message=raw.cell_errors[original]))
            mapped[target] = None
            continue
        try:
            mapped[target] = normalize_quantity(value) if target == "quantity" else normalize_code(value, raw.raw_cells.get(original, {}).get("number_format", "General"))
        except ValueError as exc:
            errors.append(ParseIssue(code="INVALID_INPUT", fields=[target], message=str(exc)))
            mapped[target] = None
    for original, message in raw.cell_errors.items():
        if original not in config.customer.field_mapping:
            errors.append(ParseIssue(code="INVALID_INPUT", fields=[original], message=message))
    # Reuse module 02 requiredness; never apply historical_quantity to this purchase.
    try:
        config.validate_required_values(mapped)
    except ValueError:
        already = {field for error in errors for field in error.fields}
        missing = [field for field in config.rule.required_fields if is_blank(mapped.get(field)) and field not in already]
        if missing:
            errors.append(ParseIssue(code="INVALID_INPUT", fields=missing, message="缺少必填值：" + ", ".join(missing)))
    fields.update(mapped)
    data = None
    if not errors:
        try:
            data = NormalizedPurchaseRow(customer_id=config.customer.customer_id, **fields,
                                         raw_data=raw.raw_data, raw_cells=raw.raw_cells,
                                         sheet_name=raw.sheet_name, row_number=raw.row_number)
        except ValidationError as exc:
            for issue in exc.errors(include_input=False):
                errors.append(ParseIssue(code="INVALID_INPUT", fields=[str(issue["loc"][0])], message="字段超出支持的长度或数量精度：" + str(issue["loc"][0])))
    return RowParseResult(status="SUCCESS" if data is not None else "INVALID_INPUT",
                          sheet_name=raw.sheet_name, row_number=raw.row_number,
                          raw_data=raw.raw_data, raw_cells=raw.raw_cells, data=data, errors=errors)
