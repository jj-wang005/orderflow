import os
import posixpath
import zipfile
import xml.etree.ElementTree as ET
import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, select, func
from sqlalchemy.orm import Session
from app.models import CustomerConfig, MatchRule, SalesOrder
from app.core.database import get_engine
from app.schemas.customer_configuration import CustomerConfiguration
from app.services.customer_configuration import read_configuration_file, ROOT
from app.repositories.customer_configuration import load_configuration, initialize_configurations, ConfigurationError

EXPECTED = {
 "A": ({"采购订单号":"customer_order_no", "物料/品项编码":"customer_material_code", "订单数量":"quantity"}, ["customer_order_no"], ["customer_order_no","quantity","customer_material_code"]),
 "B": ({"单号":"customer_order_no", "机型":"customer_model", "物料编码":"customer_material_code", "数量":"quantity"}, ["customer_order_no","customer_model"], ["customer_order_no","customer_model","quantity"]),
 "C": ({"客批号":"customer_batch", "料号":"customer_material_code", "数量":"quantity"}, ["customer_batch","customer_material_code"], ["customer_batch","customer_material_code","quantity"]),
}

@pytest.mark.parametrize("config", read_configuration_file(), ids=["A","B","C"])
def test_configuration_and_required_fields(config):
    mapping, match_fields, required = EXPECTED[config.customer.customer_id]
    assert config.customer.field_mapping == mapping
    assert config.rule.match_fields == match_fields
    assert config.rule.required_fields == required
    assert config.rule.filter_conditions.customer_scope == "same_customer"
    assert config.rule.filter_conditions.eligible_statuses == ['VALID','PROCESSING','SHIPPED','COMPLETED']
    assert config.rule.filter_conditions.allowed_source_types == ['SEED','AUTO']
    assert [(s.field,s.direction) for s in config.rule.sort_config] == [("created_at","DESC"),("id","DESC")]
    values = {field: "test" for field in required}
    values["quantity"] = "12"
    for field in ["customer_order_no","customer_material_code","customer_batch","customer_model"]:
        if field not in required: values[field] = None
    config.validate_required_values(values)
    for field in required:
        with pytest.raises(ValueError): config.validate_required_values({**values,field:None})
    if config.customer.customer_id == "C":
        assert config.rule.filter_conditions.historical_quantity.operator == "gt"
        assert config.rule.filter_conditions.historical_quantity.value == 500
    else:
        assert config.rule.filter_conditions.historical_quantity is None

@pytest.mark.parametrize("config", read_configuration_file(), ids=["A","B","C"])
def test_real_template_headers(config):
    # Inspect metadata/header only, never iterate or import purchase rows.
    ns = {"s":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    path = next((ROOT / "sample_files").glob(config.customer.customer_id + "*.xlsx"))
    with zipfile.ZipFile(path) as z:
        sheets = ET.fromstring(z.read("xl/workbook.xml")).findall("s:sheets/s:sheet",ns)
        sheet = next(s for s in sheets if s.get("name") == config.customer.sheet_name)
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = next(r.get("Target") for r in rels if r.get("Id") == sheet.get("{"+rns+"}id"))
        target = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/"+target)
        strings = [''.join(n.itertext()) for n in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("s:si",ns)] if "xl/sharedStrings.xml" in z.namelist() else []
        row = ET.fromstring(z.read(target)).find(f"s:sheetData/s:row[@r='{config.customer.header_row}']",ns)
        assert row is not None
        headers = []
        for c in row:
            v = c.find("s:v",ns); inline = c.find("s:is",ns)
            value = v.text if v is not None else ''.join(inline.itertext()) if inline is not None else ""
            headers.append(strings[int(value)] if c.get("t") == "s" else value)
        assert set(config.customer.field_mapping) <= set(headers)
        assert len(headers) == len(set(headers))

@pytest.mark.parametrize("mutation", [
 lambda c: c["customer"]["field_mapping"].update({"bad":"unknown_field"}),
 lambda c: c["customer"].update(header_row=0),
 lambda c: c["customer"]["field_mapping"].pop("物料/品项编码"),
 lambda c: c["rule"].update(match_fields=[]),
 lambda c: c["rule"].update(match_fields=["customer_order_no","customer_order_no"]),
 lambda c: c["rule"]["filter_conditions"].update(status="CREATED"),
 lambda c: c["rule"]["filter_conditions"].update(excluded_source_types=[]),
 lambda c: c["rule"]["filter_conditions"].update(customer_scope="all"),
 lambda c: c["rule"]["filter_conditions"].update(sql="1=1"),
 lambda c: c["rule"]["filter_conditions"].update(historical_quantity={"operator":"lt","value":"500"}),
 lambda c: c["rule"].update(sort_config=[{"field":"id","direction":"ASC"}]),
 lambda c: c["rule"]["validation_config"].update(extra_required_fields=["customer_order_no"]),
])
def test_invalid_configuration_rejected(mutation):
    data = read_configuration_file()[0].model_dump(mode="json")
    mutation(data)
    with pytest.raises(ValidationError): CustomerConfiguration.model_validate(data)

@pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="Local MySQL test requires RUN_DB_TESTS=1")
def test_persisted_configuration_idempotence_and_nullability():
    engine = get_engine()
    columns = {c["name"]: c for c in inspect(engine).get_columns("sales_order")}
    for field in ["customer_order_no","customer_material_code","customer_batch","customer_model"]:
        assert columns[field]["nullable"]
    for field in ["product_code","product_name","unit_price","quantity","amount"]:
        assert not columns[field]["nullable"]
    # Real transaction, rollback any initialization performed by this test.
    with engine.connect() as connection:
        outer = connection.begin()
        try:
            with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                before = session.scalar(select(func.count()).select_from(SalesOrder))
                configs = read_configuration_file()
                initialize_configurations(session, configs)
                ids = [(session.scalar(select(CustomerConfig.id).where(CustomerConfig.customer_id==c.customer.customer_id)), session.scalar(select(MatchRule.id).where(MatchRule.customer_id==c.customer.customer_id, MatchRule.enabled.is_(True)))) for c in configs]
                initialize_configurations(session, configs)
                for config, expected_ids in zip(configs, ids):
                    loaded = load_configuration(session, config.customer.customer_id)
                    assert loaded.model_dump(exclude={'source_columns'}) == config.model_dump(exclude={'source_columns'})
                    assert loaded.source_columns  # trusted original template recovery is metadata only
                    assert session.scalar(select(func.count()).select_from(CustomerConfig).where(CustomerConfig.customer_id==config.customer.customer_id)) == 1
                    assert session.scalar(select(func.count()).select_from(MatchRule).where(MatchRule.customer_id==config.customer.customer_id,MatchRule.enabled.is_(True))) == 1
                    assert session.scalar(select(MatchRule.id).where(MatchRule.customer_id==config.customer.customer_id,MatchRule.enabled.is_(True))) == expected_ids[1]
                assert session.scalar(select(func.count()).select_from(SalesOrder)) == before
                with pytest.raises(ConfigurationError): load_configuration(session, "UNKNOWN")
                changed = configs[0].model_copy(deep=True)
                changed.customer.customer_name = "changed"
                with pytest.raises(ConfigurationError): initialize_configurations(session, [changed])
                # Corrupt stored JSON must fail at load time.
                rule = session.scalar(select(MatchRule).where(MatchRule.customer_id=="A", MatchRule.enabled.is_(True)))
                rule.match_fields = ["unknown"]
                session.flush()
                with pytest.raises(ValidationError): load_configuration(session,"A")
        finally:
            outer.rollback()

@pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="Local MySQL test requires RUN_DB_TESTS=1")
def test_configuration_versions_and_ambiguity():
    with get_engine().connect() as connection:
        outer = connection.begin()
        try:
            with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                configs = read_configuration_file()
                initialize_configurations(session, configs)
                newer = configs[0].model_copy(deep=True)
                newer.customer.customer_name = "Version 2"
                newer.customer.config_version = 2
                newer.rule.rule_version = 2
                initialize_configurations(session, [newer])
                assert load_configuration(session, "A").model_dump(exclude={'source_columns'}) == newer.model_dump(exclude={'source_columns'})
                rules = session.scalars(select(MatchRule).where(MatchRule.customer_id=="A")).all()
                assert sorted((r.rule_version,r.enabled) for r in rules) == [(1,False),(2,True)]
                with pytest.raises(ConfigurationError): initialize_configurations(session, [configs[0]])
                changed = newer.model_copy(deep=True)
                changed.rule.validation_config.consistency_fields = []
                with pytest.raises(ConfigurationError): initialize_configurations(session, [changed])
                with pytest.raises(ConfigurationError): initialize_configurations(session, [newer,newer])
                invalid = newer.model_copy(deep=True)
                invalid.rule.match_fields = ["unknown"]
                with pytest.raises(ValidationError): initialize_configurations(session, [invalid])
                session.add(MatchRule(customer_id="A", **newer.rule.model_dump(mode="json")))
                session.flush()
                with pytest.raises(ConfigurationError): load_configuration(session,"A")
                with pytest.raises(ConfigurationError): initialize_configurations(session,[newer])
        finally:
            outer.rollback()


# This module owns a fresh legacy baseline; no managed database is queried.
pytestmark = pytest.mark.usefixtures("legacy_runtime")
