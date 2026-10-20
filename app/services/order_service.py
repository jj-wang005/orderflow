"""Pure order construction/validation, no database writes and no MAX-based numbering."""
from decimal import Decimal, localcontext, ROUND_HALF_UP
from uuid import uuid4
from pydantic import ValidationError
from app.models import SalesOrder
from app.schemas.purchase_row import NormalizedPurchaseRow
from app.schemas.matching import HistoricalOrderReference

class OrderValidationError(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)

def generate_order_no() -> str:
    return "AUTO-" + uuid4().hex

def calculate_amount(unit_price: Decimal, quantity: Decimal) -> Decimal:
    if not isinstance(unit_price, Decimal) or not isinstance(quantity, Decimal):
        raise OrderValidationError("INVALID_INPUT", "单价和数量必须为 Decimal")
    if not unit_price.is_finite() or not quantity.is_finite() or unit_price <= 0 or quantity <= 0:
        raise OrderValidationError("INVALID_INPUT", "单价和采购数量必须为有限正数")
    if quantity != quantity.to_integral_value():
        raise OrderValidationError("INVALID_INPUT", "采购数量必须为正整数件数")
    with localcontext() as ctx:
        ctx.prec = 50
        amount = (unit_price * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount >= Decimal("1e18"):
        raise OrderValidationError("AMOUNT_OVERFLOW", "金额超过 DECIMAL(20,2) 容量")
    return amount

def build_sales_order(purchase: NormalizedPurchaseRow, reference: HistoricalOrderReference, task_id: int) -> SalesOrder:
    try:
        purchase = NormalizedPurchaseRow.model_validate(purchase.model_dump(warnings=False))
        reference = HistoricalOrderReference.model_validate(reference.model_dump(warnings=False))
    except ValidationError:
        raise OrderValidationError("INVALID_REFERENCE", "采购行或历史产品价格不合法，禁止建单") from None
    if reference.customer_id != purchase.customer_id or task_id <= 0:
        raise OrderValidationError("INVALID_REFERENCE", "订单来源客户或任务不一致")
    return SalesOrder(
        order_no=generate_order_no(), customer_id=purchase.customer_id,
        customer_order_no=purchase.customer_order_no, customer_material_code=purchase.customer_material_code,
        customer_batch=purchase.customer_batch if purchase.customer_batch is not None else reference.customer_batch,
        customer_model=purchase.customer_model if purchase.customer_model is not None else reference.customer_model,
        product_code=reference.product_code, product_name=reference.product_name, unit_price=reference.unit_price,
        quantity=purchase.quantity, amount=calculate_amount(reference.unit_price, purchase.quantity),
        status="CREATED", source_type="AUTO", source_order_id=reference.id, import_task_id=task_id,
        source_sheet_name=purchase.sheet_name, source_row_number=purchase.row_number,
    )
