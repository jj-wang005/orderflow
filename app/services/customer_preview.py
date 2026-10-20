"""Transient draft previews; no configuration, import, order or audit writes."""
from datetime import datetime, timezone

from app.api.errors import APIError
from app.models import SalesOrder
from app.repositories.order_matching import candidate_query
from app.schemas.customer_management import ConfigurationSave
from app.schemas.matching import MatchResult
from app.services import customer_management as management
from app.services.excel_parser import parse_excel
from app.services.matching_engine import match_purchase_row

TRIAL_ROW_LIMIT = 100


def parse_draft(session, customer_id, configuration, content, filename):
    # Also protect the customer existence query against accidental ORM autoflush.
    with session.no_autoflush:
        management.customer_record(session, customer_id)
        try:
            body = ConfigurationSave.model_validate_json(configuration)
        except ValueError:
            raise APIError(422, "INVALID_CONFIGURATION", "预览配置格式不合法，请核对字段映射和匹配规则") from None
        config = management.build_configuration(customer_id, body, body.expected_version + 1)
        # A paused/new customer's draft can be inspected without enabling it in DB.
        config.customer.enabled = True
        parsed = parse_excel(config, content, file_name=filename)
    return config, parsed


def trial_match(session, config, parsed):
    """Use the production engine, without purchase guard or order construction.

    Bound both engine calls and returned diagnostics. Summary counts cover only
    evaluated rows; total_rows still reports the real number of parsed rows.
    """
    checked_at = datetime.now(timezone.utc)
    rows = []
    summary = dict(matched_rows=0, unmatched_rows=0, ambiguous_rows=0, other_rows=0)
    counters = {"MATCHED": "matched_rows", "NOT_MATCHED": "unmatched_rows", "AMBIGUOUS_MATCH": "ambiguous_rows"}
    with session.no_autoflush:
        for row in parsed.rows[:TRIAL_ROW_LIMIT]:
            values = (row.data.model_dump(mode="json") if row.data else
                      {field: row.raw_data.get(column) for column, field in config.customer.field_mapping.items()})
            if row.data is None:
                result = MatchResult(
                    customer_id=config.customer.customer_id, sheet_name=row.sheet_name, row_number=row.row_number,
                    status="INVALID_INPUT", error_code="INVALID_INPUT",
                    message="；".join(issue.message for issue in row.errors),
                    error_fields=list(dict.fromkeys(f for issue in row.errors for f in issue.fields)))
            else:
                result = match_purchase_row(session, row.data, config=config)
            # At most two diagnostic candidates selected by the engine. They are
            # never usable references and do not affect its matching decision.
            candidates = []
            if result.status == "AMBIGUOUS_MATCH":
                query = candidate_query(config, row.data)
                query = query.where(SalesOrder.id.in_(result.candidate_order_ids)).limit(2)
                candidates = [dict(candidate) for candidate in session.execute(query).mappings()]
            rows.append({"row_number": row.row_number, "sheet_name": row.sheet_name,
                         "match_values": {f: values.get(f) for f in config.rule.match_fields},
                         "result": result, "candidates": candidates})
            summary[counters.get(result.status, "other_rows")] += 1
    return {"checked_at": checked_at, "file_error": parsed.file_error,
            "total_rows": len(parsed.rows), "evaluated_rows": len(rows), "row_limit": TRIAL_ROW_LIMIT,
            "truncated": len(parsed.rows) > len(rows), "summary": summary, "rows": rows}
