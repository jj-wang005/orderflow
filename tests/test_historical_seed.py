import hashlib
import json
import os
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4
import pytest
from pydantic import ValidationError
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.core.database import get_engine
from app.models import CustomerConfig, MatchRule, SalesOrder, ImportTask, ImportTaskRow
from scripts.seed_data import load_seed_orders, seed_orders_in_temporary_test_database as seed_orders, SeedConflictError, MANIFEST_PATH, ROOT
from tests.reference_faults import FAULTS

ORDERS = load_seed_orders()
BY_NO = {o.order_no: o for o in ORDERS}
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

@pytest.mark.parametrize("order", ORDERS, ids=[o.order_no for o in ORDERS])
def test_seed_fields(order):
    values = order.order_values()
    assert order.source_type == "SEED"
    assert order.quantity > 0
    assert isinstance(order.unit_price, Decimal) and isinstance(order.quantity, Decimal)
    assert values["amount"] == (order.unit_price * order.quantity).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    assert values["updated_at"] == order.created_at
    assert values["source_row_number"] is None and values["source_sheet_name"] is None
    if order.customer_id != "C": assert order.customer_batch is None
    if order.customer_id == "C": assert order.customer_order_no is None and order.customer_model is None


def test_real_sample_provenance_and_expectations():
    for filename, expected in MANIFEST["sample_sha256"].items():
        assert hashlib.sha256((ROOT / "sample_files" / filename).read_bytes()).hexdigest() == expected
    rows = MANIFEST["purchase_expectations"]
    assert {cid: sum(r["customer_id"] == cid for r in rows) for cid in ["A","B","C"]} == {"A":9,"B":5,"C":8}
    assert len({(r["customer_id"],r["sheet_name"],r["row_number"]) for r in rows}) == 22
    for row in rows:
        if row["expected_status"] == "MATCHED":
            order = BY_NO[row["expected_order_no"]]
            assert order.customer_id == row["customer_id"]
            assert order.status == "VALID" and order.unit_price > 0
            assert Decimal(row["expected_amount"]) == (order.unit_price * Decimal(row["values"]["quantity"])).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    assert all(any(r["customer_id"] == cid and r["expected_status"] == "MATCHED" for r in rows) for cid in ["A","B","C"])
    assert {r["expected_status"] for r in rows} == {"MATCHED","NOT_MATCHED","AMBIGUOUS_MATCH"}
    assert all(o.unit_price > 0 for o in ORDERS)


def test_specific_boundary_fixtures():
    def get(suffix): return BY_NO["SEED-M03-V1-"+suffix]
    a055,a049=get("A-MATERIAL-055"),get("A-MATERIAL-049")
    assert a055.customer_order_no == a049.customer_order_no
    assert a055.customer_material_code != a049.customer_material_code
    assert a055.product_code != a049.product_code
    first,last=get("A-TIE-FIRST"),get("A-TIE-LAST")
    assert first.created_at == last.created_at and first.product_code == last.product_code
    assert ORDERS.index(first) < ORDERS.index(last)
    b=get("B-NORMAL-NEW")
    assert b.customer_model == "Q55D71B"
    assert get("B-WRONG-MODEL").customer_order_no == b.customer_order_no
    assert get("B-WRONG-MODEL").customer_model != b.customer_model
    assert get("B-NEWEST-INVALID").created_at > b.created_at > get("B-NORMAL-OLD").created_at
    assert get("B-NEWEST-INVALID").status == "INVALID"
    c501,c500=get("C-ELIGIBLE-501"),get("C-NEWER-500")
    assert c501.quantity == 501 and c500.quantity == 500
    assert (c501.customer_batch,c501.customer_material_code)==(c500.customer_batch,c500.customer_material_code)
    assert c500.created_at > c501.created_at
    assert get("C-ELIGIBLE-800").quantity == 800
    assert next(r for r in MANIFEST["purchase_expectations"] if r["customer_id"]=="C" and r["row_number"]==3)["values"]["quantity"] == "12"
    faults={f['order_no']:f for f in FAULTS}
    assert Decimal(faults['SEED-M03-V1-B-NEGATIVE-PRICE']['unit_price']) < 0
    assert Decimal(faults['SEED-M03-V1-A-ZERO-PRICE']['unit_price']) == 0
    assert get("A-CREATED").status == "CREATED"
    assert get("B-CROSS-A-NUMBER").customer_order_no == get("A-NORMAL-OLD").customer_order_no
    for cid in ["A","C"]:
        one,two=get(cid+"-CONFLICT-ONE"),get(cid+"-CONFLICT-TWO")
        assert one.created_at==two.created_at and one.product_code != two.product_code

@pytest.mark.parametrize("changes", [
 {"source_type":"AUTO"},{"quantity":"0"},{"order_no":"REAL-ORDER"},
 {"unit_price":"NaN"},{"unit_price":"1.12345"},{"product_code":""}, {"unit_price":"0"},{"unit_price":"-1"},
])
def test_invalid_fixture_rejected(changes):
    with pytest.raises(ValidationError):
        type(ORDERS[0]).model_validate({**ORDERS[0].model_dump(),**changes})

@pytest.fixture
def db_session():
    from tests.shared_fixtures import require_database_tests
    from tests.db_isolation import temporary_schema_engine
    from app.repositories.customer_configuration import initialize_configurations
    from app.services.customer_configuration import read_configuration_file
    require_database_tests()
    with temporary_schema_engine() as engine:
        with Session(engine) as db, db.begin():
            initialize_configurations(db, read_configuration_file())
        with engine.connect() as conn:
            outer = conn.begin()
            try:
                with Session(bind=conn, join_transaction_mode='create_savepoint') as db: yield db
            finally: outer.rollback()


def snapshot(session, model):
    return [tuple(getattr(row,c.name) for c in model.__table__.columns) for row in session.scalars(select(model).order_by(model.id))]

def test_database_seed_idempotence_and_sort(db_session):
    session=db_session
    others={m: snapshot(session,m) for m in [CustomerConfig,MatchRule,ImportTask,ImportTaskRow]}
    original={o.order_no: tuple(getattr(o,c.name) for c in SalesOrder.__table__.c) for o in session.scalars(select(SalesOrder))}
    # Arbitrary pre-existing AUTO and unrelated SEED fixtures must remain untouched.
    unrelated=[]
    for source,status in [("AUTO","VALID"),("AUTO","CREATED"),("SEED","VALID")]:
        data=ORDERS[0].order_values();data.update(order_no="TEST-M03-"+uuid4().hex,source_type=source,status=status,
                                                        created_at=ORDERS[0].created_at + timedelta(days=31),
                                                        updated_at=ORDERS[0].created_at + timedelta(days=31))
        obj=SalesOrder(**data);session.add(obj);unrelated.append(obj)
    session.flush()
    preserved=[tuple(getattr(o,c.name) for c in SalesOrder.__table__.c) for o in unrelated]
    first=seed_orders(session,ORDERS)
    second=seed_orders(session,ORDERS)
    assert first["inserted"] == len(ORDERS)-len(set(original)&set(BY_NO))
    assert second == {"inserted":0,"unchanged":len(ORDERS),"managed_total":len(ORDERS)}
    for m,expected in others.items(): assert snapshot(session,m)==expected
    assert [tuple(getattr(o,c.name) for c in SalesOrder.__table__.c) for o in unrelated] == preserved
    for order in session.scalars(select(SalesOrder).where(SalesOrder.order_no.in_(original))):
        assert tuple(getattr(order,c.name) for c in SalesOrder.__table__.c) == original[order.order_no]
    rows=session.scalars(select(SalesOrder).where(SalesOrder.order_no.in_(BY_NO))).all()
    assert len(rows)==len(ORDERS) and {o.customer_id for o in rows}=={"A","B","C"}
    by_no={o.order_no:o for o in rows}
    for no,spec in BY_NO.items():
        assert all(getattr(by_no[no],field)==value for field,value in spec.order_values().items())
    assert by_no['SEED-M03-V1-A-TIE-LAST'].id > by_no['SEED-M03-V1-A-TIE-FIRST'].id
    # Static eligibility probes for fixture boundaries, not a general rule engine.
    auto_ids=[o.id for o in unrelated if o.source_type=="AUTO"]
    assert session.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.id.in_(auto_ids),SalesOrder.status=='VALID',SalesOrder.source_type!='AUTO'))==0
    c_ids=[by_no['SEED-M03-V1-C-ELIGIBLE-501'].id,by_no['SEED-M03-V1-C-NEWER-500'].id]
    eligible=session.scalars(select(SalesOrder).where(SalesOrder.id.in_(c_ids),SalesOrder.quantity>500)).all()
    assert [o.order_no for o in eligible]==['SEED-M03-V1-C-ELIGIBLE-501']

@pytest.mark.parametrize("collision", ["AUTO","drift"])
def test_collision_rejected_without_overwrite(db_session,collision):
    session=db_session
    seed_orders(session,ORDERS)
    row=session.scalar(select(SalesOrder).where(SalesOrder.order_no==ORDERS[0].order_no))
    if collision=='AUTO': row.source_type='AUTO'
    else: row.product_code='SIM-EXTERNAL-CHANGE'
    session.flush(); before=snapshot(session,SalesOrder)
    with pytest.raises(SeedConflictError): seed_orders(session,ORDERS)
    assert snapshot(session,SalesOrder)==before

def test_failed_transaction_rolls_back_insert(db_session):
    session=db_session
    before=snapshot(session,SalesOrder)
    new=ORDERS[0].model_copy(update={"order_no":"SEED-M03-V1-A-ROLLBACK-"+uuid4().hex[:8]})
    with pytest.raises(RuntimeError):
        with session.begin_nested():
            seed_orders(session,[new])
            raise RuntimeError("simulate failure after insertion")
    assert snapshot(session,SalesOrder)==before


def test_formal_seed_entry_still_rejects_temporary_database(db_session):
    from scripts.seed_data import seed_orders as formal_seed
    before = snapshot(db_session, SalesOrder)
    with pytest.raises(SeedConflictError, match='Seed target must be order_automation'):
        formal_seed(db_session, ORDERS)
    assert snapshot(db_session, SalesOrder) == before
