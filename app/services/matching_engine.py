"""Configuration-driven matching, with conservative product identity checks."""
import logging
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.repositories.customer_configuration import load_configuration, ConfigurationError
from app.repositories.order_matching import candidate_query, latest_candidate, conflicting_product
from app.schemas.matching import MatchResult, HistoricalOrderReference
from app.schemas.purchase_row import NormalizedPurchaseRow

logger = logging.getLogger(__name__)

def match_purchase_row(session: Session, purchase: NormalizedPurchaseRow, *, config=None) -> MatchResult:
    """Use supplied task rules or current rules, without weakening conditions.

    All eligible candidates must identify one product code. If identity conflicts,
    chronology/ID is not proof of correctness: refuse automatic matching. Within a
    consistent product, latest created_at/id determines the reference and price.
    """
    context = dict(customer_id=getattr(purchase, "customer_id", None),
                   sheet_name=getattr(purchase, "sheet_name", None), row_number=getattr(purchase, "row_number", None))
    def failure(status, message, *, fields=None, ids=None):
        return MatchResult(**context, status=status, error_code=status, message=message,
                           error_fields=fields or [], candidate_order_ids=ids or [])
    if not isinstance(purchase, NormalizedPurchaseRow):
        return failure("INVALID_INPUT", "请提供模块 04 的标准化采购行")
    try:
        purchase = NormalizedPurchaseRow.model_validate(purchase.model_dump(warnings=False))
        for field in ("customer_id", "customer_order_no", "customer_material_code", "customer_batch", "customer_model"):
            value = getattr(purchase, field)
            if value is not None and (not value.strip() or value != value.strip()):
                return failure("INVALID_INPUT", "采购编码未完成标准化", fields=[field])
    except ValidationError as exc:
        return failure("INVALID_INPUT", "标准化采购行不合法", fields=[str(e["loc"][0]) for e in exc.errors(include_input=False)])
    try:
        # Caller owns transaction. Prevent unintended writes of pending ORM objects.
        with session.no_autoflush:
            config = load_configuration(session, purchase.customer_id) if config is None else config
            context.update(config_version=config.customer.config_version, rule_version=config.rule.rule_version)
            try:
                config.validate_required_values(purchase.model_dump(warnings=False))
            except ValueError:
                missing = [field for field in config.rule.required_fields if getattr(purchase,field,None) is None]
                return failure("INVALID_INPUT", "缺少匹配或安全校验必填字段", fields=missing)
            query = candidate_query(config, purchase)
            candidate = latest_candidate(session, query)
            if candidate is None:
                return failure("NOT_MATCHED", "同客户、匹配键及安全条件下没有合格历史订单")
            try:
                reference = HistoricalOrderReference.model_validate(candidate)
            except ValidationError as exc:
                return failure("INVALID_REFERENCE", "最新合格候选的产品、价格或核心字段不合法；不回退旧参考",
                               fields=[str(e["loc"][0]) if e["loc"] else "amount" for e in exc.errors(include_input=False)], ids=[candidate["id"]])
            conflict = conflicting_product(session, query, reference.product_code)
            if conflict is not None:
                code = conflict["product_code"]
                ids = [reference.id, conflict["id"]]
                if not isinstance(code,str) or not code.strip() or code != code.strip():
                    return failure("INVALID_REFERENCE", "其他合格候选缺少可信产品编码，无法确认产品一致性",
                                   fields=["product_code"], ids=ids)
                return failure("AMBIGUOUS_MATCH", "同一合格匹配范围出现不同产品编码；时间和 ID 不能消除产品歧义", ids=ids)
            return MatchResult(**context, status="MATCHED", message="历史候选通过产品及价格安全校验；规则的业务可靠性仍需客户确认，价格为历史参考值",
                               matched_order=reference, candidate_order_ids=[reference.id])
    except (ConfigurationError, ValidationError):
        return failure("CONFIG_ERROR", "客户匹配配置缺失、禁用、冲突或不合法")
    except SQLAlchemyError:
        logger.error("Historical matching database query failed")
        return failure("MATCH_ERROR", "历史订单查询异常，请稍后重试")
